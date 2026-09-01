from __future__ import annotations

from . import aura_live_overlay_engine as engine

_ZERO_SHA256 = "0" * 64
_REQUIRED_BASE = {"user_id", "event_id", "event_type", "received_at"}


def prepare_legacy_receipt_schema() -> None:
    """Make known historical receipt tables readable by the scoped v2 migration.

    Experimental relay revisions used the same table name with fewer recovery columns. We do not
    infer provider/session identity for those rows and do not delete them. Missing recovery fields
    are added conservatively so the v2 migration can consume every historical event under the
    explicit `legacy/legacy` namespace as already processed.
    """

    with engine._connect() as con:
        table = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_overlay_connector_receipts'"
        ).fetchone()
        if not table:
            return
        columns = {
            str(row[1])
            for row in con.execute("PRAGMA table_info(live_overlay_connector_receipts)").fetchall()
        }
        if not _REQUIRED_BASE.issubset(columns):
            # Unknown/corrupt schemas are left untouched so the caller fails closed rather than
            # manufacturing event identities from insufficient evidence.
            return
        if "payload_sha256" not in columns:
            con.execute(
                "ALTER TABLE live_overlay_connector_receipts ADD COLUMN payload_sha256 TEXT NOT NULL DEFAULT '"
                + _ZERO_SHA256
                + "'"
            )
        if "state" not in columns:
            con.execute(
                "ALTER TABLE live_overlay_connector_receipts ADD COLUMN state TEXT NOT NULL DEFAULT 'processed'"
            )
        if "processed_at" not in columns:
            con.execute("ALTER TABLE live_overlay_connector_receipts ADD COLUMN processed_at TEXT")
        if "last_error_code" not in columns:
            con.execute("ALTER TABLE live_overlay_connector_receipts ADD COLUMN last_error_code TEXT")
        con.execute(
            """UPDATE live_overlay_connector_receipts
               SET state='processed',
                   processed_at=COALESCE(processed_at,received_at),
                   last_error_code=COALESCE(last_error_code,'LegacyReceiptConsumed')"""
        )


__all__ = ["prepare_legacy_receipt_schema"]
