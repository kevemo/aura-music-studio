from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .esp_niche import require_esp_hub_member
from .owner_identity import owner_session_authorized
from .shared_sky_destination_adapters import validate_destination_url
from .shared_sky_relay import SharedSkyRelayError
from .shared_sky_security import SharedSkyVaultError
from .shared_sky_transport_domain import (
    OperationInProgress,
    PreflightBlocked,
    TransportRateLimited,
    transport,
)

router = APIRouter(tags=["Shared Sky Transport"])


class SourceRegisterRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=160)
    source_type: Literal[
        "studio_program",
        "browser",
        "external_encoder",
        "music_project",
        "video_project",
        "game_project",
        "battle_program",
    ]
    source_ref: str = Field(min_length=1, max_length=500)
    state: Literal["configuring", "ready", "failed"] = "ready"
    capabilities: dict = Field(default_factory=dict)


class TransportConfigureRequest(BaseModel):
    source_id: str | None = Field(default=None, max_length=160)
    internal_playback: bool = True
    rendition_profile: dict = Field(default_factory=dict)
    recording_enabled: bool = False
    ingest_session_id: str | None = Field(default=None, max_length=160)


class HealthReportRequest(BaseModel):
    event_type: str = Field(min_length=1, max_length=80)
    reason_code: str = Field(default="ok", max_length=80)
    destination_id: str | None = Field(default=None, max_length=160)
    metrics: dict = Field(default_factory=dict)


class RecordingFinalizeRequest(BaseModel):
    state: Literal["complete", "failed", "incomplete"]
    asset_id: str | None = Field(default=None, max_length=300)
    storage_uri: str | None = Field(default=None, max_length=1200)
    checksum_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    size_bytes: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    reason_code: str | None = Field(default=None, max_length=80)


class DestinationValidateRequest(BaseModel):
    endpoint: str = Field(min_length=8, max_length=2000)


class DestinationPresetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    destination_ids: list[str] = Field(min_length=1, max_length=50)


class HighlightMarkerRequest(BaseModel):
    offset_ms: int = Field(ge=0, le=172_800_000)
    label: str = Field(default="", max_length=240)
    marker_type: Literal["highlight", "chapter", "clip", "replay"] = "highlight"


class StaleCleanupRequest(BaseModel):
    stale_after_seconds: int = Field(default=300, ge=60, le=86_400)


def _member_id(request: Request) -> str:
    member, _membership = require_esp_hub_member(request)
    return str(member.user_id)


def _owner(request: Request) -> None:
    if not owner_session_authorized(request):
        raise HTTPException(401, "Owner authentication required")


def _idempotency_key(value: str) -> str:
    clean = (value or "").strip()
    if len(clean) < 8 or len(clean) > 200:
        raise HTTPException(400, "Idempotency-Key must contain 8 to 200 characters")
    return clean


@router.post("/shared-sky/api/programme-sources")
def register_programme_source(body: SourceRegisterRequest, request: Request):
    try:
        source = transport.register_source(
            _member_id(request),
            body.project_id,
            body.source_type,
            body.source_ref,
            state=body.state,
            capabilities=body.capabilities,
        )
        return {"source": source}
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky project not found") from exc


@router.put("/shared-sky/api/broadcasts/{broadcast_id}/transport")
def configure_transport(broadcast_id: str, body: TransportConfigureRequest, request: Request):
    try:
        return transport.configure(
            _member_id(request),
            broadcast_id,
            source_id=body.source_id,
            internal_playback=body.internal_playback,
            rendition_profile=body.rendition_profile,
            recording_enabled=body.recording_enabled,
            ingest_session_id=body.ingest_session_id,
        )
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky broadcast or programme source not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/shared-sky/api/broadcasts/{broadcast_id}/transport")
def transport_status(broadcast_id: str, request: Request):
    try:
        return transport.reconcile(_member_id(request), broadcast_id)
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky broadcast not found") from exc


@router.get("/shared-sky/api/broadcasts/{broadcast_id}/transport/preflight")
def transport_preflight(broadcast_id: str, request: Request):
    try:
        return transport.preflight(_member_id(request), broadcast_id)
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky broadcast not found") from exc


