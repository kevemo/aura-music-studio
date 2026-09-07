from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .esp_niche import require_esp_hub_member
from .shared_sky_control_room import StudioConflict, StudioInvariantError, normalize_transform, validate_no_secrets
from .shared_sky_studio_history_graphics import (
    TrackedSourceCreate,
    history_repo,
    normalize_graphic_style,
)

router = APIRouter(tags=["Shared Sky Motion Graphics"])

TickerBinding = Literal["static", "transport_state", "recording_state"]


def _clean(value: str, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


class TickerCreateRequest(BaseModel):
    name: str = Field(default="Shared Sky Ticker", min_length=1, max_length=120)
    items: list[str] = Field(default_factory=list, max_length=20)
    separator: str = Field(default=" • ", max_length=12)
    binding: TickerBinding = "static"
    prefix: str = Field(default="", max_length=80)
    speed_seconds: float = Field(default=18.0, ge=4.0, le=120.0)
    direction: Literal["left", "right"] = "left"
    style: dict[str, Any] = Field(default_factory=dict)
    transform: dict[str, Any] | None = None
    expected_version: int = Field(ge=1)

    @field_validator("items")
    @classmethod
    def clean_items(cls, value: list[str]) -> list[str]:
        rows = [_clean(item, 160) for item in value]
        return [item for item in rows if item][:20]

    @field_validator("separator")
    @classmethod
    def clean_separator(cls, value: str) -> str:
        return _clean(value, 12) or " • "


class CountdownCreateRequest(BaseModel):
    name: str = Field(default="Shared Sky Countdown", min_length=1, max_length=120)
    label: str = Field(default="Starting in", max_length=120)
    target_at: datetime
    complete_text: str = Field(default="Starting now", max_length=120)
    show_days: bool = True
    style: dict[str, Any] = Field(default_factory=dict)
    transform: dict[str, Any] | None = None
    expected_version: int = Field(ge=1)

    @field_validator("target_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Countdown target_at must include an explicit timezone")
        return value


def _style(value: dict[str, Any]) -> dict[str, Any]:
    result = normalize_graphic_style(value)
    validate_no_secrets(result)
    return result


def create_ticker(user_id: str, session_id: str, body: TickerCreateRequest) -> str:
    if body.binding == "static" and not body.items:
        raise StudioInvariantError("A static ticker requires at least one text item")
    config = {
        "privacy": "programme_safe",
        "text": body.items[0] if body.items else body.prefix or "Shared Sky",
        "graphic": {
            "schema_version": 1,
            "kind": "ticker",
            "items": body.items,
            "separator": body.separator,
            "binding": body.binding,
            "prefix": _clean(body.prefix, 80),
            "speed_seconds": float(body.speed_seconds),
            "direction": body.direction,
            "style": _style(body.style),
            "authoritative_binding": body.binding != "static",
            "external_network_fetch": False,
        },
        "transform": normalize_transform(
            body.transform
            or {"x": 0.02, "y": 0.88, "width": 0.96, "height": 0.09, "rotation": 0, "opacity": 1}
        ),
    }
    validate_no_secrets(config)
    return history_repo.create_source(
        user_id,
        session_id,
        TrackedSourceCreate(
            source_type="text",
            name=body.name,
            visible=True,
            config=config,
            expected_version=body.expected_version,
        ),
    )


def create_countdown(user_id: str, session_id: str, body: CountdownCreateRequest) -> str:
    config = {
        "privacy": "programme_safe",
        "text": _clean(body.label, 120),
        "graphic": {
            "schema_version": 1,
            "kind": "countdown",
            "label": _clean(body.label, 120),
            "target_at": body.target_at.isoformat(),
            "complete_text": _clean(body.complete_text, 120),
            "show_days": bool(body.show_days),
            "style": _style(body.style),
            "external_network_fetch": False,
        },
        "transform": normalize_transform(
            body.transform
            or {"x": 0.20, "y": 0.34, "width": 0.60, "height": 0.22, "rotation": 0, "opacity": 1}
        ),
    }
    validate_no_secrets(config)
    return history_repo.create_source(
        user_id,
        session_id,
        TrackedSourceCreate(
            source_type="text",
            name=body.name,
            visible=True,
            config=config,
            expected_version=body.expected_version,
        ),
    )


def _member(request: Request):
    return require_esp_hub_member(request)


def _raise(exc: Exception) -> None:
    if isinstance(exc, KeyError):
        raise HTTPException(404, "Shared Sky Studio resource not found") from exc
    if isinstance(exc, StudioConflict):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, (StudioInvariantError, ValueError)):
        raise HTTPException(400, str(exc)) from exc
    raise HTTPException(500, "Shared Sky motion graphic operation failed") from exc


@router.post("/shared-sky/studio/api/sessions/{session_id}/graphics/ticker")
def add_ticker(session_id: str, body: TickerCreateRequest, request: Request):
    member, _ = _member(request)
    try:
        source_id = create_ticker(member.user_id, session_id, body)
        return {"source_id": source_id, "graphic_kind": "ticker", "programme_unchanged": True}
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/graphics/countdown")
def add_countdown(session_id: str, body: CountdownCreateRequest, request: Request):
    member, _ = _member(request)
    try:
        source_id = create_countdown(member.user_id, session_id, body)
        return {"source_id": source_id, "graphic_kind": "countdown", "programme_unchanged": True}
    except Exception as exc:
        _raise(exc)


def install_shared_sky_motion_graphics(app: Any) -> None:
    existing = {
        (getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", set()) or set())))
        for route in app.router.routes
    }
    for route in router.routes:
        signature = (
            getattr(route, "path", ""),
            tuple(sorted(getattr(route, "methods", set()) or set())),
        )
        if signature not in existing:
            app.router.routes.append(route)
            existing.add(signature)
    app.openapi_schema = None


__all__ = [
    "CountdownCreateRequest",
    "TickerCreateRequest",
    "create_countdown",
    "create_ticker",
    "install_shared_sky_motion_graphics",
    "router",
]
