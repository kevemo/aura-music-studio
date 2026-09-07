from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from . import shared_sky_live_community as live

_NOTIFICATION_KIND = "creator_live"
_NOTIFICATION_STALE_SECONDS = 300
_INSTALLED = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_hardening_schema(store: Any) -> None:
    """Additive Chat 4 durability tables; safe for legacy databases and test stores."""

    now = _now()
    with store._connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS shared_sky_notification_delivery (
                broadcast_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                notification_id TEXT,
                last_error TEXT NOT NULL DEFAULT '',
                claimed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(broadcast_id,user_id,kind),
                FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_shared_sky_notification_delivery_state
                ON shared_sky_notification_delivery(state,updated_at);

            CREATE TABLE IF NOT EXISTS shared_sky_poll_vote_receipts (
                poll_id TEXT NOT NULL,
                voter_key TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                option_ids_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(poll_id,voter_key),
                FOREIGN KEY(poll_id) REFERENCES shared_sky_polls(id) ON DELETE CASCADE
            );
            """
        )
        # Historical Chat 4 emission rows were written before delivery was attempted. Treat them as
        # already sent during migration to avoid surprising duplicate notifications after upgrade.
        con.execute(
            """INSERT OR IGNORE INTO shared_sky_notification_delivery
               (broadcast_id,user_id,kind,state,attempts,created_at,updated_at)
               SELECT broadcast_id,user_id,kind,'sent',1,created_at,?
               FROM shared_sky_notification_emissions""",
            (now,),
        )


def _existing_aura_notification_id(user_id: str, broadcast_id: str) -> str | None:
    """Close the crash window between Aura notification creation and Chat 4 receipt finalisation."""

    notifier = live.notification_store
    chat_store = getattr(notifier, "chat_store", None)
    connect = getattr(chat_store, "_connect", None)
    if not callable(connect):
        return None
    try:
        with connect() as con:
            row = con.execute(
                """SELECT id FROM aura_notifications
                   WHERE user_id=? AND kind='shared_sky_live'
                     AND resource_kind='shared_sky_live' AND resource_id=?
                   ORDER BY created_at DESC LIMIT 1""",
                (user_id, broadcast_id),
            ).fetchone()
    except Exception:
        return None
    return str(row["id"] if hasattr(row, "keys") else row[0]) if row else None


def _claim_notification(store: Any, broadcast_id: str, user_id: str) -> bool:
    _ensure_hardening_schema(store)
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    stale_before = (now_dt - timedelta(seconds=_NOTIFICATION_STALE_SECONDS)).isoformat()
    with store._connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            """SELECT state,claimed_at FROM shared_sky_notification_delivery
               WHERE broadcast_id=? AND user_id=? AND kind=?""",
            (broadcast_id, user_id, _NOTIFICATION_KIND),
        ).fetchone()
        if row and str(row["state"]) == "sent":
            return False
        if row is None:
            con.execute(
                """INSERT INTO shared_sky_notification_delivery
                   (broadcast_id,user_id,kind,state,attempts,created_at,updated_at)
                   VALUES(?,?,?,'pending',0,?,?)""",
                (broadcast_id, user_id, _NOTIFICATION_KIND, now, now),
            )
        elif str(row["state"]) == "sending" and str(row["claimed_at"] or "") > stale_before:
            return False
        con.execute(
            """UPDATE shared_sky_notification_delivery
               SET state='sending',attempts=attempts+1,claimed_at=?,updated_at=?,last_error=''
               WHERE broadcast_id=? AND user_id=? AND kind=?""",
            (now, now, broadcast_id, user_id, _NOTIFICATION_KIND),
        )
    return True


def _finalise_notification(
    store: Any,
    broadcast_id: str,
    user_id: str,
    *,
    notification_id: str | None = None,
    error: Exception | None = None,
) -> None:
    now = _now()
    with store._connect() as con:
        if error is None:
            con.execute(
                """UPDATE shared_sky_notification_delivery
                   SET state='sent',notification_id=?,last_error='',claimed_at=NULL,updated_at=?
                   WHERE broadcast_id=? AND user_id=? AND kind=?""",
                (notification_id, now, broadcast_id, user_id, _NOTIFICATION_KIND),
            )
            con.execute(
                """INSERT OR IGNORE INTO shared_sky_notification_emissions
                   (broadcast_id,user_id,kind,created_at) VALUES(?,?,?,?)""",
                (broadcast_id, user_id, _NOTIFICATION_KIND, now),
            )
        else:
            con.execute(
                """UPDATE shared_sky_notification_delivery
                   SET state='failed',last_error=?,claimed_at=NULL,updated_at=?
                   WHERE broadcast_id=? AND user_id=? AND kind=?""",
                (str(error)[:500], now, broadcast_id, user_id, _NOTIFICATION_KIND),
            )


def _notify_followers_retry_safe(self: Any, broadcast_id: str) -> None:
    try:
        broadcast = self._broadcast(broadcast_id)
    except KeyError:
        return
    with self._connect() as con:
        followers = con.execute(
            """SELECT follower_user_id FROM shared_sky_follows
               WHERE creator_user_id=? AND notify_live=1""",
            (broadcast["user_id"],),
        ).fetchall()
    for row in followers:
        user_id = str(row["follower_user_id"])
        if not _claim_notification(self, broadcast_id, user_id):
            continue
        existing_id = _existing_aura_notification_id(user_id, broadcast_id)
        if existing_id:
            _finalise_notification(
                self,
                broadcast_id,
                user_id,
                notification_id=existing_id,
            )
            continue
        try:
            created = live.notification_store.create(
                user_id,
                kind="shared_sky_live",
                title=f"{broadcast['creator_display_name']} is LIVE",
                body=broadcast["title"],
                resource_kind="shared_sky_live",
                resource_id=broadcast_id,
            )
            notification_id = (
                str(created.get("id"))
                if isinstance(created, dict) and created.get("id")
                else str(getattr(created, "id", "") or "") or None
            )
        except Exception as exc:
            _finalise_notification(self, broadcast_id, user_id, error=exc)
            continue
        _finalise_notification(
            self,
            broadcast_id,
            user_id,
            notification_id=notification_id,
        )


def _legacy_vote_receipt(con: sqlite3.Connection, poll_id: str, voter_key: str) -> bool:
    rows = con.execute(
        """SELECT option_id,idempotency_key,created_at FROM shared_sky_poll_votes
           WHERE poll_id=? AND voter_key=? ORDER BY created_at,option_id""",
        (poll_id, voter_key),
    ).fetchall()
    if not rows:
        return False
    con.execute(
        """INSERT OR IGNORE INTO shared_sky_poll_vote_receipts
           (poll_id,voter_key,idempotency_key,option_ids_json,created_at)
           VALUES(?,?,?,?,?)""",
        (
            poll_id,
            voter_key,
            str(rows[0]["idempotency_key"]),
            json.dumps([str(row["option_id"]) for row in rows], separators=(",", ":")),
            str(rows[0]["created_at"]),
        ),
    )
    return True


def _vote_poll_serialized(
    self: Any,
    poll_id: str,
    voter_key: str,
    actor_user_id: str | None,
    body: live.PollVoteRequest,
) -> dict:
    _ensure_hardening_schema(self)

    # A completed receipt is a final one-vote-per-viewer decision. Retries and competing stale
    # requests return the already-committed result without consuming more rate-limit budget.
    with self._connect() as con:
        existing = con.execute(
            "SELECT 1 FROM shared_sky_poll_vote_receipts WHERE poll_id=? AND voter_key=?",
            (poll_id, voter_key),
        ).fetchone()
        if existing:
            return self.poll(poll_id, voter_key)
        if _legacy_vote_receipt(con, poll_id, voter_key):
            return self.poll(poll_id, voter_key)

    option_ids = list(dict.fromkeys(body.option_ids))
    self.rate_limit(voter_key, "poll_vote", limit=8, window_seconds=60)
    inserted = False
    broadcast_id = ""
    committed_key = body.idempotency_key

    with self._connect() as con:
        con.execute("BEGIN IMMEDIATE")
        poll = con.execute("SELECT * FROM shared_sky_polls WHERE id=?", (poll_id,)).fetchone()
        if not poll:
            raise KeyError(poll_id)
        broadcast_id = str(poll["broadcast_id"])
        ends_at = live._parse_dt(poll["ends_at"])
        if str(poll["state"]) != "live" or (
            ends_at is not None and ends_at <= datetime.now(timezone.utc)
        ):
            if str(poll["state"]) == "live":
                con.execute(
                    "UPDATE shared_sky_polls SET state='ended',closed_at=? WHERE id=?",
                    (live._now(), poll_id),
                )
            raise RuntimeError("Poll is closed")
        multiple_choice = bool(poll["multiple_choice"])
        if not multiple_choice and len(option_ids) != 1:
            raise ValueError("Choose one option")
        allowed = {
            str(row["id"])
            for row in con.execute(
                "SELECT id FROM shared_sky_poll_options WHERE poll_id=?",
                (poll_id,),
            ).fetchall()
        }
        if any(option_id not in allowed for option_id in option_ids):
            raise ValueError("Unknown poll option")

        receipt = con.execute(
            """SELECT idempotency_key FROM shared_sky_poll_vote_receipts
               WHERE poll_id=? AND voter_key=?""",
            (poll_id, voter_key),
        ).fetchone()
        if receipt:
            committed_key = str(receipt["idempotency_key"])
        elif _legacy_vote_receipt(con, poll_id, voter_key):
            receipt = con.execute(
                """SELECT idempotency_key FROM shared_sky_poll_vote_receipts
                   WHERE poll_id=? AND voter_key=?""",
                (poll_id, voter_key),
            ).fetchone()
            committed_key = str(receipt["idempotency_key"] if receipt else body.idempotency_key)
        else:
            now = live._now()
            con.execute(
                """INSERT INTO shared_sky_poll_vote_receipts
                   (poll_id,voter_key,idempotency_key,option_ids_json,created_at)
                   VALUES(?,?,?,?,?)""",
                (
                    poll_id,
                    voter_key,
                    body.idempotency_key,
                    json.dumps(option_ids, separators=(",", ":")),
                    now,
                ),
            )
            for option_id in option_ids:
                con.execute(
                    """INSERT INTO shared_sky_poll_votes
                       (poll_id,voter_key,option_id,idempotency_key,created_at)
                       VALUES(?,?,?,?,?)""",
                    (poll_id, voter_key, option_id, body.idempotency_key, now),
                )
            inserted = True

    updated = self.poll(poll_id, voter_key)
    if inserted:
        self.emit(
            broadcast_id,
            actor_user_id,
            "poll.voted",
            {
                "poll_id": poll_id,
                "results": updated["options"] if updated["results_visible"] else None,
            },
            idempotency_key=f"poll-vote:{poll_id}:{voter_key}:{committed_key}",
        )
    return updated


def install_live_community_hardening() -> None:
    """Install additive retry/concurrency hardening without forking Chat 4 domain ownership."""

    global _INSTALLED
    if _INSTALLED:
        return
    live.LiveCommunityStore._notify_followers_once = _notify_followers_retry_safe
    live.LiveCommunityStore.vote_poll = _vote_poll_serialized
    _ensure_hardening_schema(live.community)
    _INSTALLED = True


__all__ = ["install_live_community_hardening"]
