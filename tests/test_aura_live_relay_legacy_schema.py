from __future__ import annotations

import sqlite3


def test_minimal_historical_receipt_schema_is_conservatively_consumed(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_overlay_engine as engine
    from aura_music_studio import aura_live_relay_identity as identity
    from aura_music_studio.aura_live_relay_legacy_schema import prepare_legacy_receipt_schema

    db = tmp_path / "historical-relay.sqlite3"
    monkeypatch.setattr(engine, "DB_PATH", db)
    with sqlite3.connect(db) as con:
        con.executescript(
            """
            CREATE TABLE live_overlay_connector_receipts (
                user_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                received_at TEXT NOT NULL,
                PRIMARY KEY(user_id,event_id)
            );
            INSERT INTO live_overlay_connector_receipts VALUES(
                'creator-1','historical-event-0001','gift','2026-08-29T00:00:00+00:00'
            );
            """
        )

    prepare_legacy_receipt_schema()
    identity._ensure_schema()

    with engine._connect() as con:
        legacy_columns = {
            row[1] for row in con.execute("PRAGMA table_info(live_overlay_connector_receipts)").fetchall()
        }
        migrated = con.execute(
            """SELECT provider,session_id,event_id,state,payload_sha256,processed_at,last_error_code
               FROM live_overlay_connector_receipts_v2
               WHERE user_id='creator-1' AND event_id='historical-event-0001'"""
        ).fetchone()

    assert {"payload_sha256", "state", "processed_at", "last_error_code"}.issubset(legacy_columns)
    assert migrated["provider"] == "legacy"
    assert migrated["session_id"] == "legacy"
    assert migrated["state"] == "processed"
    assert migrated["payload_sha256"] == "0" * 64
    assert migrated["processed_at"] == "2026-08-29T00:00:00+00:00"
    assert migrated["last_error_code"] == "LegacyReceiptConsumed"
