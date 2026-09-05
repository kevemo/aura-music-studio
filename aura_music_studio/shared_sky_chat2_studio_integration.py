from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .esp_niche import require_esp_hub_member
from .shared_sky_control_room import (
    StudioInvariantError,
    StudioTransportError,
    TransportCommit,
    studio,
    studio_repo,
    validate_no_secrets,
)
from .shared_sky_streaming_studios import shared_sky
from .shared_sky_transport_domain import transport

router = APIRouter(tags=["Shared Sky Chat 2 Studio Integration"])

_ACTIVE_TRANSPORT_STATES = {"starting", "live", "degraded", "reconnecting", "stopping"}
_ALLOWED_RECORDING_KINDS = {"programme", "clean_feed", "isolated_source", "audio_tracks"}


class TransportBindRequest(BaseModel):
    expected_studio_version: int = Field(ge=1)
    internal_playback: bool | None = None
    recording_enabled: bool | None = None


class RecordingRequest(BaseModel):
    kind: Literal["programme", "clean_feed", "isolated_source", "audio_tracks"] = "programme"


def _profile_rendition(profile: dict[str, Any]) -> dict[str, Any]:
    width = int(profile.get("width") or 1920)
    height = int(profile.get("height") or 1080)
    orientation = str(profile.get("orientation") or "landscape")
    if orientation == "portrait":
        label = "1080x1920p30"
        shorthand = {"portrait": label}
    elif orientation == "square":
        label = "1080x1080p30"
        shorthand = {"square": label}
    else:
        label = "1080p30"
        shorthand = {"landscape": label}
    return {
        **shorthand,
        "studio_profile": {
            "width": width,
            "height": height,
            "fps": 30,
            "orientation": orientation,
        },
        "renditions": [
            {
                "id": f"studio-{width}x{height}-30",
                "width": width,
                "height": height,
                "fps": 30,
            }
        ],
    }


