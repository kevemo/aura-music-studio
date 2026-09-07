import base64
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio import owner_auth, owner_mfa
from aura_music_studio.owner_auth import sessions
from aura_music_studio.owner_auth_portal import router
from aura_music_studio.owner_identity import decode_persona_cookie
from aura_music_studio.owner_mfa import (
    OWNER_MFA_CHALLENGE_COOKIE,
    OWNER_MFA_MAX_ATTEMPTS,
    OwnerMFAService,
    _current_counter,
    _totp_code,
)


def _b32(raw: bytes) -> str:
    return base64.b32encode(raw).decode("ascii").rstrip("=")


MARY_SECRET = _b32(b"mary-owner-mfa-secret-2026")
KEV_SECRET = _b32(b"kev-owner-mfa-secret--2026")
FIXED_TIME = 1_800_000_015.0


def _configure(monkeypatch, tmp_path, *, required=True, mary=True, kev=True):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    monkeypatch.setenv("LSS_ADMIN_KEY", "unit-test-owner-key")
    monkeypatch.setenv("LSS_COOKIE_SECURE", "false")
    monkeypatch.setenv("LSS_OWNER_MFA_REQUIRED", "true" if required else "false")
    if mary:
        monkeypatch.setenv("LSS_OWNER_MARY_TOTP_SECRET", MARY_SECRET)
    else:
        monkeypatch.delenv("LSS_OWNER_MARY_TOTP_SECRET", raising=False)
    if kev:
        monkeypatch.setenv("LSS_OWNER_KEV_TOTP_SECRET", KEV_SECRET)
    else:
        monkeypatch.delenv("LSS_OWNER_KEV_TOTP_SECRET", raising=False)
    monkeypatch.setattr(owner_mfa.time, "time", lambda: FIXED_TIME)
    owner_auth._sessions = None
    owner_mfa._service = None


def _code(secret: str) -> str:
    return _totp_code(secret, _current_counter(FIXED_TIME))


