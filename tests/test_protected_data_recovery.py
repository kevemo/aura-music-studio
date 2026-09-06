from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from starlette.requests import Request

from aura_music_studio.backup_scheduler import BackupScheduler
from aura_music_studio.owner_auth import OWNER_COOKIE, OwnerSessionStore
from aura_music_studio.owner_identity import OWNER_PERSONA_COOKIE, encode_persona_cookie
from aura_music_studio.protected_data_auth import (
    PROTECTED_DATA_COOKIE,
    ProtectedDataSessionStore,
    protected_data_authorized,
    protected_key_matches,
)


def _request_with_cookies(cookies: dict[str, str]) -> Request:
    header = "; ".join(f"{key}={value}" for key, value in cookies.items()).encode("utf-8")
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/owner/backups",
            "raw_path": b"/owner/backups",
            "query_string": b"",
            "headers": [(b"cookie", header)],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
        }
    )


def test_protected_session_token_is_hashed_audited_and_revocable(tmp_path):
    db = tmp_path / "protected.sqlite3"
    store = ProtectedDataSessionStore(db)
    token = store.create("kev")

    assert token
    assert store.resolve(token) == "kev"

    with sqlite3.connect(db) as con:
        session_row = con.execute(
            "SELECT token_hash,persona FROM protected_data_sessions"
        ).fetchone()
        audit_row = con.execute(
            "SELECT event_type,persona FROM protected_data_audit_events ORDER BY created_at LIMIT 1"
        ).fetchone()

    assert session_row is not None
    assert session_row[0] == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert session_row[0] != token
    assert session_row[1] == "kev"
    assert audit_row == ("step_up_accepted", "kev")

    store.revoke(token)
    assert store.resolve(token) is None


def test_protected_credentials_have_no_default_and_are_persona_specific(monkeypatch):
    monkeypatch.delenv("SHARED_SKIES_PROTECTED_KEV_KEY", raising=False)
    monkeypatch.delenv("SHARED_SKIES_PROTECTED_MARY_KEY", raising=False)
    assert protected_key_matches("kev", "anything") is False
    assert protected_key_matches("mary", "anything") is False

    monkeypatch.setenv("SHARED_SKIES_PROTECTED_KEV_KEY", "kev-protected-secret")
    monkeypatch.setenv("SHARED_SKIES_PROTECTED_MARY_KEY", "mary-protected-secret")
    assert protected_key_matches("kev", "kev-protected-secret") is True
    assert protected_key_matches("kev", "mary-protected-secret") is False
    assert protected_key_matches("mary", "mary-protected-secret") is True
    assert protected_key_matches("mary", "kev-protected-secret") is False


def test_owner_session_alone_never_authorizes_protected_data(monkeypatch, tmp_path):
    db = tmp_path / "studio.sqlite3"
    monkeypatch.setenv("LSS_DB_PATH", str(db))
    monkeypatch.setenv("LSS_ADMIN_KEY", "owner-cookie-signing-key")

    owner_token = OwnerSessionStore(db).create()
    protected_token = ProtectedDataSessionStore(db).create("kev")
    kev_persona = encode_persona_cookie("kev")
    mary_persona = encode_persona_cookie("mary")

    owner_only = _request_with_cookies(
        {OWNER_COOKIE: owner_token, OWNER_PERSONA_COOKIE: kev_persona}
    )
    assert protected_data_authorized(owner_only) is False

    wrong_persona = _request_with_cookies(
        {
            OWNER_COOKIE: owner_token,
            OWNER_PERSONA_COOKIE: mary_persona,
            PROTECTED_DATA_COOKIE: protected_token,
        }
    )
    assert protected_data_authorized(wrong_persona) is False

    matching = _request_with_cookies(
        {
            OWNER_COOKIE: owner_token,
            OWNER_PERSONA_COOKIE: kev_persona,
            PROTECTED_DATA_COOKIE: protected_token,
        }
    )
    assert protected_data_authorized(matching) is True


def test_backup_scheduler_fails_closed_without_encryption_recipient(monkeypatch, tmp_path):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "studio.sqlite3"))
    monkeypatch.setenv("AURA_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("LSS_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("LSS_BACKUP_STATUS", str(tmp_path / "backup-status.json"))
    monkeypatch.setenv("LSS_AUTO_BACKUP_ENABLED", "true")
    monkeypatch.delenv("LSS_BACKUP_AGE_RECIPIENT", raising=False)

    scheduler = BackupScheduler()
    assert scheduler.enabled is True
    assert scheduler.encryption_ready is False
    assert scheduler.operational is False
    assert scheduler.due() is False

    result = scheduler.run_once(force=True)
    assert result["ran"] is False
    assert result["ok"] is False
    assert "encryption recipient" in result["reason"]
    assert list((tmp_path / "backups").glob("*.zip*")) == []


def test_backup_scheduler_passes_only_encrypted_output_to_recovery_rotation(monkeypatch, tmp_path):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "studio.sqlite3"))
    monkeypatch.setenv("AURA_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("LSS_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("LSS_BACKUP_STATUS", str(tmp_path / "backup-status.json"))
    monkeypatch.setenv("LSS_AUTO_BACKUP_ENABLED", "true")
    monkeypatch.setenv("LSS_BACKUP_AGE_RECIPIENT", "age1publicrecipient")

    scheduler = BackupScheduler()
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        output = Path(kwargs["output"])
        return {
            "backup": None,
            "encrypted_backup": str(output.with_suffix(output.suffix + ".age")),
            "bytes": 123,
            "manifest": {"project_file_count": 2},
        }

    monkeypatch.setattr(scheduler.manager, "create", fake_create)
    result = scheduler.run_once(force=True)

    assert result["ok"] is True
    assert result["encrypted"] is True
    assert captured["age_recipient"] == "age1publicrecipient"
    assert captured["keep_plain_when_encrypted"] is False