class CanonicalChat2StudioTransportAdapter:
    """Chat 3 composition -> canonical Chat 2 stable programme-source handoff.

    Chat 3 owns which scene is Programme. Chat 2 owns whether the resulting continuous
    ``studio_program`` feed is configured, live, degraded, recorded or delivered. No scene
    snapshot is copied into Chat 2's transport tables and no destination/recording state is
    inferred locally.
    """

    def __init__(self, provider: Any = transport):
        self.provider = provider

    def _source_ref(self, project_id: str) -> str:
        return f"studio://{project_id}/programme/main"

    def _transport_status(self, user_id: str, broadcast_id: str, *, reconcile: bool = False) -> dict[str, Any]:
        resolver = getattr(self.provider, "reconcile", None) if reconcile else None
        if callable(resolver):
            result = resolver(user_id, broadcast_id)
        else:
            result = self.provider.status(user_id, broadcast_id)
        if not isinstance(result, dict):
            raise StudioTransportError("Chat 2 transport returned invalid authoritative status")
        return result

    def _existing_source(self, user_id: str, project_id: str) -> dict[str, Any] | None:
        source_ref = self._source_ref(project_id)
        connector = getattr(self.provider, "connect", None)
        if callable(connector):
            with connector() as con:
                row = con.execute(
                    "SELECT id FROM shared_sky_programme_sources "
                    "WHERE user_id=? AND project_id=? AND source_type='studio_program' "
                    "AND source_ref=? ORDER BY updated_at DESC LIMIT 1",
                    (user_id, project_id, source_ref),
                ).fetchone()
            if row:
                return self.provider.source(user_id, str(row["id"]))
        return None

    def _bound_source(
        self,
        user_id: str,
        broadcast_id: str,
        *,
        reconcile: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        status = self._transport_status(user_id, broadcast_id, reconcile=reconcile)
        session = dict(status.get("session") or {})
        source = None
        source_id = session.get("source_id")
        if source_id:
            try:
                source = self.provider.source(user_id, str(source_id))
            except KeyError:
                source = None
        return status, source

    @staticmethod
    def _source_matches(source: dict[str, Any] | None, project_id: str) -> bool:
        return bool(
            source
            and source.get("project_id") == project_id
            and source.get("source_type") == "studio_program"
            and source.get("state") == "ready"
        )

    def bind(
        self,
        user_id: str,
        broadcast_id: str,
        project_id: str,
        profile: dict[str, Any],
        *,
        internal_playback: bool | None = None,
        recording_enabled: bool | None = None,
    ) -> dict[str, Any]:
        broadcast = shared_sky.broadcast(user_id, broadcast_id)
        if broadcast.get("project_id") != project_id:
            raise StudioInvariantError("Broadcast does not belong to this Studio project")

        current, bound = self._bound_source(user_id, broadcast_id)
        transport_session = dict(current.get("session") or {})
        state = str(transport_session.get("state") or "draft")
        active = state in _ACTIVE_TRANSPORT_STATES
        desired_profile = _profile_rendition(profile)
        use_internal = (
            bool(transport_session.get("internal_playback", True))
            if internal_playback is None
            else bool(internal_playback)
        )
        use_recording = (
            bool(transport_session.get("recording_enabled", False))
            if recording_enabled is None
            else bool(recording_enabled)
        )

        if self._source_matches(bound, project_id):
            if active:
                return {
                    "source": bound,
                    "transport": current,
                    "created": False,
                    "configured": True,
                    "reconfigured": False,
                    "authoritative": True,
                }
            configuration_matches = (
                bool(transport_session.get("internal_playback", True)) == use_internal
                and bool(transport_session.get("recording_enabled", False)) == use_recording
                and dict(transport_session.get("rendition_profile") or {}) == desired_profile
            )
            if configuration_matches:
                return {
                    "source": bound,
                    "transport": current,
                    "created": False,
                    "configured": True,
                    "reconfigured": False,
                    "authoritative": True,
                }
            configured = self.provider.configure(
                user_id,
                broadcast_id,
                source_id=str(bound["id"]),
                internal_playback=use_internal,
                rendition_profile=desired_profile,
                recording_enabled=use_recording,
                ingest_session_id=None,
            )
            return {
                "source": bound,
                "transport": configured,
                "created": False,
                "configured": True,
                "reconfigured": True,
                "authoritative": True,
            }

        if active:
            raise StudioTransportError(
                "Active Chat 2 transport is not bound to this Studio programme source; "
                "stop/reconfigure the broadcast before changing its programme source"
            )

        source = self._existing_source(user_id, project_id)
        if not source:
            source = self.provider.register_source(
                user_id,
                project_id,
                "studio_program",
                self._source_ref(project_id),
                state="ready",
                capabilities={
                    "landscape": True,
                    "portrait": True,
                    "square": True,
                    "audio": True,
                    "composition_authority": "chat3",
                    "schema_version": 1,
                },
            )
            created = True
        else:
            created = False

        configured = self.provider.configure(
            user_id,
            broadcast_id,
            source_id=str(source["id"]),
            internal_playback=use_internal,
            rendition_profile=desired_profile,
            recording_enabled=use_recording,
            ingest_session_id=None,
        )
        return {
            "source": source,
            "transport": configured,
            "created": created,
            "configured": True,
            "reconfigured": False,
            "authoritative": True,
        }

    def status(self, user_id: str, broadcast_id: str | None) -> dict[str, Any]:
        if not broadcast_id:
            return {
                "state": "offline",
                "authoritative": True,
                "programme_commit_supported": True,
                "programme_source_bound": False,
                "source": "studio",
            }
        broadcast = shared_sky.broadcast(user_id, broadcast_id)
        current, source = self._bound_source(user_id, broadcast_id, reconcile=True)
        transport_session = dict(current.get("session") or {})
        state = str(transport_session.get("state") or broadcast.get("state") or "unknown")
        bound = self._source_matches(source, str(broadcast["project_id"]))
        active = state in _ACTIVE_TRANSPORT_STATES
        return {
            "state": state,
            "authoritative": True,
            "programme_commit_supported": (not active) or bound,
            "programme_source_bound": bound,
            "source_id": source.get("id") if source else None,
            "correlation_id": transport_session.get("correlation_id"),
            "trace_id": transport_session.get("trace_id"),
            "recordings": list(current.get("recordings") or []),
            "destinations": list(current.get("destinations") or []),
            "playback": current.get("playback") or {},
            "relay": current.get("relay") or {},
            "source": "chat2_transport_domain",
        }

    def preflight(
        self,
        user_id: str,
        broadcast_id: str,
        project_id: str,
        profile: dict[str, Any],
        *,
        internal_playback: bool | None = None,
        recording_enabled: bool | None = None,
    ) -> dict[str, Any]:
        binding = self.bind(
            user_id,
            broadcast_id,
            project_id,
            profile,
            internal_playback=internal_playback,
            recording_enabled=recording_enabled,
        )
        result = self.provider.preflight(user_id, broadcast_id)
        return {"binding": binding, "preflight": result, "authoritative": True}

    def commit_programme(
        self,
        user_id: str,
        broadcast_id: str | None,
        snapshot: dict[str, Any],
        correlation_id: str,
    ) -> TransportCommit:
        validate_no_secrets(snapshot)
        if not broadcast_id:
            return TransportCommit(
                True,
                True,
                "offline_programme",
                correlation_id=correlation_id,
            )
        broadcast = shared_sky.broadcast(user_id, broadcast_id)
        current, source = self._bound_source(user_id, broadcast_id)
        transport_session = dict(current.get("session") or {})
        state = str(transport_session.get("state") or broadcast.get("state") or "unknown")
        active = state in _ACTIVE_TRANSPORT_STATES
        if active and not self._source_matches(source, str(broadcast["project_id"])):
            return TransportCommit(
                False,
                True,
                state,
                "Active Chat 2 transport is not bound to the Chat 3 studio_program source",
                correlation_id,
            )
        if not active and not self._source_matches(source, str(broadcast["project_id"])):
            try:
                self.bind(
                    user_id,
                    broadcast_id,
                    str(broadcast["project_id"]),
                    dict(snapshot.get("profile") or {}),
                )
            except (StudioInvariantError, StudioTransportError, KeyError, ValueError, RuntimeError) as exc:
                return TransportCommit(False, True, state, str(exc), correlation_id)
            current, source = self._bound_source(user_id, broadcast_id)
            transport_session = dict(current.get("session") or {})
            state = str(transport_session.get("state") or state)
        if not self._source_matches(source, str(broadcast["project_id"])):
            return TransportCommit(
                False,
                True,
                state,
                "Chat 2 did not confirm the stable Studio programme source binding",
                correlation_id,
            )
        emitter = getattr(self.provider, "emit", None)
        if callable(emitter):
            emitter(
                broadcast_id,
                "studio_programme_committed",
                "ok",
                {"scene_switch": 1},
            )
        return TransportCommit(True, True, state, correlation_id=correlation_id)

    def recording_capabilities(self, user_id: str, broadcast_id: str | None) -> dict[str, Any]:
        if not broadcast_id:
            return {
                "supported": False,
                "state": "unavailable",
                "reason": "Recording requires a broadcast session",
            }
        current = self._transport_status(user_id, broadcast_id, reconcile=True)
        recordings = list(current.get("recordings") or [])
        programme = next((row for row in recordings if row.get("kind") == "programme"), None)
        return {
            "supported": True,
            "authoritative": True,
            "state": str((programme or {}).get("state") or "idle"),
            "programme": programme,
            "recordings": recordings,
            "supported_kinds": sorted(_ALLOWED_RECORDING_KINDS),
            "manual_stop_supported": callable(getattr(self.provider, "stop_recording", None)),
        }


class CanonicalChat2RecordingActions:
    def __init__(self, provider: Any = transport):
        self.provider = provider

    def action(
        self,
        user_id: str,
        broadcast_id: str | None,
        action: Literal["start", "stop"],
        kind: str = "programme",
    ) -> dict[str, Any]:
        if not broadcast_id:
            raise StudioTransportError("Recording requires a broadcast session")
        if kind not in _ALLOWED_RECORDING_KINDS:
            raise StudioInvariantError("Unsupported Shared Sky recording kind")
        if action == "start":
            result = self.provider.request_recording(user_id, broadcast_id, kind)
            return {"supported": True, "authoritative": True, **result}
        stopper = getattr(self.provider, "stop_recording", None)
        if not callable(stopper):
            raise StudioTransportError(
                "Chat 2 does not expose standalone recording stop; recording finalization is "
                "owned by the recording writer/broadcast lifecycle"
            )
        result = stopper(user_id, broadcast_id, kind)
        if not isinstance(result, dict):
            raise StudioTransportError("Chat 2 recording stop returned invalid authoritative state")
        return {"supported": True, "authoritative": True, **result}


chat2_studio_transport = CanonicalChat2StudioTransportAdapter(transport)
chat2_recording_actions = CanonicalChat2RecordingActions(transport)


def _member(request: Request):
    return require_esp_hub_member(request)


def _raise(exc: Exception) -> None:
    if isinstance(exc, KeyError):
        raise HTTPException(404, "Shared Sky transport/studio resource not found") from exc
    if isinstance(exc, StudioTransportError):
        raise HTTPException(503, str(exc)) from exc
    if isinstance(exc, StudioInvariantError):
        raise HTTPException(400, str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(400, str(exc)) from exc
    raise HTTPException(500, "Shared Sky Chat 2 Studio integration failed") from exc


@router.get("/shared-sky/studio/api/sessions/{session_id}/transport/status")
def studio_transport_status(session_id: str, request: Request):
    member, _ = _member(request)
    try:
        session = studio_repo.get_session(member.user_id, session_id)
        return chat2_studio_transport.status(member.user_id, session.get("broadcast_id"))
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/transport/bind")
def bind_studio_transport(session_id: str, body: TransportBindRequest, request: Request):
    member, _ = _member(request)
    try:
        session = studio_repo.get_session(member.user_id, session_id)
        if int(session["version"]) != body.expected_studio_version:
            raise StudioInvariantError("Studio state changed; refresh before binding transport")
        broadcast_id = session.get("broadcast_id")
        if not broadcast_id:
            raise StudioInvariantError("Bind a broadcast to this Studio session first")
        result = chat2_studio_transport.bind(
            member.user_id,
            str(broadcast_id),
            str(session["project_id"]),
            dict(session.get("profile") or {}),
            internal_playback=body.internal_playback,
            recording_enabled=body.recording_enabled,
        )
        shared_sky.event(
            member.user_id,
            str(broadcast_id),
            "studio_transport_bound",
            {"session_id": session_id, "source_id": result["source"]["id"]},
        )
        return result
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/transport/preflight")
def preflight_studio_transport(session_id: str, body: TransportBindRequest, request: Request):
    member, _ = _member(request)
    try:
        session = studio_repo.get_session(member.user_id, session_id)
        if int(session["version"]) != body.expected_studio_version:
            raise StudioInvariantError("Studio state changed; refresh before transport preflight")
        broadcast_id = session.get("broadcast_id")
        if not broadcast_id:
            raise StudioInvariantError("Transport preflight requires a broadcast session")
        return chat2_studio_transport.preflight(
            member.user_id,
            str(broadcast_id),
            str(session["project_id"]),
            dict(session.get("profile") or {}),
            internal_playback=body.internal_playback,
            recording_enabled=body.recording_enabled,
        )
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/transport/recordings")
def request_studio_recording(session_id: str, body: RecordingRequest, request: Request):
    member, _ = _member(request)
    try:
        session = studio_repo.get_session(member.user_id, session_id)
        return chat2_recording_actions.action(
            member.user_id,
            session.get("broadcast_id"),
            "start",
            body.kind,
        )
    except Exception as exc:
        _raise(exc)


def install_chat2_studio_integration(app: Any) -> None:
    """Install canonical Chat 2 adapters after the compatibility modules have loaded."""
    from . import shared_sky_control_room_extensions as extensions

    studio.transport = chat2_studio_transport
    extensions.recording_actions = chat2_recording_actions
    existing = {getattr(route, "path", "") for route in app.router.routes}
    marker = "/shared-sky/studio/api/sessions/{session_id}/transport/status"
    if marker not in existing:
        app.include_router(router)


__all__ = [
    "CanonicalChat2RecordingActions",
    "CanonicalChat2StudioTransportAdapter",
    "TransportBindRequest",
    "chat2_recording_actions",
    "chat2_studio_transport",
    "install_chat2_studio_integration",
    "router",
]