@router.post("/shared-sky/api/broadcasts/{broadcast_id}/transport/start")
def transport_start(
    broadcast_id: str,
    request: Request,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
):
    try:
        return transport.start(_member_id(request), broadcast_id, _idempotency_key(idempotency_key))
    except PreflightBlocked as exc:
        raise HTTPException(409, detail={"code": "preflight_blocked", **exc.result}) from exc
    except OperationInProgress as exc:
        raise HTTPException(409, str(exc)) from exc
    except TransportRateLimited as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky broadcast not found") from exc
    except (ValueError, SharedSkyVaultError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except SharedSkyRelayError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/shared-sky/api/broadcasts/{broadcast_id}/transport/stop")
def transport_stop(
    broadcast_id: str,
    request: Request,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
):
    try:
        return transport.stop(_member_id(request), broadcast_id, _idempotency_key(idempotency_key))
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky broadcast not found") from exc
    except OperationInProgress as exc:
        raise HTTPException(409, str(exc)) from exc
    except TransportRateLimited as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/shared-sky/api/broadcasts/{broadcast_id}/destinations/{destination_id}/retry")
def retry_destination(
    broadcast_id: str,
    destination_id: str,
    request: Request,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
):
    try:
        return transport.retry_destination(
            _member_id(request),
            broadcast_id,
            destination_id,
            _idempotency_key(idempotency_key),
        )
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky broadcast or destination not found") from exc
    except OperationInProgress as exc:
        raise HTTPException(409, str(exc)) from exc
    except TransportRateLimited as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/shared-sky/api/broadcasts/{broadcast_id}/playback")
def playback_descriptor(broadcast_id: str, request: Request):
    try:
        return transport.playback(_member_id(request), broadcast_id)
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky broadcast not found") from exc


@router.post("/shared-sky/api/broadcasts/{broadcast_id}/transport/health")
def report_transport_health(broadcast_id: str, body: HealthReportRequest, request: Request):
    try:
        return transport.report_health(
            _member_id(request),
            broadcast_id,
            body.event_type,
            body.reason_code,
            body.metrics,
            destination_id=body.destination_id,
        )
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky broadcast not found") from exc
    except TransportRateLimited as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc


@router.get("/shared-sky/api/destination-capabilities")
def destination_capabilities(request: Request):
    return {"destinations": transport.adapter_matrix(_member_id(request))}


@router.post("/shared-sky/api/destination-presets")
def create_destination_preset(body: DestinationPresetRequest, request: Request):
    try:
        return {
            "preset": transport.create_destination_preset(
                _member_id(request), body.name, body.destination_ids
            )
        }
    except KeyError as exc:
        raise HTTPException(404, "One or more Shared Sky destinations were not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/shared-sky/api/destination-presets")
def list_destination_presets(request: Request):
    return {"presets": transport.destination_presets(_member_id(request))}


@router.post(
    "/shared-sky/api/broadcasts/{broadcast_id}/destination-presets/{preset_id}/apply"
)
def apply_destination_preset(broadcast_id: str, preset_id: str, request: Request):
    try:
        return transport.apply_destination_preset(
            _member_id(request), broadcast_id, preset_id
        )
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky broadcast, preset or destination not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/shared-sky/api/transport/capacity")
def member_transport_capacity(request: Request):
    return transport.capacity_snapshot(_member_id(request))


@router.get("/owner/shared-sky/api/transport/capacity")
def owner_transport_capacity(request: Request):
    _owner(request)
    return transport.capacity_snapshot()


@router.post("/owner/shared-sky/api/transport/cleanup-stale")
def owner_cleanup_stale_transport(body: StaleCleanupRequest, request: Request):
    _owner(request)
    return transport.cleanup_stale_sessions(stale_after_seconds=body.stale_after_seconds)


@router.post("/shared-sky/api/broadcasts/{broadcast_id}/recordings/{kind}")
def request_recording(broadcast_id: str, kind: str, request: Request):
    try:
        return {"recording": transport.request_recording(_member_id(request), broadcast_id, kind)}
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky broadcast not found") from exc
    except (ValueError, RuntimeError) as exc:
        status = 503 if isinstance(exc, RuntimeError) else 400
        raise HTTPException(status, str(exc)) from exc


@router.put("/shared-sky/api/broadcasts/{broadcast_id}/recordings/{kind}")
def finalize_recording(
    broadcast_id: str,
    kind: str,
    body: RecordingFinalizeRequest,
    request: Request,
):
    try:
        return {
            "recording": transport.finalize_recording(
                _member_id(request),
                broadcast_id,
                kind,
                body.model_dump(),
            )
        }
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky recording not found") from exc


@router.post("/shared-sky/api/broadcasts/{broadcast_id}/markers")
def create_highlight_marker(
    broadcast_id: str,
    body: HighlightMarkerRequest,
    request: Request,
):
    try:
        marker = transport.add_highlight_marker(
            _member_id(request),
            broadcast_id,
            offset_ms=body.offset_ms,
            label=body.label,
            marker_type=body.marker_type,
        )
        return {"marker": marker}
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky broadcast not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/shared-sky/api/broadcasts/{broadcast_id}/markers")
def list_highlight_markers(broadcast_id: str, request: Request):
    try:
        return {"markers": transport.highlight_markers(_member_id(request), broadcast_id)}
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky broadcast not found") from exc


@router.post("/shared-sky/api/destinations/validate")
def validate_custom_destination(body: DestinationValidateRequest, request: Request):
    user_id = _member_id(request)
    try:
        transport.rate_limit(user_id, "destination_validate", limit=30)
        endpoint = validate_destination_url(body.endpoint, resolve_dns=True)
    except TransportRateLimited as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    parsed = urlparse(endpoint)
    return {
        "valid": True,
        "scheme": parsed.scheme.lower(),
        "host": parsed.hostname,
        "credential_embedded": False,
    }


__all__ = [
    "DestinationPresetRequest",
    "DestinationValidateRequest",
    "HealthReportRequest",
    "HighlightMarkerRequest",
    "RecordingFinalizeRequest",
    "SourceRegisterRequest",
    "StaleCleanupRequest",
    "TransportConfigureRequest",
    "router",
]
