from __future__ import annotations

import json
import math
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
                "ip_address": "203.0.113.5",
            },
        }
    )

    assert sanitized["email"] == "[REDACTED:PII]"
    assert sanitized["password"] == "[REDACTED:SECRET]"
    assert sanitized["nested"]["refresh_token"] == "[REDACTED:SECRET]"
    assert sanitized["nested"]["display_name"] == "[REDACTED:PII]"
    assert sanitized["nested"]["ip_address"] == "[REDACTED:PII]"
    assert sanitized["nested"]["status"] == "allowed"


def test_free_text_scrubs_email_bearer_jwt_private_key_ip_and_secret_query_values():
    # Assemble the synthetic PEM boundary at runtime so the repository itself never contains a
    # committed string that resembles a real private key header and trips the secret scanner.
    pem_begin = "-----BEGIN " + "PRIVATE" + " KEY-----"
    pem_end = "-----END " + "PRIVATE" + " KEY-----"
    text = (
        "user person@example.com from 203.0.113.5 auth Bearer abc.def-123 "
        "jwt eyJabcdefghijk.abcdefghijkl.abcdefghijkl "
        f"key {pem_begin}\nverysecretmaterial\n{pem_end} "
        "url https://example.test/callback?access_token=topsecret&mode=safe"
    )
    redacted = redact_text(text)

    for secret in (
        "person@example.com",
        "203.0.113.5",
        "abc.def-123",
        "eyJabcdefghijk.abcdefghijkl.abcdefghijkl",
        "verysecretmaterial",
        "access_token=topsecret",
    ):
        assert secret not in redacted
    assert "mode=safe" in redacted
    assert "[REDACTED:EMAIL]" in redacted
    assert "[REDACTED:TOKEN]" in redacted
    assert "[REDACTED:SECRET]" in redacted
    assert "[REDACTED:PII]" in redacted


def test_audit_ledger_persists_only_sanitized_data_and_keeps_chain_valid(tmp_path):
    store = AccountStore(tmp_path / "audit-dlp.sqlite3")
    ledger = AuditLedger(store)

    entry = ledger.append(
        actor="owner person@example.com",
        action="export requested for person@example.com",
        subject_user_id="person@example.com",
        details={
            "email": "person@example.com",
            "authorization": "Bearer super-secret-token",
            "note": "contact person@example.com after callback?token=hidden-value",
            "safe_count": 4,
        },
    )

    assert "person@example.com" not in entry["actor"]
    assert "person@example.com" not in entry["action"]
    assert "person@example.com" not in entry["subject_user_id"]
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
        raw = con.execute(
            "SELECT actor,action,subject_user_id,details_json FROM admin_audit_log"
        ).fetchone()
    assert "person@example.com" not in " ".join(value or "" for value in raw)
    assert "super-secret-token" not in " ".join(value or "" for value in raw)
    assert "hidden-value" not in " ".join(value or "" for value in raw)


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


def test_non_finite_numbers_are_normalized_for_strict_json_and_hash_stability(tmp_path):
    sanitized = sanitize_audit_details(
        {"nan": math.nan, "positive": math.inf, "negative": -math.inf, "normal": 1.25}
    )
    assert sanitized == {
        "nan": "[REDACTED:NON_FINITE_NUMBER]",
        "positive": "[REDACTED:NON_FINITE_NUMBER]",
        "negative": "[REDACTED:NON_FINITE_NUMBER]",
        "normal": 1.25,
    }
    json.dumps(sanitized, allow_nan=False)

    ledger = AuditLedger(AccountStore(tmp_path / "strict-json.sqlite3"))
    entry = ledger.append(actor="owner", action="telemetry", details={"risk": math.nan})
    assert entry["details"]["risk"] == "[REDACTED:NON_FINITE_NUMBER]"
    assert ledger.verify_chain()["valid"] is True


def test_sets_are_sanitized_deterministically_and_are_json_safe():
    first = sanitize_audit_details({"scopes": {"write", "read", "admin"}})
    second = sanitize_audit_details({"scopes": {"admin", "write", "read"}})
    assert first == second
    assert first["scopes"] == ["admin", "read", "write"]
    json.dumps(first, allow_nan=False)


def test_chain_verification_fails_closed_on_malformed_persisted_details(tmp_path):
    store = AccountStore(tmp_path / "tamper.sqlite3")
    ledger = AuditLedger(store)
    entry = ledger.append(actor="owner", action="safe", details={"status": "ok"})
    assert ledger.verify_chain()["valid"] is True

    with sqlite3.connect(store.db_path) as con:
        con.execute(
            "UPDATE admin_audit_log SET details_json = ? WHERE id = ?",
            ("{not-valid-json", entry["id"]),
        )
        con.commit()

    result = ledger.verify_chain()
    assert result["valid"] is False
    assert result["broken_at"] == entry["id"]
