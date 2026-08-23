from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .accounts import AccountStore


ALLOWED_SETTINGS: dict[str, tuple[type, Any, int | None, int | None]] = {
    "auto_backup_enabled": (bool, False, None, None),
    "auto_backup_interval_hours": (int, 24, 1, 24 * 30),
    "auto_backup_keep": (int, 7, 1, 365),
    "auto_backup_include_outputs": (bool, False, None, None),
    "auto_backup_include_work": (bool, True, None, None),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StudioSettings:
    """Small owner-controlled runtime settings store.

    Only explicitly whitelisted non-secret operational settings live here. Credentials, DDNS tokens,
    payment secrets, SMTP credentials and encryption private material remain deployment secrets.
    """

    def __init__(self, store: AccountStore | None = None):
        self.store = store or AccountStore()
        self.db_path = self.store.db_path
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS studio_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT
                )"""
            )

    @staticmethod
    def _validate(key: str, value: Any) -> Any:
        if key not in ALLOWED_SETTINGS:
            raise KeyError(f"Unsupported Studio setting: {key}")
        expected, default, minimum, maximum = ALLOWED_SETTINGS[key]
        if expected is bool:
            if isinstance(value, bool):
                parsed = value
            elif isinstance(value, str):
                text = value.strip().lower()
                if text in {"1", "true", "yes", "on"}:
                    parsed = True
                elif text in {"0", "false", "no", "off", ""}:
                    parsed = False
                else:
                    raise ValueError(f"Invalid boolean for {key}")
            else:
                parsed = bool(value)
            return parsed
        if expected is int:
            try:
                parsed = int(value)
            except Exception as exc:
                raise ValueError(f"Invalid integer for {key}") from exc
            if minimum is not None and parsed < minimum:
                raise ValueError(f"{key} must be >= {minimum}")
            if maximum is not None and parsed > maximum:
                raise ValueError(f"{key} must be <= {maximum}")
            return parsed
        return value if value is not None else default

    def get(self, key: str) -> Any:
        if key not in ALLOWED_SETTINGS:
            raise KeyError(key)
        default = ALLOWED_SETTINGS[key][1]
        with self._connect() as con:
            row = con.execute("SELECT value_json FROM studio_settings WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return self._validate(key, json.loads(row["value_json"]))
        except Exception:
            return default

    def set(self, key: str, value: Any, *, updated_by: str = "ESP Owner") -> Any:
        parsed = self._validate(key, value)
        with self._connect() as con:
            con.execute(
                """INSERT INTO studio_settings (key,value_json,updated_at,updated_by)
                   VALUES (?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET
                     value_json=excluded.value_json,
                     updated_at=excluded.updated_at,
                     updated_by=excluded.updated_by""",
                (key, json.dumps(parsed), _now(), (updated_by or "ESP Owner")[:120]),
            )
        return parsed

    def update_many(self, values: dict[str, Any], *, updated_by: str = "ESP Owner") -> dict[str, Any]:
        validated = {key: self._validate(key, value) for key, value in values.items()}
        now = _now()
        with self._connect() as con:
            for key, parsed in validated.items():
                con.execute(
                    """INSERT INTO studio_settings (key,value_json,updated_at,updated_by)
                       VALUES (?,?,?,?)
                       ON CONFLICT(key) DO UPDATE SET
                         value_json=excluded.value_json,
                         updated_at=excluded.updated_at,
                         updated_by=excluded.updated_by""",
                    (key, json.dumps(parsed), now, (updated_by or "ESP Owner")[:120]),
                )
        return validated

    def all_public(self) -> dict[str, Any]:
        return {key: self.get(key) for key in ALLOWED_SETTINGS}
