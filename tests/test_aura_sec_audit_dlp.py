from __future__ import annotations

import json
import sqlite3

from aura_music_studio.accounts import AccountStore
from aura_music_studio.audit import AuditLedger
from aura_music_studio.aura_sec_dlp import redact_text, sanitize_audit_details


def test_structured_secrets_and_pii_are_redacted():
    sanitized = sanitize_audit_details(
        {
            "email": "person@example.com",
            "password": "NeverStoreThis123!",
            "nested": {
                "refresh_token": "refresh-secret",
                "display_name": "Private Person",
                "status": "allowed",
            },
        }
    )

    assert sanitized["email"] == "[REDACTED:PII]"
    assert sanitized["password"] == "[REDACTED:SECRET]"
    assert sanitized["nested"]["refresh_token"] == "[REDACTED:SECRET]"
    assert sanitized["nested"]["display_name"] == "[REDACTED:PII]"
    assert sanitized["nested"]["status"] == "allowed"


def test_free_text_scrubs_email_bearer_jwt_and_secret_query_values():
    text = (
        "user person@example.com auth Bearer abc.def-123 "
        "jwt eyJabcdefghijk.abcdefghijkl.abcdefghijkl "
        "url https://example.test/callback?access_token=topsecret&mode=safe"
    )
    redacted = redact_text(text)

    assert "person@example.com" not in redacted
    assert "abc.def-123" not in redacted
    assert "eyJabcdefghijk.abcdefghijkl.abcdefghijkl" not in redacted
    assert "access_token=topsecret" not in redacted
    assert "mode=safe" in redacted
    assert "[REDACTED:EMAIL]" in redacted
    assert "[REDACTED:TOKEN]" in redacted


def test_audit_ledger_persists_only_sanitized_details_and_keeps_chain_valid(tmp_path):
    store = AccountStore(tmp_path / "audit-dlp.sqlite3")
    ledger = AuditLedger(store)

    entry = ledger.append(
        actor="owner person@example.com",
        action="export requested for person@example.com",
        subject_user_id="opaque-user-123",
        details={
            "email": "person@example.com",
            "authorization": "Bearer super-secret-token",
            "note": "contact person@example.com after callback?token=hidden-value",
            "safe_count": 4,
        },
    )

    assert "person@example.com" not in entry["actor"]
    assert "person@example.com" not in entry["action"]
    assert entry["subject_user_id"] == "opaque-user-123"
    assert entry["details"]["email"] == "[REDACTED:PII]"
    assert entry["details"]["authorization"] == "[REDACTED:SECRET]"
    assert "person@example.com" not in entry["details"]["note"]
    assert "token=hidden-value" not in entry["details"]["note"]
    assert entry["details"]["safe_count"] == 4

    recent = ledger.recent(1)[0]
    persisted = json.dumps(recent, sort_keys=True)
    assert "person@example.com" not in persisted
    assert "super-secret-token" not in persisted
    assert "hidden-value" not in persisted
    assert ledger.verify_chain()["valid"] is True

    with sqlite3.connect(store.db_path) as con:
        raw = con.execute("SELECT actor,action,details_json FROM admin_audit_log").fetchone()
    assert "person@example.com" not in " ".join(raw)
    assert "super-secret-token" not in " ".join(raw)
    assert "hidden-value" not in " ".join(raw)


def test_dlp_limits_depth_and_collection_size_without_losing_event_semantics():
    deeply_nested = {"level": {"level": {"level": {"value": "safe"}}}}
    limited = sanitize_audit_details(deeply_nested, max_depth=2)
    assert limited["level"]["level"] == "[REDACTED:DEPTH_LIMIT]"

    many = sanitize_audit_details({f"item_{i}": i for i in range(5)}, max_items=2)
    assert many["item_0"] == 0
    assert many["item_1"] == 1
    assert many["_truncated"] is True


def test_dlp_never_echoes_binary_payloads_into_security_logs():
    sanitized = sanitize_audit_details({"payload": b"\x00private-binary\xff"})
    assert sanitized["payload"] == "[REDACTED:BINARY]"
