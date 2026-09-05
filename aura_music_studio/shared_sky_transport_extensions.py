from __future__ import annotations

import json
import os
from datetime import timedelta
from uuid import uuid4

from .shared_sky_destination_adapters import (
    CapabilityState,
    ProviderOperationError,
    YouTubeLiveAdapter,
    validate_destination_url,
)
from .shared_sky_relay import SharedSkyRelayError, relay
from .shared_sky_security import SharedSkyVaultError
from .shared_sky_transport_models import BroadcastState, DestinationState, iso, jload, now


class ResumableYouTubeLiveAdapter(YouTubeLiveAdapter):
    """Reuse a previously-created YouTube broadcast/stream after relay failure."""

    def prepare(self, *, user_id: str, destination: dict, broadcast: dict, profile: dict) -> dict:
        existing = dict(profile.get("_existing_run") or {})
        broadcast_id = str(existing.get("provider_external_id") or "").strip()
        stream_id = str(existing.get("provider_stream_id") or "").strip()
        if not broadcast_id or not stream_id:
            return super().prepare(
                user_id=user_id,
                destination=destination,
                broadcast=broadcast,
                profile=profile,
            )

        record, blocker = self._account(user_id, destination)
        if blocker or not record:
            raise ProviderOperationError(
                blocker.reason_code if blocker else "youtube_oauth_missing",
                blocker.message if blocker else "YouTube OAuth unavailable",
                retryable=False,
            )
        token = self.vault.access_token(user_id, str(record["credential_id"]))
        payload = self._request(
            "GET",
            "liveStreams",
            token,
            params={"part": "cdn,status", "id": stream_id},
        )
        items = payload.get("items") or []
        stream = items[0] if isinstance(items, list) and items else {}
        info = (stream.get("cdn") or {}).get("ingestionInfo") or {}
        address = str(info.get("rtmpsIngestionAddress") or info.get("ingestionAddress") or "").strip()
        stream_name = str(info.get("streamName") or "").strip()
        if not address or not stream_name:
            raise ProviderOperationError(
                "provider_resume_incomplete",
                "YouTube did not return reusable ingest data for the existing stream",
                retryable=False,
            )
        return {
            "provider_broadcast_id": broadcast_id,
            "provider_stream_id": stream_id,
            "output_url": address.rstrip("/") + "/" + stream_name.lstrip("/"),
            "resumed_provider_resource": True,
        }


