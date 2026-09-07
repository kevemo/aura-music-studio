from __future__ import annotations

import os
import time
from pathlib import Path

from .shared_sky_internal_media import SharedSkyInternalMediaError, internal_media
from .shared_sky_transport_models import iso


class TransportMediaStartupReadinessMixin:
    """Require viewer-playable evidence before the built-in HLS path counts as started.

    The process-local supervisor is implementation-specific. Dedicated Chat 10 media workers may
    satisfy their own equivalent readiness contract before returning from start_hls; this mixin
    only probes jobs that are actually owned by the built-in supervisor in this process.
    """

    @staticmethod
    def _hls_startup_timeout_seconds() -> float:
        try:
            value = float(os.getenv("SHARED_SKY_HLS_STARTUP_TIMEOUT_SECONDS", "10"))
        except ValueError:
            value = 10.0
        return max(2.0, min(30.0, value))

    def _start_internal_delivery(self, user_id: str, broadcast: dict, session: dict) -> bool:
        try:
            started = super()._start_internal_delivery(user_id, broadcast, session)
        except SharedSkyInternalMediaError:
            raise
        except OSError as exc:
            raise SharedSkyInternalMediaError(
                "Shared Sky internal media runtime could not create or start its local media resources"
            ) from exc
        if not started:
            return False

        with self.connect() as con:
            rows = [
                dict(row)
                for row in con.execute(
                    """SELECT id,output_path FROM shared_sky_internal_media_jobs
                       WHERE broadcast_id=? AND user_id=? AND kind='hls' AND state='running'
                       ORDER BY rendition,id""",
                    (broadcast["id"], user_id),
                ).fetchall()
            ]
        if not rows:
            raise SharedSkyInternalMediaError("Shared Sky HLS start produced no durable media jobs")

        # Tests and future dedicated-worker adapters can return durable jobs without registering
        # them in this process-local supervisor. Those adapters own their own equivalent readiness
        # acknowledgement. For built-in jobs, require actual playlist evidence here.
        with internal_media._lock:  # noqa: SLF001 - same package, deliberate built-in ownership probe
            locally_managed = {str(job_id) for job_id in internal_media._jobs}  # noqa: SLF001
        owned = [row for row in rows if str(row["id"]) in locally_managed]
        if not owned:
            return True

        deadline = time.monotonic() + self._hls_startup_timeout_seconds()
        failure_code = "internal_playback_startup_timeout"
        while True:
            all_ready = True
            for row in owned:
                state = internal_media.state(str(row["id"]))
                if not state.get("running"):
                    failure_code = "internal_playback_process_exited_before_ready"
                    all_ready = False
                    deadline = 0.0
                    break
                output = Path(str(row.get("output_path") or ""))
                try:
                    output_ready = output.exists() and output.is_file() and output.stat().st_size > 0
                except OSError:
                    output_ready = False
                if not output_ready:
                    all_ready = False
            if all_ready:
                self.emit(broadcast["id"], "internal_playback_ready", "playlist_ready")
                return True
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)

        stopped = internal_media.stop_broadcast(broadcast["id"], kinds={"hls"})
        stopped_ids = [str(item.get("job_id") or "") for item in stopped if item.get("job_id")]
        stamp = iso()
        with self.connect() as con:
            if stopped_ids:
                marks = ",".join("?" for _ in stopped_ids)
                con.execute(
                    f"""UPDATE shared_sky_internal_media_jobs
                        SET state='failed',reason_code=?,ended_at=COALESCE(ended_at,?),updated_at=?
                        WHERE broadcast_id=? AND id IN ({marks})""",
                    (failure_code, stamp, stamp, broadcast["id"], *stopped_ids),
                )
            else:
                con.execute(
                    """UPDATE shared_sky_internal_media_jobs
                       SET state='failed',reason_code=?,ended_at=COALESCE(ended_at,?),updated_at=?
                       WHERE broadcast_id=? AND user_id=? AND kind='hls' AND state='running'""",
                    (failure_code, stamp, stamp, broadcast["id"], user_id),
                )
        raise SharedSkyInternalMediaError(
            "Shared Sky HLS did not become viewer-playable before the startup deadline"
        )


__all__ = ["TransportMediaStartupReadinessMixin"]
