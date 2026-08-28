from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from aura_music_studio.account_security_api import AccountSecurityService
from aura_music_studio.accounts import AccountStore, _hash_secret
from aura_music_studio.api import app


def _route_count(path: str, method: str) -> int:
    wanted = method.upper()
    return sum(
        1
        for route in app.routes
        if getattr(route, "path", None) == path
        and wanted in set(getattr(route, "methods", set()) or set())
    )


def test_account_security_routes_have_one_authoritative_handler():
    assert _route_count("/auth/password-reset/request", "POST") == 1
    assert _route_count("/auth/password-reset/confirm", "POST") == 1
    assert _route_count("/auth/sessions", "GET") == 1
    assert _route_count("/auth/sessions/revoke-others", "POST") == 1
    assert _route_count("/auth/sessions/{session_id}", "DELETE") == 1

    shim = Path("aura_music_studio/account_recovery.py").read_text(encoding="utf-8")
    assert "@router." not in shim
    assert "AccountSecurityService" not in shim


def test_canonical_reset_token_is_hash_stored_single_use_and_revokes_sessions(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    service = AccountSecurityService(accounts)
    signup = accounts.signup(
        "recovery@example.com",
        "Recovery Member",
        "original-password",
        "free",
    )
    session_a = accounts.create_session(signup.user_id)
    session_b = accounts.create_session(signup.user_id)

    issued = service.create_password_reset("recovery@example.com")
    assert issued["issued"] is True
    raw_token = str(issued["token"])

    with sqlite3.connect(accounts.db_path) as con:
        stored = con.execute(
            "SELECT token_hash,used_at FROM password_reset_tokens WHERE user_id=?",
            (signup.user_id,),
        ).fetchone()
    assert stored is not None
    assert stored[0] == _hash_secret(raw_token)
    assert stored[0] != raw_token
    assert stored[1] is None

    result = service.complete_password_reset(raw_token, "replacement-password")
    assert result["reset"] is True
    assert result["sessions_revoked"] is True
    assert accounts.resolve_session(session_a) is None
    assert accounts.resolve_session(session_b) is None
    assert accounts.authenticate("recovery@example.com", "replacement-password") is not None
    assert accounts.authenticate("recovery@example.com", "original-password") is None

    try:
        service.complete_password_reset(raw_token, "third-password")
    except ValueError:
        pass
    else:
        raise AssertionError("Consumed reset token must not be reusable")


def test_canonical_session_inventory_is_account_scoped_and_mfa_compatible(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    service = AccountSecurityService(accounts)
    first = accounts.signup("first@example.com", "First Member", "first-password", "free")
    second = accounts.signup("second@example.com", "Second Member", "second-password", "free")

    current = accounts.create_session(first.user_id)
    other = accounts.create_session(first.user_id)
    foreign = accounts.create_session(second.user_id)

    sessions = service.list_sessions(current)
    assert len(sessions) == 2
    assert sum(1 for item in sessions if item["current"]) == 1
    assert all("active" in item for item in sessions)
    assert all("token_hash" not in item for item in sessions)

    foreign_id = service.list_sessions(foreign)[0]["id"]
    try:
        service.revoke_session_id(current, foreign_id)
    except ValueError:
        pass
    else:
        raise AssertionError("A member must not revoke another member's session")

    assert service.revoke_other_sessions(current) == 1
    assert accounts.resolve_session(current) is not None
    assert accounts.resolve_session(other) is None
    assert accounts.resolve_session(foreign) is not None


def test_reset_delivery_uses_fragment_and_public_response_does_not_expose_secret():
    source = Path("aura_music_studio/account_security_api.py").read_text(encoding="utf-8")
    assert "/auth/reset-password#token=" in source
    assert '"token": token' in source

    public_route_section = source.split('@router.post("/auth/password-reset/request")', 1)[1]
    public_route_section = public_route_section.split('@router.post("/auth/password-reset/confirm")', 1)[0]
    assert '"accepted": True' in public_route_section
    assert '"token":' not in public_route_section


def test_consolidation_branch_is_blocked_from_vercel():
    vercel = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    assert vercel["git"]["deploymentEnabled"]["feature/core-account-security-consolidation"] is False
