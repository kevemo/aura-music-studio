from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from .shared_sky_internal_media import SharedSkyInternalMediaError, internal_media
from .shared_sky_transport_models import BroadcastState, iso


_ACTIVE = {
    BroadcastState.STARTING,
    BroadcastState.LIVE,
    BroadcastState.DEGRADED,
    BroadcastState.RECONNECTING,
}


class TransportLocalRecordingMixin:
    """Allow the first-party local recorder to be the durable recording target."""

    def _local_recording_ready(self) -> bool:
        settings = internal_media.settings
        health = internal_media.health()
        if not (
            settings.enabled
            and settings.recording_root
            and health.ffmpeg_available
        ):
            return False
        try:
            settings.recording_root.mkdir(parents=True, exist_ok=True)
            return settings.recording_root.is_dir() and os.access(settings.recording_root, os.W_OK)
        except OSError:
            return False

    def preflight(self, user_id: str, broadcast_id: str) -> dict:
        result = super().preflight(user_id, broadcast_id)
        session = self._session(user_id, broadcast_id)
        if not session.get("recording_enabled") or not self._local_recording_ready():
            return result
        removed = [
            item
            for item in result.get("blocking_errors", [])
            if item.get("code") == "recording_storage_unconfigured"
        ]
        if not removed:
            return result
        result["blocking_errors"] = [
            item
            for item in result.get("blocking_errors", [])
            if item.get("code") != "recording_storage_unconfigured"
        ]
        result["warnings"] = list(result.get("warnings", []))
        result["recording"] = {
            "capability_state": "ready",
            "mode": "first_party_local",
            "storage_path_exposed": False,
        }
        result["ready"] = not result["blocking_errors"]
        if result["ready"]:
            self._set_state(
                user_id,
                broadcast_id,
                BroadcastState.READY,
                force=True,
                reason="preflight_local_recording_ready",
                validation=result,
            )
        return result

    def request_recording(self, user_id: str, broadcast_id: str, kind: str) -> dict:
        if (os.getenv("SHARED_SKY_RECORDING_STORAGE_URI") or "").strip():
            return super().request_recording(user_id, broadcast_id, kind)
        if kind != "programme" or not self._local_recording_ready():
            return super().request_recording(user_id, broadcast_id, kind)

        session = self._session(user_id, broadcast_id)
        settings = internal_media.settings
        assert settings.recording_root is not None
        try:
            root = (settings.recording_root / broadcast_id).resolve()
            root.mkdir(parents=True, exist_ok=True)
            placeholder = (root / f"{kind}.pending").resolve().as_uri()
        except OSError as exc:
            raise SharedSkyInternalMediaError(
                "Shared Sky local recording storage is not writable"
            ) from exc
        stamp = iso()
        with self.connect() as con:
            row = con.execute(
                "SELECT id FROM shared_sky_recordings WHERE broadcast_id=? AND kind=?",
                (broadcast_id, kind),
            ).fetchone()
            recording_id = str(row["id"]) if row else f"rec_{uuid4().hex}"
            con.execute(
                """INSERT INTO shared_sky_recordings(
                       id,broadcast_id,kind,state,storage_uri,reason_code,created_at,updated_at
                   ) VALUES(?,?,?,'requested',?,'',?,?)
                   ON CONFLICT(broadcast_id,kind) DO UPDATE SET
                       state='requested',storage_uri=excluded.storage_uri,
                       reason_code='',updated_at=excluded.updated_at""",
                (recording_id, broadcast_id, kind, placeholder, stamp, stamp),
            )

        if BroadcastState(session["state"]) in _ACTIVE:
            with self.connect() as con:
                existing = con.execute(
                    """SELECT id FROM shared_sky_internal_media_jobs
                       WHERE broadcast_id=? AND kind=? AND state='running' LIMIT 1""",
                    (broadcast_id, f"recording:{kind}"),
                ).fetchone()
            if not existing:
                try:
                    job = internal_media.start_recording(
                        broadcast_id=broadcast_id,
                        input_url=self.base.contribution_url(broadcast_id),
                        kind=kind,
                    )
                except SharedSkyInternalMediaError:
                    raise
                except OSError as exc:
                    raise SharedSkyInternalMediaError(
                        "Shared Sky local recording process could not be started"
                    ) from exc
                self._persist_media_job(user_id, job)
                with self.connect() as con:
                    con.execute(
                        """UPDATE shared_sky_recordings
                           SET state='recording',storage_uri=?,reason_code='',updated_at=?
                           WHERE broadcast_id=? AND kind=?""",
                        (Path(job["output_path"]).resolve().as_uri(), iso(), broadcast_id, kind),
                    )
        return self._recording_row(user_id, broadcast_id, kind)


__all__ = ["TransportLocalRecordingMixin"]
