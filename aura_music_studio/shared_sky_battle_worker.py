from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from .shared_sky_battles import SharedSkyBattleStore, iso


@dataclass(frozen=True)
class BattleWorkerSettings:
    enabled: bool
    poll_seconds: float

    @classmethod
    def from_env(cls) -> "BattleWorkerSettings":
        enabled = os.getenv("SHARED_SKY_BATTLE_WORKER_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
        try:
            poll = float(os.getenv("SHARED_SKY_BATTLE_WORKER_POLL_SECONDS", "1"))
        except ValueError:
            poll = 1.0
        return cls(enabled=enabled, poll_seconds=max(0.25, min(30.0, poll)))


class SharedSkyBattleFinalizer:
    """Durable server-time finaliser for Battle rounds.

    Clients can disappear, sleep or throttle without extending a competitive round. This worker
    only finalises due authoritative rounds already persisted by ``SharedSkyBattleStore``; it does
    not own transport, Gifts, engagement ingestion or payout state.
    """

    def __init__(
        self,
        store: SharedSkyBattleStore,
        *,
        settings: BattleWorkerSettings | None = None,
        worker_id: str | None = None,
    ):
        self.store = store
        self.settings = settings or BattleWorkerSettings.from_env()
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"
        self._init_schema()

    def _init_schema(self) -> None:
        with self.store._connect() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS shared_sky_battle_worker_heartbeats (
                    worker_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    finalised_count INTEGER NOT NULL DEFAULT 0,
                    last_error_code TEXT NOT NULL DEFAULT ''
                )"""
            )

    def heartbeat(self, status: str, *, finalised_count: int = 0, error_code: str = "") -> None:
        now = iso(datetime.now(timezone.utc))
        with self.store._connect() as con:
            con.execute(
                """INSERT INTO shared_sky_battle_worker_heartbeats(worker_id,status,last_seen_at,finalised_count,last_error_code)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(worker_id) DO UPDATE SET status=excluded.status,last_seen_at=excluded.last_seen_at,
                       finalised_count=excluded.finalised_count,last_error_code=excluded.last_error_code""",
                (self.worker_id, status[:32], now, max(0, int(finalised_count)), error_code[:120]),
            )

    def run_once(self) -> list[str]:
        self.heartbeat("running")
        try:
            finalised = self.store.finalize_due(limit=500)
        except Exception as exc:
            self.heartbeat("error", error_code=type(exc).__name__)
            raise
        self.heartbeat("idle", finalised_count=len(finalised))
        return finalised

    def run_forever(self) -> None:
        if not self.settings.enabled:
            self.heartbeat("disabled")
            return
        while True:
            self.run_once()
            time.sleep(self.settings.poll_seconds)


def run_worker() -> None:
    from .shared_sky_battle_api import battle_store
    SharedSkyBattleFinalizer(battle_store).run_forever()


__all__ = ["BattleWorkerSettings", "SharedSkyBattleFinalizer", "run_worker"]
