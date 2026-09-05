from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from . import aura_live_overlay_engine as engine

_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_SESSION_RE = re.compile(r"^[A-Za-z0-9._:-]{8,120}$")
_EVENT_RE = re.compile(r"^[A-Za-z0-9._:-]{8,160}$")
_INGEST_PATH = "/live-overlay/source/relay/events"
_STATUS_PATH = "/api/live-overlays/connector"
_CONTRACT_PATH = "/api/live-overlays/event-contract"
_SETUP_PATH = "/live-overlay-studio/connector"


def _canonical_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(422, "LIVE relay occurred_at is required")
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(400, "LIVE relay occurred_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HTTPException(400, "LIVE relay occurred_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _identity_payload(body: object) -> tuple[str, str, str, str, str, dict, str, str, str]:
    if not isinstance(body, dict):
        raise HTTPException(422, "LIVE relay request body must be a JSON object")
    provider = body.get("provider")
    session_id = body.get("session_id")
    event_id = body.get("event_id")
    event_type = body.get("event_type")
    if not isinstance(provider, str) or not _PROVIDER_RE.fullmatch(provider):
        raise HTTPException(422, "LIVE relay provider must be a bounded lowercase provider identifier")
    if not isinstance(session_id, str) or not _SESSION_RE.fullmatch(session_id):
        raise HTTPException(422, "LIVE relay session_id is invalid")
    if not isinstance(event_id, str) or not _EVENT_RE.fullmatch(event_id):
        raise HTTPException(422, "LIVE relay event_id is invalid")
    if not isinstance(event_type, str) or event_type not in engine.EVENT_TYPES:
        raise HTTPException(400, "Unsupported normalized LIVE event type")
    occurred_at = _canonical_timestamp(body.get("occurred_at"))
    payload = body.get("payload", {})
    if not isinstance(payload, dict):
        raise HTTPException(422, "LIVE relay payload must be a JSON object")
    normalized, payload_sha = engine._validated_connector_payload(payload)
    immutable = {
        "provider": provider,
        "session_id": session_id,
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "payload_sha256": payload_sha,
    }
    immutable_bytes = json.dumps(
        immutable,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    immutable_sha = hashlib.sha256(immutable_bytes).hexdigest()
    scoped_material = f"{provider}\0{session_id}\0{event_id}".encode("utf-8")
    scoped_event_id = "scoped:" + hashlib.sha256(scoped_material).hexdigest()
    return (
        provider,
        session_id,
        event_id,
        event_type,
        occurred_at,
        normalized,
        payload_sha,
        immutable_sha,
        scoped_event_id,
    )


def _ensure_schema() -> None:
    with engine._connect() as con:
        con.execute("BEGIN IMMEDIATE")
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_overlay_connector_receipts_v2 (
                user_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                session_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                immutable_sha256 TEXT NOT NULL,
                state TEXT NOT NULL,
                received_at TEXT NOT NULL,
                processed_at TEXT,
                last_error_code TEXT,
                PRIMARY KEY(user_id,provider,session_id,event_id)
            );
            CREATE INDEX IF NOT EXISTS idx_live_overlay_connector_receipts_v2_user_state
                ON live_overlay_connector_receipts_v2(user_id,state,received_at);
            """
        )
        legacy_exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_overlay_connector_receipts'"
        ).fetchone()
        if legacy_exists:
            # Historical receipt identities were only user + event_id. Consume them under an
            # explicit legacy namespace rather than guessing a provider/session and risking replay.
            con.execute(
                """
                INSERT OR IGNORE INTO live_overlay_connector_receipts_v2(
                    user_id,provider,session_id,event_id,event_type,occurred_at,
                    payload_sha256,immutable_sha256,state,received_at,processed_at,last_error_code
                )
                SELECT user_id,'legacy','legacy',event_id,event_type,received_at,
                       payload_sha256,payload_sha256,'processed',received_at,
                       COALESCE(processed_at,received_at),'LegacyReceiptConsumed'
                FROM live_overlay_connector_receipts
                """
            )


def _receipt(user_id: str, provider: str, session_id: str, event_id: str):
    _ensure_schema()
    with engine._connect() as con:
        return con.execute(
            """SELECT * FROM live_overlay_connector_receipts_v2
               WHERE user_id=? AND provider=? AND session_id=? AND event_id=?""",
            (user_id, provider, session_id, event_id),
        ).fetchone()


def _recent_receipts(user_id: str) -> list[dict]:
    _ensure_schema()
    with engine._connect() as con:
        rows = con.execute(
            """SELECT provider,session_id,event_id,event_type,occurred_at,state,
                      received_at,processed_at,last_error_code
               FROM live_overlay_connector_receipts_v2
               WHERE user_id=? AND provider<>'legacy'
               ORDER BY received_at DESC LIMIT 20""",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _begin_receipt(
    *,
    user_id: str,
    provider: str,
    session_id: str,
    event_id: str,
    event_type: str,
    occurred_at: str,
    payload_sha: str,
    immutable_sha: str,
) -> str:
    _ensure_schema()
    now = engine._now()
    with engine._connect() as con:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            """SELECT immutable_sha256,state,received_at
               FROM live_overlay_connector_receipts_v2
               WHERE user_id=? AND provider=? AND session_id=? AND event_id=?""",
            (user_id, provider, session_id, event_id),
        ).fetchone()
        if existing:
            if str(existing["immutable_sha256"]) != immutable_sha:
                raise HTTPException(
                    409,
                    "LIVE provider/session event ID was already used for different immutable event data",
                )
            state = str(existing["state"])
            if state == "processed":
                return "processed"
            if state == "processing" and not engine._processing_is_stale(str(existing["received_at"])):
                return "processing"
            con.execute(
                """UPDATE live_overlay_connector_receipts_v2
                   SET state='processing',received_at=?,processed_at=NULL,last_error_code=NULL
                   WHERE user_id=? AND provider=? AND session_id=? AND event_id=?""",
                (now, user_id, provider, session_id, event_id),
            )
            return "retry"
        con.execute(
            """INSERT INTO live_overlay_connector_receipts_v2(
                   user_id,provider,session_id,event_id,event_type,occurred_at,
                   payload_sha256,immutable_sha256,state,received_at,processed_at,last_error_code
               ) VALUES(?,?,?,?,?,?,?,?, 'processing', ?,NULL,NULL)""",
            (
                user_id,
                provider,
                session_id,
                event_id,
                event_type,
                occurred_at,
                payload_sha,
                immutable_sha,
                now,
            ),
        )
    return "new"


def _finish_receipt(
    *,
    user_id: str,
    provider: str,
    session_id: str,
    event_id: str,
    success: bool,
    error_code: str | None = None,
) -> None:
    now = engine._now()
    state = "processed" if success else "failed"
    with engine._connect() as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            """UPDATE live_overlay_connector_receipts_v2
               SET state=?,processed_at=?,last_error_code=?
               WHERE user_id=? AND provider=? AND session_id=? AND event_id=?""",
            (
                state,
                now if success else None,
                None if success else (error_code or "RelayRejected")[:80],
                user_id,
                provider,
                session_id,
                event_id,
            ),
        )
        con.execute(
            """DELETE FROM live_overlay_connector_receipts_v2
               WHERE user_id=? AND provider<>'legacy' AND rowid NOT IN (
                   SELECT rowid FROM live_overlay_connector_receipts_v2
                   WHERE user_id=? AND provider<>'legacy'
                   ORDER BY received_at DESC LIMIT ?
               )""",
            (user_id, user_id, engine.CONNECTOR_RECEIPT_LIMIT),
        )


async def _body_bytes(response) -> bytes:
    body = b""
    async for chunk in response.body_iterator:
        body += chunk
    return body


def _rebuilt_response(response, body: bytes, *, content_type: str | None = None) -> Response:
    rebuilt = Response(
        content=body,
        status_code=response.status_code,
        media_type=content_type,
        background=response.background,
    )
    raw_headers = [
        (key, value)
        for key, value in response.raw_headers
        if key.lower() not in {b"content-length", b"content-type"}
    ]
    if content_type:
        raw_headers.append((b"content-type", content_type.encode("latin-1")))
    raw_headers.append((b"content-length", str(len(body)).encode("ascii")))
    rebuilt.raw_headers = raw_headers
    return rebuilt


def _duplicate_response(
    *, provider: str, session_id: str, event_id: str, event_type: str, occurred_at: str
) -> JSONResponse:
    return JSONResponse(
        {
            "accepted": True,
            "duplicate": True,
            "event_id": event_id,
            "event_type": event_type,
            "provider": provider,
            "session_id": session_id,
            "occurred_at": occurred_at,
            "state": "processed",
            "normalized_relay": True,
            "scoped_provider_identity": True,
            "provider_connected": False,
            "direct_tiktok_connection_claimed": False,
            "provider_write_authority": False,
        },
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


class AuraLiveRelayIdentityMiddleware(BaseHTTPMiddleware):
    """Require provider/session/time identity on the canonical production relay boundary.

    The existing AURA.LIVE engine remains authoritative for membership, token auth, rate limits,
    normalized payload validation, event application, automation safety and its processing lease.
    This middleware adds the missing provider-event namespace and immutable occurrence evidence.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method.upper()

        if method == "POST" and path == _INGEST_PATH:
            return await self._ingest(request, call_next)

        response = await call_next(request)

        if method == "GET" and path == _CONTRACT_PATH and response.status_code == 200:
            body = await _body_bytes(response)
            try:
                data = json.loads(body)
            except Exception:
                return _rebuilt_response(response, body, content_type="application/json")
            if isinstance(data, dict):
                data.update(
                    {
                        "schema_version": 3,
                        "relay_identity_required": True,
                        "relay_identity_fields": {
                            "provider": "required lowercase provider identifier",
                            "session_id": "required provider LIVE session identifier",
                            "event_id": "required provider event identifier",
                            "occurred_at": "required timezone-aware ISO-8601 provider event timestamp",
                        },
                        "replay_identity": ["provider", "session_id", "event_id"],
                        "immutable_event_binding": [
                            "event_type",
                            "occurred_at",
                            "normalized_payload_sha256",
                        ],
                    }
                )
            encoded = json.dumps(data, separators=(",", ":")).encode("utf-8")
            return _rebuilt_response(response, encoded, content_type="application/json")

        if method == "GET" and path == _STATUS_PATH and response.status_code == 200:
            body = await _body_bytes(response)
            try:
                data = json.loads(body)
            except Exception:
                return _rebuilt_response(response, body, content_type="application/json")
            member = getattr(request.state, "member", None)
            if isinstance(data, dict) and member is not None:
                data["recent_deliveries"] = _recent_receipts(str(member.user_id))
                data["relay_identity_required"] = True
                data["replay_identity"] = ["provider", "session_id", "event_id"]
            encoded = json.dumps(data, separators=(",", ":")).encode("utf-8")
            return _rebuilt_response(response, encoded, content_type="application/json")

        if method == "GET" and path == _SETUP_PATH and response.status_code == 200:
            body = await _body_bytes(response)
            try:
                text = body.decode("utf-8")
            except UnicodeDecodeError:
                return _rebuilt_response(response, body, content_type="text/html; charset=utf-8")
            old = '{"event_id":"provider-unique-id","event_type":"gift","payload":'
            new = (
                '{"provider":"documented_provider","session_id":"live-session-001",'
                '"event_id":"provider-unique-id","event_type":"gift",'
                '"occurred_at":"2026-09-01T05:30:00Z","payload":'
            )
            text = text.replace(old, new).replace(
                "Duplicate event IDs are applied once only;",
                "Duplicate provider + LIVE session + event IDs are applied once only;",
            )
            return _rebuilt_response(response, text.encode("utf-8"), content_type="text/html; charset=utf-8")

        return response

    async def _ingest(self, request: Request, call_next):
        try:
            raw = await request.body()
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "LIVE relay body must contain valid JSON") from exc

        (
            provider,
            session_id,
            event_id,
            event_type,
            occurred_at,
            normalized,
            payload_sha,
            immutable_sha,
            scoped_event_id,
        ) = _identity_payload(data)

        token = engine._relay_token(request)
        connector = engine._resolve_connector(token)
        user_id = str(connector["user_id"])
        receipt_state = _begin_receipt(
            user_id=user_id,
            provider=provider,
            session_id=session_id,
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload_sha=payload_sha,
            immutable_sha=immutable_sha,
        )

        if receipt_state == "processed":
            # Preserve the existing route's membership/rate semantics even when the older bounded
            # receipt has already been pruned and the v2 ledger is the remaining replay authority.
            engine._require_active_member(user_id)
            engine._rate_limit(engine._hash_relay_token(token))
            return _duplicate_response(
                provider=provider,
                session_id=session_id,
                event_id=event_id,
                event_type=event_type,
                occurred_at=occurred_at,
            )
        if receipt_state == "processing":
            engine._require_active_member(user_id)
            engine._rate_limit(engine._hash_relay_token(token))
            return JSONResponse(
                {
                    "accepted": False,
                    "duplicate": True,
                    "retryable": True,
                    "event_id": event_id,
                    "provider": provider,
                    "session_id": session_id,
                    "occurred_at": occurred_at,
                    "state": "processing",
                },
                status_code=409,
                headers={
                    "Cache-Control": "no-store",
                    "Referrer-Policy": "no-referrer",
                    "Retry-After": str(engine.PROCESSING_LEASE_SECONDS),
                },
            )

        forwarded = {
            "event_id": scoped_event_id,
            "event_type": event_type,
            "payload": normalized,
        }
        encoded_request = json.dumps(forwarded, separators=(",", ":")).encode("utf-8")
        request._body = encoded_request
        request.scope["headers"] = [
            (key, value)
            for key, value in request.scope.get("headers", [])
            if key.lower() != b"content-length"
        ] + [(b"content-length", str(len(encoded_request)).encode("ascii"))]

        response = await call_next(request)
        response_body = await _body_bytes(response)
        try:
            response_data = json.loads(response_body)
        except Exception:
            response_data = None

        if 200 <= response.status_code < 300:
            _finish_receipt(
                user_id=user_id,
                provider=provider,
                session_id=session_id,
                event_id=event_id,
                success=True,
            )
        elif response.status_code == 409 and isinstance(response_data, dict) and response_data.get("state") == "processing":
            pass
        else:
            detail = response_data.get("detail") if isinstance(response_data, dict) else None
            _finish_receipt(
                user_id=user_id,
                provider=provider,
                session_id=session_id,
                event_id=event_id,
                success=False,
                error_code=str(detail or f"HTTP{response.status_code}"),
            )

        if isinstance(response_data, dict):
            response_data["event_id"] = event_id
            response_data["provider"] = provider
            response_data["session_id"] = session_id
            response_data["occurred_at"] = occurred_at
            response_data["scoped_provider_identity"] = True
            response_body = json.dumps(response_data, separators=(",", ":")).encode("utf-8")
            return _rebuilt_response(response, response_body, content_type="application/json")
        return _rebuilt_response(response, response_body, content_type=response.headers.get("content-type"))


__all__ = [
    "AuraLiveRelayIdentityMiddleware",
    "_canonical_timestamp",
    "_ensure_schema",
    "_identity_payload",
    "_recent_receipts",
]