def test_challenge_and_totp_secrets_are_never_stored_plaintext(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    service = OwnerMFAService(tmp_path / "accounts.sqlite3")
    raw_challenge = service.create_challenge("mary", purpose="login")

    with sqlite3.connect(service.db_path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM owner_mfa_challenges").fetchone()
    assert row is not None
    persisted = " ".join(str(row[key]) for key in row.keys())
    assert raw_challenge not in persisted
    assert MARY_SECRET not in persisted
    assert KEV_SECRET not in persisted
    assert len(str(row["token_hash"])) == 64


def test_valid_totp_consumes_challenge_and_same_counter_cannot_replay(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    service = OwnerMFAService(tmp_path / "accounts.sqlite3")
    first = service.create_challenge("mary", purpose="login")
    code = _code(MARY_SECRET)

    assert service.verify_challenge(first, code, expected_purpose="login") == "mary"
    assert service.challenge(first) is None

    second = service.create_challenge("mary", purpose="login")
    with pytest.raises(ValueError, match="already been used"):
        service.verify_challenge(second, code, expected_purpose="login")


def test_five_bad_codes_exhaust_challenge(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    service = OwnerMFAService(tmp_path / "accounts.sqlite3")
    token = service.create_challenge("kev", purpose="login")

    for _ in range(OWNER_MFA_MAX_ATTEMPTS):
        with pytest.raises(ValueError, match="Incorrect owner verification code"):
            service.verify_challenge(token, "000000", expected_purpose="login")
    assert service.challenge(token) is None
    with pytest.raises(ValueError, match="missing or expired|exhausted"):
        service.verify_challenge(token, _code(KEV_SECRET), expected_purpose="login")


def test_challenge_is_bound_to_persona_and_purpose(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    service = OwnerMFAService(tmp_path / "accounts.sqlite3")
    token = service.create_challenge("kev", purpose="switch")
    code = _code(KEV_SECRET)

    with pytest.raises(ValueError, match="does not match this action"):
        service.verify_challenge(token, code, expected_purpose="login")
    with pytest.raises(ValueError, match="does not match this action"):
        service.verify_challenge(
            token,
            code,
            expected_purpose="switch",
            expected_persona="mary",
        )
    assert service.verify_challenge(
        token,
        code,
        expected_purpose="switch",
        expected_persona="kev",
    ) == "kev"


def test_required_mfa_fails_closed_if_either_owner_secret_is_missing(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path, mary=True, kev=False)
    service = OwnerMFAService(tmp_path / "accounts.sqlite3")
    assert service.configuration_status()["required"] is True
    assert service.configuration_status()["configured"] is False
    with pytest.raises(RuntimeError, match="required but not fully configured"):
        service.create_challenge("mary", purpose="login")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, follow_redirects=False)


def test_mfa_required_login_creates_no_owner_session_until_totp_succeeds(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    client = _client()

    first = client.post(
        "/owner/login",
        data={"admin_key": "unit-test-owner-key", "persona": "mary"},
    )
    assert first.status_code == 200
    assert "Verify Mary" in first.text
    challenge = client.cookies.get(OWNER_MFA_CHALLENGE_COOKIE)
    assert challenge
    assert client.cookies.get("lss_admin_session") is None
    # OwnerSessionStore is intentionally lazy. Before a successful second factor the
    # owner_sessions table may not exist at all; if it does, it must still be empty.
    with sqlite3.connect(tmp_path / "accounts.sqlite3") as con:
        table = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='owner_sessions'"
        ).fetchone()
        if table:
            assert con.execute("SELECT COUNT(*) FROM owner_sessions").fetchone()[0] == 0

    verified = client.post(
        "/owner/login",
        data={"persona": "mary", "totp_code": _code(MARY_SECRET)},
    )
    assert verified.status_code == 303
    assert verified.headers["location"] == "/owner/dashboard"
    owner_token = client.cookies.get("lss_admin_session")
    assert owner_token
    assert sessions().valid(owner_token, touch=False) is True
    assert decode_persona_cookie(client.cookies.get("pfh_owner_persona")) == "mary"
    assert client.cookies.get(OWNER_MFA_CHALLENGE_COOKIE) is None


def test_mfa_disabled_preserves_legacy_one_step_owner_login(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path, required=False, mary=False, kev=False)
    client = _client()
    response = client.post("/owner/login", data={"admin_key": "unit-test-owner-key"})
    assert response.status_code == 303
    assert client.cookies.get("lss_admin_session")
    assert client.cookies.get(OWNER_MFA_CHALLENGE_COOKIE) is None


def test_switching_mary_to_kev_requires_kevs_totp_and_preserves_current_identity_until_success(
    tmp_path, monkeypatch
):
    _configure(monkeypatch, tmp_path)
    client = _client()

    client.post(
        "/owner/login",
        data={"admin_key": "unit-test-owner-key", "persona": "mary"},
    )
    client.post(
        "/owner/login",
        data={"persona": "mary", "totp_code": _code(MARY_SECRET)},
    )
    assert decode_persona_cookie(client.cookies.get("pfh_owner_persona")) == "mary"
    owner_token = client.cookies.get("lss_admin_session")

    prompt = client.post("/owner/persona/kev")
    assert prompt.status_code == 200
    assert "Verify Kev" in prompt.text
    assert decode_persona_cookie(client.cookies.get("pfh_owner_persona")) == "mary"
    assert client.cookies.get("lss_admin_session") == owner_token

    wrong = client.post(
        "/owner/persona/kev",
        data={"totp_code": "000000"},
    )
    assert wrong.status_code == 200
    assert decode_persona_cookie(client.cookies.get("pfh_owner_persona")) == "mary"

    switched = client.post(
        "/owner/persona/kev",
        data={"totp_code": _code(KEV_SECRET)},
    )
    assert switched.status_code == 303
    assert decode_persona_cookie(client.cookies.get("pfh_owner_persona")) == "kev"
    assert client.cookies.get("lss_admin_session") == owner_token


def test_secure_owner_persona_route_precedes_legacy_switch_route_in_production():
    source = Path("app.py").read_text(encoding="utf-8")
    assert source.index("app.include_router(owner_auth_router)") < source.index(
        "app.include_router(owner_control_center_router)"
    )
