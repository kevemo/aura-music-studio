from __future__ import annotations

from .shared_sky_relay import relay
from .shared_sky_transport_models import BroadcastState, TERMINAL, iso


class TransportMediaLifecycleMixin:
    """Keep media-process shutdown inside the durable stop/idempotency boundary."""

    def stop(
        self,
        user_id: str,
        broadcast_id: str,
        key: str,
        reason: str = "creator_stop",
    ) -> dict:
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

            try:
                self._stop_internal_delivery(user_id, broadcast_id, reason=reason)
            except Exception:
                self.emit(
                    broadcast_id,
                    "internal_playback_stop_warning",
                    "internal_playback_stop_failed",
                )
            try:
                self._stop_recording_delivery(user_id, broadcast_id, reason=reason)
            except Exception:
                self.emit(
                    broadcast_id,
                    "recording_stop_warning",
                    "recording_stop_failed",
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

    def cleanup_stale_sessions(self, *, stale_after_seconds: int=300) -> dict:
        result = super().cleanup_stale_sessions(stale_after_seconds=stale_after_seconds)
        for action in result.get("actions", []):
            broadcast_id = str(action.get("broadcast_id") or "")
            if not broadcast_id:
                continue
            with self.connect() as con:
                session = con.execute(
                    "SELECT user_id FROM shared_sky_transport_sessions WHERE broadcast_id=?",
                    (broadcast_id,),
                ).fetchone()
            if not session:
                continue
            user_id = str(session["user_id"])
            try:
                self._stop_internal_delivery(
                    user_id,
                    broadcast_id,
                    reason=str(action.get("reason_code") or "stale_cleanup"),
                )
            except Exception:
                pass
            try:
                self._stop_recording_delivery(
                    user_id,
                    broadcast_id,
                    reason=str(action.get("reason_code") or "stale_cleanup"),
                )
            except Exception:
                pass
        return result


__all__ = ["TransportMediaLifecycleMixin"]
