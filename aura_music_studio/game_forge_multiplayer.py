from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .game_forge_api import _creator
from .game_forge_store import game_dir, load_game

router = APIRouter(tags=["Game Forge Multiplayer Authority"])

_SCHEMA_VERSION = "game_forge_multiplayer_session.v1"
_PROVIDER_ENV = "AURA_GAME_MULTIPLAYER_PROVIDER"
_SESSION_PREFIX = "gfmp_"
_OPAQUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,239}$")

SessionState = Literal[
    "requested",
    "provisioning",
    "active",
    "draining",
    "ended",
    "failed",
    "cancelled",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _provider_name() -> str | None:
    raw = str(os.getenv(_PROVIDER_ENV) or "").strip().lower()
    if not raw:
        return None
    normalized = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    if not normalized:
        return None
    enabled = str(os.getenv(f"AURA_GAME_MULTIPLAYER_PROVIDER_{normalized.upper()}_ENABLED") or "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return None
    return normalized


def _opaque(value: str | None, label: str) -> str:
    clean = str(value or "").strip()
    if not clean or not _OPAQUE_RE.fullmatch(clean):
        raise ValueError(f"{label} must be an opaque provider identifier")
    if "/" in clean or "\\" in clean or "://" in clean:
        raise ValueError(f"{label} must not be a filesystem path or URL")
    return clean


def _sessions_dir(game_id: str) -> Path:
    root = game_dir(game_id) / "multiplayer" / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_session_id(session_id: str) -> str:
    clean = str(session_id or "").strip()
    if not clean.startswith(_SESSION_PREFIX) or not re.fullmatch(r"gfmp_[a-f0-9]{32}", clean):
        raise ValueError("Invalid multiplayer session id")
    return clean


def _session_path(game_id: str, session_id: str) -> Path:
    root = _sessions_dir(game_id).resolve()
    path = (root / f"{_safe_session_id(session_id)}.json").resolve()
    if root not in path.parents:
        raise ValueError("Multiplayer session path escaped tenant game storage")
    return path


class MultiplayerSessionRequest(BaseModel):
    request_key: str = Field(min_length=1, max_length=120)
    max_players: int = Field(default=8, ge=2, le=64)
    expected_game_version: int | None = Field(default=None, ge=1)
    expected_build_id: str | None = Field(default=None, max_length=240)

    @field_validator("request_key", "expected_build_id")
    @classmethod
    def validate_opaque_fields(cls, value: str | None, info):
        if value is None:
            return value
        return _opaque(value, info.field_name)


class MultiplayerSession(BaseModel):
    schema_version: str = _SCHEMA_VERSION
    session_id: str
    request_key_hash: str
    game_id: str
    game_version: int
    build_id: str
    build_content_hash: str
    provider: str | None = None
    state: SessionState
    max_players: int = Field(ge=2, le=64)
    provider_session_ref: str | None = None
    provider_join_ref: str | None = None
    player_count: int | None = Field(default=None, ge=0)
    latency_ms: float | None = Field(default=None, ge=0)
    termination_requested_at: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    correlation_ref: str | None = None
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    activated_at: str | None = None
    ended_at: str | None = None
    provenance: dict = Field(default_factory=dict)


def _save(session: MultiplayerSession) -> MultiplayerSession:
    session.updated_at = _now()
    path = _session_path(session.game_id, session.session_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(session.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
    return session


def load_multiplayer_session(game_id: str, session_id: str) -> MultiplayerSession:
    path = _session_path(game_id, session_id)
    if not path.is_file():
        raise FileNotFoundError(session_id)
    return MultiplayerSession.model_validate_json(path.read_text(encoding="utf-8"))


def list_multiplayer_sessions(game_id: str) -> list[MultiplayerSession]:
    rows: list[MultiplayerSession] = []
    for path in _sessions_dir(game_id).glob(f"{_SESSION_PREFIX}*.json"):
        try:
            rows.append(MultiplayerSession.model_validate_json(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return sorted(rows, key=lambda row: row.created_at, reverse=True)


def _request_hash(request_key: str) -> str:
    return hashlib.sha256(request_key.encode("utf-8")).hexdigest()


def _session_id(game_id: str, game_version: int, build_id: str, request_key: str) -> str:
    seed = f"{game_id}:{game_version}:{build_id}:{request_key}".encode("utf-8")
    return f"{_SESSION_PREFIX}{hashlib.sha256(seed).hexdigest()[:32]}"


def multiplayer_capability(game_id: str) -> dict:
    try:
        game = load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc

    provider = _provider_name()
    blockers: list[str] = []
    if not game.content.online_multiplayer:
        blockers.append("online_multiplayer_disclosure_disabled")
    if game.latest_build is None:
        blockers.append("build_required")
    elif not game.latest_build.network_access_enabled:
        blockers.append("latest_build_network_access_disabled")
    if provider is None:
        blockers.append("multiplayer_provider_unavailable")

    return {
        "game_id": game.id,
        "available": not blockers,
        "provider": provider,
        "authority": "server_provider_authoritative",
        "browser_authoritative": False,
        "simulated_sessions": False,
        "simulated_players": False,
        "simulated_network_metrics": False,
        "blockers": blockers,
    }


def request_multiplayer_session(game_id: str, body: MultiplayerSessionRequest) -> MultiplayerSession:
    try:
        game = load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc

    if not game.content.online_multiplayer:
        raise HTTPException(409, detail={"code": "multiplayer_not_declared", "message": "Game DNA does not declare online multiplayer."})
    build = game.latest_build
    if build is None:
        raise HTTPException(409, detail={"code": "multiplayer_build_required", "message": "Create a real Game Forge build before requesting multiplayer."})
    if not build.network_access_enabled:
        raise HTTPException(
            409,
            detail={
                "code": "multiplayer_build_network_disabled",
                "message": "The pinned Game Forge build does not permit network access; multiplayer cannot be represented as ready.",
            },
        )
    if body.expected_game_version is not None and body.expected_game_version != game.version:
        raise HTTPException(409, detail={"code": "stale_game_version", "current_game_version": game.version})
    if body.expected_build_id is not None and body.expected_build_id != build.build_id:
        raise HTTPException(409, detail={"code": "stale_build", "current_build_id": build.build_id})

    session_id = _session_id(game.id, game.version, build.build_id, body.request_key)
    try:
        existing = load_multiplayer_session(game.id, session_id)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        return existing

    provider = _provider_name()
    session = MultiplayerSession(
        session_id=session_id,
        request_key_hash=_request_hash(body.request_key),
        game_id=game.id,
        game_version=game.version,
        build_id=build.build_id,
        build_content_hash=build.content_hash,
        provider=provider,
        state="requested" if provider else "failed",
        max_players=body.max_players,
        failure_code=None if provider else "multiplayer_provider_unavailable",
        failure_message=None if provider else "No enabled Game Forge multiplayer provider is configured.",
        provenance={
            "authority": "server_provider_authoritative",
            "provider_execution_delegated": bool(provider),
            "client_can_activate_session": False,
            "client_can_report_metrics": False,
            "fake_players_generated": False,
            "fake_metrics_generated": False,
        },
    )
    _save(session)
    if provider is None:
        raise HTTPException(
            503,
            detail={
                "code": "multiplayer_provider_unavailable",
                "session_id": session.session_id,
                "message": "No enabled Game Forge multiplayer provider is configured. No session, players or network metrics were fabricated.",
            },
        )
    return session


def _require_provider(session: MultiplayerSession, provider: str) -> str:
    clean = str(provider or "").strip().lower()
    if not clean or clean != session.provider:
        raise ValueError("Multiplayer provider does not match the session authority")
    return clean


def claim_multiplayer_session(game_id: str, session_id: str, *, provider: str, correlation_ref: str | None = None) -> MultiplayerSession:
    session = load_multiplayer_session(game_id, session_id)
    _require_provider(session, provider)
    if session.state == "provisioning":
        return session
    if session.state != "requested":
        raise ValueError("Only requested multiplayer sessions can be claimed")
    session.state = "provisioning"
    if correlation_ref is not None:
        session.correlation_ref = _opaque(correlation_ref, "correlation_ref")
    return _save(session)


def activate_multiplayer_session(
    game_id: str,
    session_id: str,
    *,
    provider: str,
    provider_session_ref: str,
    provider_join_ref: str,
) -> MultiplayerSession:
    session = load_multiplayer_session(game_id, session_id)
    _require_provider(session, provider)
    if session.state == "active":
        return session
    if session.state != "provisioning":
        raise ValueError("Only provisioning multiplayer sessions can be activated")
    session.provider_session_ref = _opaque(provider_session_ref, "provider_session_ref")
    session.provider_join_ref = _opaque(provider_join_ref, "provider_join_ref")
    session.state = "active"
    session.activated_at = _now()
    return _save(session)


def report_multiplayer_metrics(
    game_id: str,
    session_id: str,
    *,
    provider: str,
    player_count: int | None = None,
    latency_ms: float | None = None,
) -> MultiplayerSession:
    session = load_multiplayer_session(game_id, session_id)
    _require_provider(session, provider)
    if session.state != "active":
        raise ValueError("Network metrics are accepted only for active provider-authoritative sessions")
    if player_count is not None:
        if player_count < 0 or player_count > session.max_players:
            raise ValueError("player_count is outside the session capacity")
        session.player_count = int(player_count)
    if latency_ms is not None:
        if latency_ms < 0 or latency_ms > 120000:
            raise ValueError("latency_ms is outside the accepted provider-reporting range")
        session.latency_ms = float(latency_ms)
    return _save(session)


def fail_multiplayer_session(
    game_id: str,
    session_id: str,
    *,
    provider: str,
    code: str,
    message: str,
) -> MultiplayerSession:
    session = load_multiplayer_session(game_id, session_id)
    _require_provider(session, provider)
    if session.state in {"ended", "cancelled"}:
        raise ValueError("A terminal multiplayer session cannot be failed")
    session.state = "failed"
    session.failure_code = _opaque(code, "failure_code")
    session.failure_message = str(message or "Multiplayer provider failed").strip()[:500]
    session.player_count = None
    session.latency_ms = None
    session.ended_at = _now()
    return _save(session)


def end_multiplayer_session(game_id: str, session_id: str, *, provider: str) -> MultiplayerSession:
    session = load_multiplayer_session(game_id, session_id)
    _require_provider(session, provider)
    if session.state == "ended":
        return session
    if session.state not in {"active", "draining"}:
        raise ValueError("Only active or draining multiplayer sessions can be ended")
    session.state = "ended"
    session.player_count = 0 if session.player_count is not None else None
    session.latency_ms = None
    session.ended_at = _now()
    return _save(session)


def request_multiplayer_termination(game_id: str, session_id: str) -> MultiplayerSession:
    session = load_multiplayer_session(game_id, session_id)
    if session.state in {"requested", "provisioning"}:
        session.state = "cancelled"
        session.termination_requested_at = _now()
        session.ended_at = session.termination_requested_at
        return _save(session)
    if session.state == "active":
        session.state = "draining"
        session.termination_requested_at = _now()
        return _save(session)
    return session


def public_multiplayer_session(session: MultiplayerSession) -> dict:
    payload = session.model_dump()
    payload["request_key_hash"] = session.request_key_hash
    payload["provider_credentials_included"] = False
    payload["authoritative_metrics"] = session.state in {"active", "draining", "ended"} and (
        session.player_count is not None or session.latency_ms is not None
    )
    return payload


@router.get("/api/game-forge/games/{game_id}/multiplayer/capability")
def get_multiplayer_capability(game_id: str, request: Request):
    _creator(request)
    return multiplayer_capability(game_id)


@router.get("/api/game-forge/games/{game_id}/multiplayer/sessions")
def get_multiplayer_sessions(game_id: str, request: Request):
    _creator(request)
    try:
        load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc
    rows = list_multiplayer_sessions(game_id)
    return {"sessions": [public_multiplayer_session(row) for row in rows], "count": len(rows)}


@router.post("/api/game-forge/games/{game_id}/multiplayer/sessions")
def create_multiplayer_session(game_id: str, body: MultiplayerSessionRequest, request: Request):
    _creator(request)
    return public_multiplayer_session(request_multiplayer_session(game_id, body))


@router.get("/api/game-forge/games/{game_id}/multiplayer/sessions/{session_id}")
def get_multiplayer_session(game_id: str, session_id: str, request: Request):
    _creator(request)
    try:
        session = load_multiplayer_session(game_id, session_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Multiplayer session not found") from exc
    return public_multiplayer_session(session)


@router.delete("/api/game-forge/games/{game_id}/multiplayer/sessions/{session_id}")
def delete_multiplayer_session(game_id: str, session_id: str, request: Request):
    _creator(request)
    try:
        session = request_multiplayer_termination(game_id, session_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Multiplayer session not found") from exc
    return public_multiplayer_session(session)


__all__ = [
    "MultiplayerSession",
    "MultiplayerSessionRequest",
    "activate_multiplayer_session",
    "claim_multiplayer_session",
    "end_multiplayer_session",
    "fail_multiplayer_session",
    "list_multiplayer_sessions",
    "load_multiplayer_session",
    "multiplayer_capability",
    "public_multiplayer_session",
    "report_multiplayer_metrics",
    "request_multiplayer_session",
    "request_multiplayer_termination",
    "router",
]
