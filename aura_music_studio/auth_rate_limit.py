from __future__ import annotations

import hashlib
import math
import sqlite3
import time
from pathlib import Path


class AuthRateLimitStore:
    """SQLite-backed sliding-window admission shared by application workers.

    The store keeps only a SHA-256 digest of the caller key, never the raw client address. A
    BEGIN IMMEDIATE transaction serializes count+insert admission across workers on the same
    durable SQLite database, preventing concurrent requests from all observing a stale count.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=2.0)
        con.execute("PRAGMA busy_timeout=2000")
        return con

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _init_schema(self, con: sqlite3.Connection) -> None:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS auth_rate_events (
                client_sha256 TEXT NOT NULL,
                scope TEXT NOT NULL,
                occurred_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_auth_rate_client_scope_time
                ON auth_rate_events(client_sha256, scope, occurred_at);
            CREATE INDEX IF NOT EXISTS idx_auth_rate_time
                ON auth_rate_events(occurred_at);
            """
        )

    def allow(
        self,
        client_key: str,
        scope: str,
        *,
        limit: int,
        window_seconds: int,
        now: float | None = None,
    ) -> tuple[bool, int]:
        limit = int(limit)
        window_seconds = int(window_seconds)
        if limit < 1 or limit > 10_000:
            raise ValueError("Auth rate limit must be between 1 and 10000")
        if window_seconds < 1 or window_seconds > 86_400:
            raise ValueError("Auth rate window must be between 1 and 86400 seconds")

        current = float(time.time() if now is None else now)
        cutoff = current - window_seconds
        client_digest = self._digest(str(client_key or "unknown"))
        scope = str(scope or "auth")[:240]

        with self._connect() as con:
            self._init_schema(con)
            con.execute("BEGIN IMMEDIATE")
            con.execute("DELETE FROM auth_rate_events WHERE occurred_at < ?", (cutoff,))
            rows = con.execute(
                """SELECT occurred_at FROM auth_rate_events
                   WHERE client_sha256=? AND scope=? AND occurred_at>=?
                   ORDER BY occurred_at ASC LIMIT ?""",
                (client_digest, scope, cutoff, limit),
            ).fetchall()
            if len(rows) >= limit:
                earliest = float(rows[0][0])
                retry_after = max(1, math.ceil(window_seconds - (current - earliest)))
                return False, retry_after
            con.execute(
                "INSERT INTO auth_rate_events(client_sha256,scope,occurred_at) VALUES (?,?,?)",
                (client_digest, scope, current),
            )
        return True, 0


__all__ = ["AuthRateLimitStore"]