class TransportExtensionsMixin:
    """Compatibility hardening that composes over the core Chat 2 transport service."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.adapters["youtube"] = ResumableYouTubeLiveAdapter(self.db_path)
        self._extension_schema()

    def _extension_schema(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS shared_sky_destination_presets (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    destination_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, name),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_destination_presets_user
                ON shared_sky_destination_presets(user_id, updated_at DESC);
                """
            )

    def create_destination_preset(self, user_id: str, name: str, destination_ids: list[str]) -> dict:
        clean_name = (name or "").strip()
        if not clean_name or len(clean_name) > 120:
            raise ValueError("Destination preset name must contain 1 to 120 characters")
        ordered = list(dict.fromkeys(str(item).strip() for item in destination_ids if str(item).strip()))
        if not ordered or len(ordered) > 50:
            raise ValueError("Destination preset must contain between 1 and 50 destinations")
        for destination_id in ordered:
            self.base.destination(user_id, destination_id)
        stamp = iso()
        preset_id = f"preset_{uuid4().hex}"
        with self.connect() as con:
            existing = con.execute(
                "SELECT id FROM shared_sky_destination_presets WHERE user_id=? AND name=?",
                (user_id, clean_name),
            ).fetchone()
            if existing:
                preset_id = str(existing["id"])
                con.execute(
                    "UPDATE shared_sky_destination_presets SET destination_ids_json=?,updated_at=? "
                    "WHERE id=? AND user_id=?",
                    (json.dumps(ordered), stamp, preset_id, user_id),
                )
            else:
                con.execute(
                    "INSERT INTO shared_sky_destination_presets "
                    "(id,user_id,name,destination_ids_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (preset_id, user_id, clean_name, json.dumps(ordered), stamp, stamp),
                )
        return self.destination_preset(user_id, preset_id)

    def destination_preset(self, user_id: str, preset_id: str) -> dict:
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM shared_sky_destination_presets WHERE id=? AND user_id=?",
                (preset_id, user_id),
            ).fetchone()
        if not row:
            raise KeyError(preset_id)
        item = dict(row)
        item["destination_ids"] = jload(item.pop("destination_ids_json", "[]"), [])
        return item

    def destination_presets(self, user_id: str) -> list[dict]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM shared_sky_destination_presets WHERE user_id=? ORDER BY name,id",
                (user_id,),
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["destination_ids"] = jload(item.pop("destination_ids_json", "[]"), [])
            output.append(item)
        return output

    def apply_destination_preset(self, user_id: str, broadcast_id: str, preset_id: str) -> dict:
        broadcast = self.base.broadcast(user_id, broadcast_id)
        session = self._session(user_id, broadcast_id)
        if BroadcastState(session["state"]) in {
            BroadcastState.STARTING,
            BroadcastState.LIVE,
            BroadcastState.DEGRADED,
            BroadcastState.RECONNECTING,
            BroadcastState.STOPPING,
        }:
            raise ValueError("Destination presets cannot replace destinations during an active broadcast")
        preset = self.destination_preset(user_id, preset_id)
        for destination_id in preset["destination_ids"]:
            self.base.destination(user_id, destination_id)
        with self.connect() as con:
            con.execute(
                "UPDATE shared_sky_broadcasts SET destination_ids_json=?,updated_at=? WHERE id=? AND user_id=?",
                (json.dumps(preset["destination_ids"]), iso(), broadcast["id"], user_id),
            )
        self._sync_runs(user_id, broadcast_id)
        self.emit(broadcast_id, "destination_preset_applied", "ok")
        return self.status(user_id, broadcast_id)

    def preflight(self, user_id: str, broadcast_id: str) -> dict:
        """Allow partial-live startup when at least one independent delivery path is healthy."""
        result = super().preflight(user_id, broadcast_id)
        internal_ready = (
            result.get("internal_playback", {}).get("capability_state") == CapabilityState.READY
        )
        destination_ready = any(
            item.get("capability_state") == CapabilityState.READY
            for item in result.get("destinations", [])
        )
        if not internal_ready and not destination_ready:
            return result

        retained = []
        demoted = []
        for item in result.get("blocking_errors", []):
            scope = item.get("scope")
            code = item.get("code")
            destination_local = scope == "destination"
            external_runtime = internal_ready and (
                scope == "relay"
                or (scope == "ingest" and code == "ingest_endpoint_unconfigured")
            )
            if destination_local or external_runtime:
                warning = dict(item)
                warning["non_fatal_delivery_path_failure"] = True
                demoted.append(warning)
            else:
                retained.append(item)
        result["blocking_errors"] = retained
        result["warnings"] = list(result.get("warnings", [])) + demoted
        result["ready"] = not retained
        if result["ready"]:
            self._set_state(
                user_id,
                broadcast_id,
                BroadcastState.READY,
                force=True,
                reason="preflight_partial_ready" if demoted else "preflight_ready",
                validation=result,
            )
        return result

    def _start_destination(
        self,
        user_id: str,
        broadcast: dict,
        session: dict,
        destination_id: str,
    ) -> tuple[bool, dict | None]:
        """Persist provider IDs before relay start so reconnects can reuse remote resources."""
        destination = self.base.destination(user_id, destination_id)
        adapter = self._adapter(destination)
        if not adapter:
            return (False, {"destination_id": destination_id, "reason_code": "provider_unknown"})
        cap = adapter.capability(user_id=user_id, destination=destination)
        if cap.state != CapabilityState.READY:
            return (False, {"destination_id": destination_id, "reason_code": cap.reason_code})
        try:
            with self.connect() as con:
                existing_row = con.execute(
                    "SELECT * FROM shared_sky_destination_runs WHERE broadcast_id=? AND destination_id=?",
                    (broadcast["id"], destination_id),
                ).fetchone()
            existing = dict(existing_row) if existing_row else {}
            profile = dict(session.get("rendition_profile") or {})
            profile["_existing_run"] = {
                "provider_external_id": existing.get("provider_external_id"),
                "provider_stream_id": existing.get("provider_stream_id"),
            }
            prepared = adapter.prepare(
                user_id=user_id,
                destination=destination,
                broadcast=broadcast,
                profile=profile,
            )
            output_url = prepared.get("output_url") or self.base._destination_output_url(
                user_id, destination_id
            )
            validate_destination_url(str(output_url), resolve_dns=True)
            output_id = str(existing.get("output_id") or f"out_{uuid4().hex}")

            # The external IDs are durable before FFmpeg starts. If relay startup fails,
            # a retry can resume the same provider broadcast/stream instead of creating duplicates.
            with self.connect() as con:
                con.execute(
                    "UPDATE shared_sky_destination_runs SET provider_external_id=COALESCE(?,provider_external_id),"
                    "provider_stream_id=COALESCE(?,provider_stream_id),output_id=?,updated_at=? "
                    "WHERE broadcast_id=? AND destination_id=?",
                    (
                        prepared.get("provider_broadcast_id"),
                        prepared.get("provider_stream_id"),
                        output_id,
                        iso(),
                        broadcast["id"],
                        destination_id,
                    ),
                )

            relay.start_output(
                output_id=output_id,
                destination_id=destination_id,
                input_url=self.base.contribution_url(broadcast["id"]),
                output_url=str(output_url),
                passthrough=bool(broadcast["passthrough"]),
            )
            with self.connect() as con:
                con.execute(
                    "UPDATE shared_sky_destination_runs SET state='live',capability_state='ready',"
                    "next_retry_at=NULL,last_error_code='',last_error_safe='',"
                    "started_at=COALESCE(started_at,?),updated_at=? "
                    "WHERE broadcast_id=? AND destination_id=?",
                    (iso(), iso(), broadcast["id"], destination_id),
                )
            self.emit(broadcast["id"], "destination_live", "ok", destination_id=destination_id)
            return (True, None)
        except (SharedSkyRelayError, SharedSkyVaultError, ProviderOperationError, ValueError) as exc:
            code = getattr(exc, "code", "destination_start_failed")
            self._fail_destination(
                broadcast["id"],
                destination_id,
                code,
                str(exc),
                bool(getattr(exc, "retryable", True)),
            )
            return (False, {"destination_id": destination_id, "reason_code": code})

    def capacity_snapshot(self, user_id: str | None = None) -> dict:
        """Expose measured transport pressure without inventing host/media metrics."""
        active_states = ("starting", "live", "degraded", "reconnecting", "stopping")
        cutoff = iso(now() - timedelta(minutes=5))
        with self.connect() as con:
            session_where = "WHERE state IN (?,?,?,?,?)"
            session_args: list[object] = list(active_states)
            if user_id:
                session_where += " AND user_id=?"
                session_args.append(user_id)
            active_sessions = int(
                con.execute(
                    f"SELECT COUNT(*) FROM shared_sky_transport_sessions {session_where}",
                    tuple(session_args),
                ).fetchone()[0]
            )
            if user_id:
                run_owner = (
                    " AND broadcast_id IN (SELECT broadcast_id FROM shared_sky_transport_sessions WHERE user_id=?)"
                )
                run_args = (user_id,)
            else:
                run_owner = ""
                run_args = ()
            runs = {
                state: int(
                    con.execute(
                        f"SELECT COUNT(*) FROM shared_sky_destination_runs WHERE state=?{run_owner}",
                        (state, *run_args),
                    ).fetchone()[0]
                )
                for state in ("live", "reconnecting", "failed")
            }
            fanout_rows = con.execute(
                "SELECT COUNT(*) AS n FROM shared_sky_destination_runs WHERE broadcast_id IN "
                "(SELECT broadcast_id FROM shared_sky_transport_sessions WHERE state IN (?,?,?,?,?)"
                + (" AND user_id=?" if user_id else "")
                + ") GROUP BY broadcast_id",
                (*active_states, *((user_id,) if user_id else ())),
            ).fetchall()
            active_recordings = int(
                con.execute(
                    "SELECT COUNT(*) FROM shared_sky_recordings WHERE state IN ('requested','recording')"
                    + (
                        " AND broadcast_id IN (SELECT broadcast_id FROM shared_sky_transport_sessions WHERE user_id=?)"
                        if user_id
                        else ""
                    ),
                    (user_id,) if user_id else (),
                ).fetchone()[0]
            )
            recent_events = con.execute(
                "SELECT event_type,metrics_json FROM shared_sky_transport_events WHERE created_at>=?"
                + (
                    " AND broadcast_id IN (SELECT broadcast_id FROM shared_sky_transport_sessions WHERE user_id=?)"
                    if user_id
                    else ""
                ),
                (cutoff, user_id) if user_id else (cutoff,),
            ).fetchall()
            media_table = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='shared_sky_media_nodes'"
            ).fetchone()
            media_capacity = None
            if media_table:
                row = con.execute(
                    "SELECT COALESCE(SUM(capacity),0),COALESCE(SUM(active_sessions),0),"
                    "SUM(CASE WHEN healthy=1 THEN 1 ELSE 0 END) FROM shared_sky_media_nodes"
                ).fetchone()
                media_capacity = {
                    "declared_capacity": int(row[0] or 0),
                    "declared_active_sessions": int(row[1] or 0),
                    "healthy_nodes": int(row[2] or 0),
                }

        queue_values = []
        buffer_values = []
        reconnect_events = 0
        for row in recent_events:
            reconnect_events += str(row["event_type"]) == "destination_failure"
            metrics = jload(row["metrics_json"], {})
            if isinstance(metrics.get("queue_depth"), (int, float)):
                queue_values.append(metrics["queue_depth"])
            if isinstance(metrics.get("buffer_ms"), (int, float)):
                buffer_values.append(metrics["buffer_ms"])
        relay_health = relay.health()
        return {
            "scope": "member" if user_id else "global",
            "active_broadcast_sessions": active_sessions,
            "destination_runs": runs,
            "max_active_fanout": max((int(row["n"]) for row in fanout_rows), default=0),
            "active_recordings": active_recordings,
            "destination_failure_events_last_5m": reconnect_events,
            "max_reported_queue_depth_last_5m": max(queue_values, default=None),
            "max_reported_buffer_ms_last_5m": max(buffer_values, default=None),
            "relay_active_outputs": relay_health.active_outputs,
            "relay_runtime_mode": relay_health.runtime_mode,
            "media_node_capacity": media_capacity,
            "cpu_gpu_values_fabricated": False,
        }


__all__ = ["ResumableYouTubeLiveAdapter", "TransportExtensionsMixin"]
