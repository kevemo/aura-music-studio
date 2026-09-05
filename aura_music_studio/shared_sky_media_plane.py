from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .esp_niche import require_esp_hub_member
from .owner_identity import owner_session_authorized
from .shared_sky_streaming_studios import shared_sky

router = APIRouter(tags=["Shared Sky Media Plane"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _signing_secret() -> str:
    return (os.getenv("SHARED_SKY_INGEST_SIGNING_SECRET") or "").strip()


def _node_secret() -> str:
    return (os.getenv("SHARED_SKY_MEDIA_NODE_SECRET") or "").strip()


def _safe_base_url() -> str:
    base = (os.getenv("SHARED_SKY_INGEST_BASE_URL") or "").strip().rstrip("/")
    if base and not base.lower().startswith(("rtmp://", "rtmps://", "srt://", "https://")):
        raise RuntimeError("SHARED_SKY_INGEST_BASE_URL must use RTMP, RTMPS, SRT or HTTPS")
    return base


class IngestSessionCreate(BaseModel):
    broadcast_id: str = Field(min_length=8, max_length=128)
    ttl_seconds: int = Field(default=900, ge=60, le=7200)


class NodeHeartbeat(BaseModel):
    node_id: str = Field(min_length=2, max_length=120)
    region: str = Field(default="unknown", min_length=2, max_length=80)
    ingest_protocols: list[str] = Field(default_factory=lambda: ["rtmps"], max_length=8)
    capacity: int = Field(default=100, ge=1, le=100000)
    active_sessions: int = Field(default=0, ge=0, le=100000)
    healthy: bool = True
    public_ingest_base: str = Field(default="", max_length=500)


@dataclass(frozen=True)
class MediaPlaneSettings:
    signing_configured: bool
    node_auth_configured: bool
    ingest_base_configured: bool
    node_stale_seconds: int

    @classmethod
    def from_env(cls) -> "MediaPlaneSettings":
        try:
            stale = int(os.getenv("SHARED_SKY_MEDIA_NODE_STALE_SECONDS", "90"))
        except ValueError:
            stale = 90
        return cls(
            signing_configured=bool(_signing_secret()),
            node_auth_configured=bool(_node_secret()),
            ingest_base_configured=bool((os.getenv("SHARED_SKY_INGEST_BASE_URL") or "").strip()),
            node_stale_seconds=max(30, min(900, stale)),
        )


class SharedSkyMediaPlane:
    """Durable control-plane contract for contribution ingest and media nodes."""

    def __init__(self, db_path: str | os.PathLike | None = None):
        self.db_path = str(db_path or shared_sky.db_path)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS shared_sky_ingest_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    broadcast_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    node_id TEXT,
                    state TEXT NOT NULL DEFAULT 'issued',
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    last_seen_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(broadcast_id) REFERENCES shared_sky_broadcasts(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_ingest_sessions_broadcast
                ON shared_sky_ingest_sessions(broadcast_id, state, expires_at);
                CREATE TABLE IF NOT EXISTS shared_sky_media_nodes (
                    node_id TEXT PRIMARY KEY,
                    region TEXT NOT NULL,
                    ingest_protocols TEXT NOT NULL,
                    capacity INTEGER NOT NULL,
                    active_sessions INTEGER NOT NULL,
                    healthy INTEGER NOT NULL,
                    public_ingest_base TEXT NOT NULL DEFAULT '',
                    last_seen_at TEXT NOT NULL
                );
                """
            )

    def _require_owned_broadcast(self, user_id: str, broadcast_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM shared_sky_broadcasts WHERE id=? AND user_id=?",
                (broadcast_id, user_id),
            ).fetchone()
        if not row:
            raise KeyError(broadcast_id)
        return dict(row)

    def _token(self, *, session_id: str, user_id: str, broadcast_id: str, expires_at: datetime) -> str:
        secret = _signing_secret()
        if not secret:
            raise RuntimeError("Shared Sky ingest signing is not configured")
        nonce = secrets.token_urlsafe(18)
        expiry = int(expires_at.timestamp())
        body = f"{session_id}.{user_id}.{broadcast_id}.{expiry}.{nonce}"
        sig = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{body}.{sig}"

    def verify_token(self, token: str) -> dict:
        secret = _signing_secret()
        if not secret:
            raise RuntimeError("Shared Sky ingest signing is not configured")
        parts = (token or "").split(".")
        if len(parts) != 6:
            raise ValueError("Invalid Shared Sky ingest credential")
        session_id, user_id, broadcast_id, expiry_raw, nonce, supplied = parts
        body = ".".join(parts[:-1])
        expected = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, supplied):
            raise ValueError("Invalid Shared Sky ingest credential")
        try:
            expiry = datetime.fromtimestamp(int(expiry_raw), tz=timezone.utc)
        except (ValueError, OverflowError) as exc:
            raise ValueError("Invalid Shared Sky ingest credential") from exc
        if expiry <= _now():
            raise ValueError("Shared Sky ingest credential has expired")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM shared_sky_ingest_sessions WHERE id=? AND token_hash=?",
                (session_id, token_hash),
            ).fetchone()
        if not row or row["state"] != "issued" or row["revoked_at"]:
            raise ValueError("Shared Sky ingest credential is not active")
        return {
            "session_id": session_id,
            "user_id": user_id,
            "broadcast_id": broadcast_id,
            "expires_at": expiry.isoformat(),
            "nonce": nonce,
        }

    def _healthy_nodes(self) -> list[dict]:
        settings = MediaPlaneSettings.from_env()
        cutoff = _now() - timedelta(seconds=settings.node_stale_seconds)
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM shared_sky_media_nodes WHERE healthy=1 ORDER BY active_sessions ASC, last_seen_at DESC"
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            seen = _parse(item["last_seen_at"])
            if seen and seen >= cutoff and int(item["active_sessions"]) < int(item["capacity"]):
                item["ingest_protocols"] = [x for x in str(item["ingest_protocols"]).split(",") if x]
                item["healthy"] = True
                result.append(item)
        return result

    def create_session(self, user_id: str, body: IngestSessionCreate) -> dict:
        self._require_owned_broadcast(user_id, body.broadcast_id)
        settings = MediaPlaneSettings.from_env()
        if not settings.signing_configured:
            raise RuntimeError("Shared Sky ingest signing is not configured")
        base = _safe_base_url()
        nodes = self._healthy_nodes()
        selected = nodes[0] if nodes else None
        node_base = (selected or {}).get("public_ingest_base", "").strip() if selected else ""
        ingest_base = node_base.rstrip("/") or base
        if not ingest_base:
            raise RuntimeError("Shared Sky ingest endpoint is not configured")
        now = _now()
        expires = now + timedelta(seconds=body.ttl_seconds)
        session_id = uuid4().hex
        token = self._token(session_id=session_id, user_id=user_id, broadcast_id=body.broadcast_id, expires_at=expires)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        node_id = selected.get("node_id") if selected else None
        with self._connect() as con:
            con.execute(
                """INSERT INTO shared_sky_ingest_sessions(
                    id,user_id,broadcast_id,token_hash,node_id,state,issued_at,expires_at
                ) VALUES(?,?,?,?,?,'issued',?,?)""",
                (session_id, user_id, body.broadcast_id, token_hash, node_id, _iso(now), _iso(expires)),
            )
        ingest_url = f"{ingest_base}/{quote(body.broadcast_id, safe='')}/{quote(token, safe='')}"
        return {
            "id": session_id,
            "broadcast_id": body.broadcast_id,
            "node_id": node_id,
            "state": "issued",
            "expires_at": _iso(expires),
            "ingest_url": ingest_url,
            "credential": token,
        }

    def revoke(self, user_id: str, session_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM shared_sky_ingest_sessions WHERE id=? AND user_id=?", (session_id, user_id)
            ).fetchone()
            if not row:
                raise KeyError(session_id)
            stamp = _iso()
            con.execute(
                "UPDATE shared_sky_ingest_sessions SET state='revoked', revoked_at=? WHERE id=? AND user_id=?",
                (stamp, session_id, user_id),
            )
        return {"id": session_id, "state": "revoked", "revoked_at": stamp}

    def heartbeat(self, body: NodeHeartbeat) -> dict:
        protocols = []
        for protocol in body.ingest_protocols:
            clean = str(protocol).strip().lower()
            if clean in {"rtmp", "rtmps", "srt", "rist", "webrtc"} and clean not in protocols:
                protocols.append(clean)
        if not protocols:
            raise ValueError("Media node must advertise at least one supported ingest protocol")
        public_base = body.public_ingest_base.strip().rstrip("/")
        if public_base and not public_base.lower().startswith(("rtmp://", "rtmps://", "srt://", "https://")):
            raise ValueError("Media node ingest base uses an unsupported scheme")
        stamp = _iso()
        with self._connect() as con:
            con.execute(
                """INSERT INTO shared_sky_media_nodes(
                    node_id,region,ingest_protocols,capacity,active_sessions,healthy,public_ingest_base,last_seen_at
                ) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(node_id) DO UPDATE SET
                    region=excluded.region,
                    ingest_protocols=excluded.ingest_protocols,
                    capacity=excluded.capacity,
                    active_sessions=excluded.active_sessions,
                    healthy=excluded.healthy,
                    public_ingest_base=excluded.public_ingest_base,
                    last_seen_at=excluded.last_seen_at""",
                (
                    body.node_id,
                    body.region,
                    ",".join(protocols),
                    body.capacity,
                    body.active_sessions,
                    int(body.healthy),
                    public_base,
                    stamp,
                ),
            )
        return {"accepted": True, "node_id": body.node_id, "last_seen_at": stamp}

    def status(self) -> dict:
        settings = MediaPlaneSettings.from_env()
        nodes = self._healthy_nodes()
        with self._connect() as con:
            active = con.execute(
                "SELECT COUNT(*) FROM shared_sky_ingest_sessions WHERE state='issued' AND expires_at>?",
                (_iso(),),
            ).fetchone()[0]
            total_nodes = con.execute("SELECT COUNT(*) FROM shared_sky_media_nodes").fetchone()[0]
        return {
            "signing_configured": settings.signing_configured,
            "node_auth_configured": settings.node_auth_configured,
            "ingest_base_configured": settings.ingest_base_configured,
            "healthy_nodes": len(nodes),
            "registered_nodes": total_nodes,
            "active_ingest_sessions": active,
            "nodes": [
                {
                    "node_id": item["node_id"],
                    "region": item["region"],
                    "capacity": item["capacity"],
                    "active_sessions": item["active_sessions"],
                    "ingest_protocols": item["ingest_protocols"],
                    "last_seen_at": item["last_seen_at"],
                }
                for item in nodes
            ],
            "media_termination_deployed": False,
            "note": "This registry is a control-plane contract; a deployed RTMP/SRT/WebRTC media service remains required.",
        }


media_plane = SharedSkyMediaPlane()


def _owner(request: Request) -> None:
    if not owner_session_authorized(request):
        raise HTTPException(401, "Owner authentication required")


def _member(request: Request):
    return require_esp_hub_member(request)


@router.post("/shared-sky/api/ingest-sessions")
def create_ingest_session(body: IngestSessionCreate, request: Request):
    member, _ = _member(request)
    try:
        payload = {"session": media_plane.create_session(member.user_id, body)}
        return JSONResponse(
            content=payload,
            headers={
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "Referrer-Policy": "no-referrer",
            },
        )
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky broadcast not found") from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/shared-sky/api/ingest-sessions/{session_id}/revoke")
def revoke_ingest_session(session_id: str, request: Request):
    member, _ = _member(request)
    try:
        return media_plane.revoke(member.user_id, session_id)
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky ingest session not found") from exc


@router.post("/shared-sky/internal/media-nodes/heartbeat", include_in_schema=False)
def media_node_heartbeat(body: NodeHeartbeat, x_shared_sky_node_key: str = Header(default="")):
    configured = _node_secret()
    if not configured or not x_shared_sky_node_key or not hmac.compare_digest(configured, x_shared_sky_node_key):
        raise HTTPException(401, "Shared Sky media node authentication required")
    try:
        return media_plane.heartbeat(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/owner/shared-sky/api/media-plane")
def owner_media_plane_status(request: Request):
    _owner(request)
    return media_plane.status()


__all__ = ["IngestSessionCreate", "MediaPlaneSettings", "NodeHeartbeat", "SharedSkyMediaPlane", "media_plane", "router"]
