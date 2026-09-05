from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .shared_sky_relay import relay
from .shared_sky_transport_models import BroadcastState, TERMINAL, iso, now


class TransportRecoveryMixin:
    """Operational recovery and replay-marker extensions for Shared Sky transport."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._recovery_schema()

    def _recovery_schema(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS shared_sky_highlight_markers (
                    id TEXT PRIMARY KEY,
                    broadcast_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    marker_type TEXT NOT NULL DEFAULT 'highlight',
                    label TEXT NOT NULL DEFAULT '',
                    offset_ms INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_highlight_markers_broadcast
                ON shared_sky_highlight_markers(broadcast_id, offset_ms, created_at);
                """
            )

    def add_highlight_marker(
        self,
        user_id: str,
        broadcast_id: str,
        *,
        offset_ms: int,
        label: str = "",
        marker_type: str = "highlight",
    ) -> dict:
        self._session(user_id, broadcast_id)
        offset = int(offset_ms)
        if offset < 0 or offset > 172_800_000:
            raise ValueError("Highlight marker offset must be between 0 and 48 hours")
        kind = (marker_type or "highlight").strip().lower()
        if kind not in {"highlight", "chapter", "clip", "replay"}:
            raise ValueError("Unsupported Shared Sky marker type")
        clean_label = (label or "").strip()
        if len(clean_label) > 240:
            raise ValueError("Highlight marker label is too long")
        marker_id = f"mark_{uuid4().hex}"
        stamp = iso()
        with self.connect() as con:
            con.execute(
                """INSERT INTO shared_sky_highlight_markers
                   (id,broadcast_id,user_id,marker_type,label,offset_ms,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (marker_id, broadcast_id, user_id, kind, clean_label, offset, stamp),
            )
        self.emit(
            broadcast_id,
            "highlight_marker_created",
            "ok",
            {"offset_ms": offset},
        )
        return {
            "id": marker_id,
            "broadcast_id": broadcast_id,
            "marker_type": kind,
            "label": clean_label,
            "offset_ms": offset,
            "created_at": stamp,
        }

    def highlight_markers(self, user_id: str, broadcast_id: str) -> list[dict]:
        self._session(user_id, broadcast_id)
        with self.connect() as con:
            rows = con.execute(
                """SELECT id,broadcast_id,marker_type,label,offset_ms,created_at
                   FROM shared_sky_highlight_markers
                   WHERE broadcast_id=? AND user_id=?
                   ORDER BY offset_ms,id""",
                (broadcast_id, user_id),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def verify_playback_token(
        token: str,
        *,
        expected_broadcast_id: str | None = None,
        expected_user_id: str | None = None,
    ) -> dict:
        """Verify the bearer token issued by TransportSupport.playback()."""
        secret = (os.getenv("SHARED_SKY_PLAYBACK_SIGNING_SECRET") or "").strip()
        if not secret:
            raise RuntimeError("Shared Sky playback signing is not configured")
        parts = (token or "").split(".")
        if len(parts) != 5:
            raise ValueError("Invalid Shared Sky playback token")
        broadcast_id, user_id, expiry_raw, nonce, supplied = parts
        body = ".".join(parts[:-1])
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, supplied):
            raise ValueError("Invalid Shared Sky playback token")
        try:
            expiry = datetime.fromtimestamp(int(expiry_raw), tz=timezone.utc)
        except (ValueError, OverflowError) as exc:
            raise ValueError("Invalid Shared Sky playback token") from exc
        if expiry <= now():
            raise ValueError("Shared Sky playback token has expired")
        if expected_broadcast_id and broadcast_id != expected_broadcast_id:
            raise ValueError("Shared Sky playback token is bound to another broadcast")
        if expected_user_id and user_id != expected_user_id:
            raise ValueError("Shared Sky playback token is bound to another member")
        return {
            "broadcast_id": broadcast_id,
            "user_id": user_id,
            "expires_at": expiry.isoformat(),
            "nonce": nonce,
        }

    def stop(
        self,
        user_id: str,
        broadcast_id: str,
        key: str,
        reason: str = "creator_stop",
    ) -> dict:
        """Stop media first, then close provider resources in deterministic destination order."""
        self.rate_limit(user_id, "stop", limit=20)

        def run():
            session = self._session(user_id, broadcast_id)
            if BroadcastState(session["state"]) in TERMINAL:
                return {
                    "broadcast": self.status(user_id, broadcast_id),
                    "already_terminal": True,
                }
            self._set_state(
                user_id,
                broadcast_id,
                BroadcastState.STOPPING,
                force=True,
                reason=reason,
            )
            with self.connect() as con:
                runs = [
                    dict(row)
                    for row in con.execute(
                        """SELECT * FROM shared_sky_destination_runs
                           WHERE broadcast_id=? ORDER BY destination_id,id""",
                        (broadcast_id,),
                    ).fetchall()
                ]
            for item in runs:
                if item.get("output_id"):
                    relay.stop_output(str(item["output_id"]))
                try:
                    destination = self.base.destination(user_id, item["destination_id"])
                    adapter = self._adapter(destination)
                    if adapter:
                        adapter.stop(user_id=user_id, destination=destination, run=item)
                except Exception:
                    # Stop must remain best-effort across independent providers. The transport
                    # record is still terminal and the provider can be reconciled administratively.
                    self.emit(
                        broadcast_id,
                        "destination_stop_warning",
                        "provider_stop_failed",
                        destination_id=item["destination_id"],
                    )
            stamp = iso()
            with self.connect() as con:
                con.execute(
                    """UPDATE shared_sky_destination_runs
                       SET state='ended',ended_at=COALESCE(ended_at,?),updated_at=?
                       WHERE broadcast_id=? AND state NOT IN ('ended','failed','unavailable')""",
                    (stamp, stamp, broadcast_id),
                )
                con.execute(
                    """UPDATE shared_sky_recordings
                       SET state=CASE WHEN state IN ('requested','recording') THEN 'incomplete' ELSE state END,
                           reason_code=CASE WHEN state IN ('requested','recording')
                               THEN 'broadcast_stopped_before_finalize' ELSE reason_code END,
                           updated_at=?
                       WHERE broadcast_id=?""",
                    (stamp, broadcast_id),
                )
            self._set_state(
                user_id,
                broadcast_id,
                BroadcastState.ENDED,
                force=True,
                reason=reason,
            )
            return {
                "broadcast": self.status(user_id, broadcast_id),
                "already_terminal": False,
            }

        return self._idem(user_id, broadcast_id, "stop", key, {"reason": reason}, run)

    def cleanup_stale_sessions(self, *, stale_after_seconds: int = 300) -> dict:
        """Recover only transitional sessions whose persisted state is clearly stale."""
        seconds = max(60, min(int(stale_after_seconds), 86_400))
        cutoff = iso(now() - timedelta(seconds=seconds))
        with self.connect() as con:
            rows = [
                dict(row)
                for row in con.execute(
                    """SELECT broadcast_id,user_id,state,updated_at
                       FROM shared_sky_transport_sessions
                       WHERE state IN ('starting','stopping') AND updated_at<?
                       ORDER BY updated_at,broadcast_id""",
                    (cutoff,),
                ).fetchall()
            ]
        actions: list[dict] = []
        for stale in rows:
            broadcast_id = str(stale["broadcast_id"])
            user_id = str(stale["user_id"])
            current = self._session(user_id, broadcast_id)
            if current["state"] != stale["state"]:
                continue
            with self.connect() as con:
                runs = [
                    dict(row)
                    for row in con.execute(
                        """SELECT destination_id,output_id FROM shared_sky_destination_runs
                           WHERE broadcast_id=? ORDER BY destination_id""",
                        (broadcast_id,),
                    ).fetchall()
                ]
            for run in runs:
                if run.get("output_id"):
                    relay.stop_output(str(run["output_id"]))
            if stale["state"] == BroadcastState.STOPPING:
                stamp = iso()
                with self.connect() as con:
                    con.execute(
                        """UPDATE shared_sky_destination_runs
                           SET state='ended',ended_at=COALESCE(ended_at,?),updated_at=?
                           WHERE broadcast_id=? AND state NOT IN ('ended','failed','unavailable')""",
                        (stamp, stamp, broadcast_id),
                    )
                target = BroadcastState.ENDED
                reason = "stale_stop_cleanup"
            else:
                target = BroadcastState.FAILED
                reason = "stale_start_cleanup"
            self._set_state(user_id, broadcast_id, target, force=True, reason=reason)
            actions.append(
                {
                    "broadcast_id": broadcast_id,
                    "previous_state": stale["state"],
                    "state": target.value,
                    "reason_code": reason,
                }
            )
        return {
            "stale_after_seconds": seconds,
            "recovered": len(actions),
            "actions": actions,
        }


__all__ = ["TransportRecoveryMixin"]
