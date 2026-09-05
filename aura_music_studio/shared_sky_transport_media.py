from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from .shared_sky_destination_adapters import CapabilityState
from .shared_sky_internal_media import SharedSkyInternalMediaError, internal_media
from .shared_sky_relay import SharedSkyRelayError
from .shared_sky_transport_models import BroadcastState, PreflightBlocked, iso


_ACTIVE = {
    BroadcastState.STARTING,
    BroadcastState.LIVE,
    BroadcastState.DEGRADED,
    BroadcastState.RECONNECTING,
}


class TransportMediaMixin:
    """First-party HLS/recording runtime integration for the transport domain."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._media_schema()

    def _media_schema(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS shared_sky_internal_media_jobs (
                    id TEXT PRIMARY KEY,
                    broadcast_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    rendition TEXT,
                    state TEXT NOT NULL,
                    pid INTEGER,
                    worker_mode TEXT NOT NULL DEFAULT 'web-process-media-supervisor',
                    output_path TEXT NOT NULL DEFAULT '',
                    reason_code TEXT NOT NULL DEFAULT '',
                    started_at TEXT,
                    ended_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_internal_media_jobs_broadcast
                ON shared_sky_internal_media_jobs(broadcast_id,state,kind);
                """
            )

    def _playback_capability(self) -> tuple[CapabilityState, str, str]:
        base = (os.getenv("SHARED_SKY_PLAYBACK_BASE_URL") or "").strip()
        secret = (os.getenv("SHARED_SKY_PLAYBACK_SIGNING_SECRET") or "").strip()
        if not base or not secret:
            return (
                CapabilityState.CREDENTIALS_MISSING,
                "internal_playback_unconfigured",
                "Internal playback origin/signing is not configured",
            )
        if not base.startswith("https://") and os.getenv(
            "SHARED_SKY_ALLOW_INSECURE_PLAYBACK", "0"
        ).strip().lower() not in {"1", "true", "yes", "on"}:
            return (
                CapabilityState.RUNTIME_UNAVAILABLE,
                "internal_playback_https_required",
                "Internal playback origin must use HTTPS",
            )
        health = internal_media.health()
        if not health.enabled or not health.configured:
            return (
                CapabilityState.RUNTIME_UNAVAILABLE,
                "internal_media_runtime_unconfigured",
                "The first-party Shared Sky media runtime is not enabled/configured",
            )
        if not health.ffmpeg_available:
            return (
                CapabilityState.RUNTIME_UNAVAILABLE,
                "internal_media_ffmpeg_unavailable",
                "FFmpeg is unavailable for first-party Shared Sky playback",
            )
        if not (os.getenv("SHARED_SKY_INGEST_BASE_URL") or "").strip():
            return (
                CapabilityState.RUNTIME_UNAVAILABLE,
                "internal_media_ingest_unconfigured",
                "Internal playback requires a configured contribution-ingest endpoint",
            )
        return (
            CapabilityState.READY,
            "ready",
            "First-party Shared Sky HLS media runtime is ready",
        )

    def playback(self, user_id: str, broadcast_id: str, ttl: int=120) -> dict:
        payload = super().playback(user_id, broadcast_id, ttl=ttl)
        if payload.get("capability_state") == CapabilityState.READY:
            payload["mode"] = "hls"
            payload["latency_profile"] = "short-segment-live"
            payload["runtime_mode"] = internal_media.health().runtime_mode
        return payload

    def _persist_media_job(self, user_id: str, job: dict, *, state: str="running") -> None:
        stamp = iso()
        with self.connect() as con:
            con.execute(
                """INSERT INTO shared_sky_internal_media_jobs(
                       id,broadcast_id,user_id,kind,rendition,state,pid,output_path,
                       reason_code,started_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       state=excluded.state,pid=excluded.pid,output_path=excluded.output_path,
                       reason_code=excluded.reason_code,updated_at=excluded.updated_at""",
                (
                    job["job_id"],
                    job["broadcast_id"],
                    user_id,
                    job["kind"],
                    job.get("rendition"),
                    state,
                    job.get("pid"),
                    job.get("output_path") or "",
                    "",
                    stamp,
                    stamp,
                ),
            )

    def _mark_jobs_ended(self, broadcast_id: str, job_ids: list[str], *, reason: str) -> None:
        if not job_ids:
            return
        marks = ",".join("?" for _ in job_ids)
        stamp = iso()
        with self.connect() as con:
            con.execute(
                f"""UPDATE shared_sky_internal_media_jobs
                    SET state='ended',reason_code=?,ended_at=COALESCE(ended_at,?),updated_at=?
                    WHERE broadcast_id=? AND id IN ({marks})""",
                (reason[:80], stamp, stamp, broadcast_id, *job_ids),
            )

    def _start_internal_delivery(self, user_id: str, broadcast: dict, session: dict) -> bool:
        if not session.get("internal_playback"):
            return False
        jobs = internal_media.start_hls(
            broadcast_id=broadcast["id"],
            input_url=self.base.contribution_url(broadcast["id"]),
            profile=session.get("rendition_profile") or {},
        )
        if not jobs:
            raise SharedSkyInternalMediaError("No Shared Sky HLS rendition could be started")
        for job in jobs:
            self._persist_media_job(user_id, job)
        self.emit(broadcast["id"], "internal_playback_started", "ok")
        return True

    def _internal_delivery_active(self, user_id: str, broadcast_id: str) -> bool:
        self._session(user_id, broadcast_id)
        with self.connect() as con:
            rows = con.execute(
                """SELECT id FROM shared_sky_internal_media_jobs
                   WHERE broadcast_id=? AND user_id=? AND kind='hls' AND state='running'""",
                (broadcast_id, user_id),
            ).fetchall()
        if not rows:
            return False
        active = False
        for row in rows:
            runtime = internal_media.state(str(row["id"]))
            active = active or bool(runtime.get("running"))
        return active

    def _stop_internal_delivery(self, user_id: str, broadcast_id: str, *, reason: str) -> None:
        self._session(user_id, broadcast_id)
        stopped = internal_media.stop_broadcast(broadcast_id, kinds={"hls"})
        self._mark_jobs_ended(
            broadcast_id,
            [str(item["job_id"]) for item in stopped],
            reason=reason,
        )
        if stopped:
            self.emit(broadcast_id, "internal_playback_stopped", reason)

    def start(self, user_id: str, broadcast_id: str, key: str) -> dict:
        self.rate_limit(user_id, "start", limit=10)

        def run():
            check = self.preflight(user_id, broadcast_id)
            if not check["ready"]:
                raise PreflightBlocked(check)
            session = self._set_state(
                user_id,
                broadcast_id,
                BroadcastState.STARTING,
                reason="start_requested",
            )
            broadcast = self.base.broadcast(user_id, broadcast_id)
            started = 0
            failures: list[dict] = []
            for destination_id in broadcast["destination_ids"]:
                ok, failure = self._start_destination(user_id, broadcast, session, destination_id)
                started += int(ok)
                if failure:
                    failures.append(failure)

            internal = False
            if session["internal_playback"]:
                try:
                    internal = self._start_internal_delivery(user_id, broadcast, session)
                except SharedSkyInternalMediaError as exc:
                    failures.append(
                        {
                            "scope": "internal_playback",
                            "reason_code": "internal_playback_start_failed",
                        }
                    )
                    self.emit(
                        broadcast_id,
                        "internal_playback_failure",
                        "internal_playback_start_failed",
                    )

            if not started and not internal:
                self._set_state(
                    user_id,
                    broadcast_id,
                    BroadcastState.FAILED,
                    force=True,
                    reason="no_delivery_path_started",
                )
                raise SharedSkyRelayError("No delivery path could be started")

            self._set_state(
                user_id,
                broadcast_id,
                BroadcastState.DEGRADED if failures else BroadcastState.LIVE,
                force=True,
                reason="partial_live" if failures else "live",
            )
            if session["recording_enabled"]:
                try:
                    self.request_recording(user_id, broadcast_id, "programme")
                except (RuntimeError, SharedSkyInternalMediaError):
                    failures.append(
                        {"scope": "recording", "reason_code": "recording_start_failed"}
                    )
                    self.emit(broadcast_id, "recording_failure", "recording_start_failed")
                    self._set_state(
                        user_id,
                        broadcast_id,
                        BroadcastState.DEGRADED,
                        force=True,
                        reason="recording_start_failed",
                    )
            return {
                "broadcast": self.status(user_id, broadcast_id),
                "partial": bool(failures),
                "failures": failures,
                "started_destinations": started,
                "internal_playback": internal,
            }

        return self._idem(user_id, broadcast_id, "start", key, {}, run)

    def request_recording(self, user_id: str, broadcast_id: str, kind: str) -> dict:
        recording = super().request_recording(user_id, broadcast_id, kind)
        settings = internal_media.settings
        session = self._session(user_id, broadcast_id)
        if (
            kind == "programme"
            and settings.enabled
            and settings.recording_root
            and BroadcastState(session["state"]) in _ACTIVE
        ):
            with self.connect() as con:
                existing = con.execute(
                    """SELECT id FROM shared_sky_internal_media_jobs
                       WHERE broadcast_id=? AND kind=? AND state='running' LIMIT 1""",
                    (broadcast_id, f"recording:{kind}"),
                ).fetchone()
            if not existing:
                job = internal_media.start_recording(
                    broadcast_id=broadcast_id,
                    input_url=self.base.contribution_url(broadcast_id),
                    kind=kind,
                )
                self._persist_media_job(user_id, job)
                with self.connect() as con:
                    con.execute(
                        """UPDATE shared_sky_recordings
                           SET state='recording',storage_uri=?,reason_code='',updated_at=?
                           WHERE broadcast_id=? AND kind=?""",
                        (
                            Path(job["output_path"]).resolve().as_uri(),
                            iso(),
                            broadcast_id,
                            kind,
                        ),
                    )
                recording = self._recording_row(user_id, broadcast_id, kind)
        return recording

    def _recording_row(self, user_id: str, broadcast_id: str, kind: str) -> dict:
        self._session(user_id, broadcast_id)
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM shared_sky_recordings WHERE broadcast_id=? AND kind=?",
                (broadcast_id, kind),
            ).fetchone()
        if not row:
            raise KeyError(kind)
        return self._recording(dict(row))

    def _stop_recording_delivery(self, user_id: str, broadcast_id: str, *, reason: str) -> None:
        self._session(user_id, broadcast_id)
        stopped = internal_media.stop_broadcast(broadcast_id, kinds={"recording"})
        for item in stopped:
            job_id = str(item["job_id"])
            self._mark_jobs_ended(broadcast_id, [job_id], reason=reason)
            kind = str(item.get("kind") or "recording:programme").split(":", 1)[-1]
            metadata = internal_media.recording_metadata(item.get("output_path") or "")
            if metadata.get("exists") and int(metadata.get("size_bytes") or 0) > 0:
                with self.connect() as con:
                    con.execute(
                        """UPDATE shared_sky_recordings
                           SET state='complete',asset_id=COALESCE(asset_id,?),
                               checksum_sha256=?,size_bytes=?,duration_ms=?,reason_code=?,updated_at=?
                           WHERE broadcast_id=? AND kind=?""",
                        (
                            f"localrec_{uuid4().hex}",
                            metadata.get("checksum_sha256"),
                            metadata.get("size_bytes"),
                            metadata.get("duration_ms"),
                            reason[:80],
                            iso(),
                            broadcast_id,
                            kind,
                        ),
                    )
            else:
                with self.connect() as con:
                    con.execute(
                        """UPDATE shared_sky_recordings
                           SET state='incomplete',reason_code=?,updated_at=?
                           WHERE broadcast_id=? AND kind=?""",
                        ("local_recording_missing", iso(), broadcast_id, kind),
                    )

    def reconcile_media_jobs(self, user_id: str, broadcast_id: str) -> list[dict]:
        self._session(user_id, broadcast_id)
        with self.connect() as con:
            rows = [
                dict(row)
                for row in con.execute(
                    """SELECT * FROM shared_sky_internal_media_jobs
                       WHERE broadcast_id=? AND user_id=? ORDER BY kind,rendition,id""",
                    (broadcast_id, user_id),
                ).fetchall()
            ]
        for row in rows:
            if row["state"] != "running":
                continue
            runtime = internal_media.state(row["id"])
            if runtime["running"]:
                continue
            reason = (
                "media_process_exited"
                if runtime.get("managed")
                else "media_worker_process_not_owned"
            )
            state = "failed" if runtime.get("managed") else "orphaned"
            with self.connect() as con:
                con.execute(
                    """UPDATE shared_sky_internal_media_jobs
                       SET state=?,reason_code=?,ended_at=?,updated_at=? WHERE id=?""",
                    (state, reason, iso(), iso(), row["id"]),
                )
        with self.connect() as con:
            return [
                dict(row)
                for row in con.execute(
                    """SELECT id,broadcast_id,kind,rendition,state,pid,worker_mode,
                              reason_code,started_at,ended_at,updated_at
                       FROM shared_sky_internal_media_jobs
                       WHERE broadcast_id=? AND user_id=? ORDER BY kind,rendition,id""",
                    (broadcast_id, user_id),
                ).fetchall()
            ]

    def reconcile(self, user_id: str, broadcast_id: str) -> dict:
        session = self._session(user_id, broadcast_id)
        payload = super().reconcile(user_id, broadcast_id)
        current = BroadcastState(payload["session"]["state"])
        if current in {
            BroadcastState.LIVE,
            BroadcastState.DEGRADED,
            BroadcastState.RECONNECTING,
        } and session.get("internal_playback"):
            self.reconcile_media_jobs(user_id, broadcast_id)
            if not self._internal_delivery_active(user_id, broadcast_id):
                live_external = any(
                    item.get("state") == "live" for item in payload.get("destinations", [])
                )
                target = BroadcastState.DEGRADED if live_external else BroadcastState.FAILED
                self._set_state(
                    user_id,
                    broadcast_id,
                    target,
                    force=True,
                    reason="internal_playback_process_exited",
                )
                payload = self.status(user_id, broadcast_id)
        return payload

    def status(self, user_id: str, broadcast_id: str) -> dict:
        payload = super().status(user_id, broadcast_id)
        payload["internal_media"] = {
            "health": internal_media.health().__dict__,
            "jobs": self.reconcile_media_jobs(user_id, broadcast_id),
        }
        return payload


__all__ = ["TransportMediaMixin"]
