from __future__ import annotations

import os
from datetime import datetime, timezone
from html import escape
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .owner_identity import owner_session_authorized
from .shared_skies_branding import PRODUCT_NAME, install_shared_skies_branding
from .shared_sky_battle_bootstrap import install_shared_sky_battle_routes
from .shared_sky_live_bootstrap import install_shared_sky_live_community
from .shared_sky_media_plane import router as shared_sky_media_plane_router
from .shared_sky_relay import relay
from .shared_sky_streaming_studios import shared_sky
from .shared_sky_worker import SharedSkyWorker, WorkerSettings

router = APIRouter(tags=["Shared Skies Owner Operations"])


def _owner(request: Request) -> None:
    if not owner_session_authorized(request):
        raise HTTPException(401, "Owner authentication required")


def _bool_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int_env(name: str, *, default: int = 0, minimum: int = 0, maximum: int = 8) -> int:
    try:
        value = int((os.getenv(name) or str(default)).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def _safe_worker_rows(rows: list[dict]) -> list[dict]:
    """Project worker health without returning provider/relay error text."""
    safe: list[dict] = []
    for row in rows:
        safe.append(
            {
                "worker_id": str(row.get("worker_id") or ""),
                "status": str(row.get("status") or "unknown")[:32],
                "last_seen_at": row.get("last_seen_at"),
                "last_claimed_schedule_id": row.get("last_claimed_schedule_id"),
                "healthy": bool(row.get("healthy")),
                "error_present": bool(str(row.get("last_error") or "")),
            }
        )
    return safe


def _runtime_snapshot() -> dict:
    settings = WorkerSettings.from_env()
    worker = SharedSkyWorker(shared_sky, settings=settings, worker_id="owner-runtime-probe")
    workers = _safe_worker_rows(worker.worker_health(stale_after_seconds=180))
    active_workers = [row for row in workers if row.get("healthy")]
    owner_status = shared_sky.owner_status()
    relay_health = relay.health().__dict__
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "product": PRODUCT_NAME,
        "scheduler": {
            "enabled": settings.enabled,
            "poll_seconds": settings.poll_seconds,
            "lease_seconds": settings.lease_seconds,
            "max_attempts": settings.max_attempts,
            "retry_seconds": settings.retry_seconds,
            "healthy_workers": len(active_workers),
            "workers": workers,
            "raw_worker_errors_exposed": False,
        },
        "relay": relay_health,
        "vault": owner_status.get("vault", {}),
        "counts": owner_status.get("counts", {}),
        "live_broadcasts": owner_status.get("live_broadcasts", []),
        "deployment": {
            "ingest_configured": bool((os.getenv("SHARED_SKY_INGEST_BASE_URL") or "").strip()),
            "relay_enabled": bool(relay_health.get("enabled")),
            "ffmpeg_available": bool(relay_health.get("ffmpeg_available")),
            "scheduler_enabled": settings.enabled,
            "provider_oauth_configured": _bool_env("SHARED_SKY_PROVIDER_OAUTH_READY"),
            "multi_host_participant_capacity": _bounded_int_env(
                "SHARED_SKY_MULTIHOST_MAX_PARTICIPANTS"
            ),
        },
        "truth_boundary": {
            "production_ready": False,
            "external_provider_approval_required": True,
            "webrtc_sfu_required_for_guest_media": True,
            "pre_recorded_playout_worker_required": True,
        },
    }


@router.get("/owner/shared-sky/api/runtime")
def owner_shared_sky_runtime(request: Request):
    _owner(request)
    return _runtime_snapshot()


@router.get("/owner/shared-sky/runtime", response_class=HTMLResponse, include_in_schema=False)
def owner_shared_sky_runtime_page(request: Request):
    _owner(request)
    status = _runtime_snapshot()
    counts = status["counts"]
    scheduler = status["scheduler"]
    relay_state = status["relay"]
    deployment = status["deployment"]
    worker_rows = "".join(
        "<tr>"
        f"<td>{escape(str(row.get('worker_id', '')))}</td>"
        f"<td>{escape(str(row.get('status', '')))}</td>"
        f"<td>{'Healthy' if row.get('healthy') else 'Stale'}</td>"
        f"<td>{escape(str(row.get('last_seen_at', '')))}</td>"
        "</tr>"
        for row in scheduler["workers"]
    ) or "<tr><td colspan='4'>No worker heartbeat has been recorded yet.</td></tr>"
    live_rows = "".join(
        "<tr>"
        f"<td>{escape(str(row.get('title', '')))}</td>"
        f"<td>{escape(str(row.get('id', '')))}</td>"
        f"<td>{escape(str(row.get('started_at', '')))}</td>"
        "</tr>"
        for row in status["live_broadcasts"]
    ) or "<tr><td colspan='3'>No live Shared Skies broadcasts.</td></tr>"
    readiness = (
        deployment["ingest_configured"]
        and deployment["relay_enabled"]
        and deployment["ffmpeg_available"]
        and deployment["scheduler_enabled"]
    )
    html = f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Shared Skies Owner Runtime</title><style>
body{{margin:0;background:#07101d;color:#eef7ff;font-family:Inter,system-ui,sans-serif}}.wrap{{max-width:1200px;margin:auto;padding:28px}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{background:#0e1c31;border:1px solid #ffffff20;border-radius:16px;padding:16px;margin:14px 0}}.metric b{{display:block;font-size:1.7rem;margin-top:4px}}.ok{{color:#73e3aa}}.bad{{color:#ff94aa}}table{{width:100%;border-collapse:collapse}}th,td{{padding:9px;border-bottom:1px solid #ffffff18;text-align:left}}a{{color:#8edcff}}@media(max-width:800px){{.grid{{grid-template-columns:1fr 1fr}}}}
</style></head><body><div class='wrap'><p><a href='/owner/shared-sky'>← Shared Skies owner controls</a></p><h1>Shared Skies Runtime & Operations</h1><p class='{'ok' if readiness else 'bad'}'>{'Core runtime prerequisites are configured.' if readiness else 'Runtime remains fail-closed until all required ingest/relay/scheduler prerequisites are configured.'}</p><div class='grid'>
<div class='card metric'><small>Projects</small><b>{counts.get('projects', 0)}</b></div><div class='card metric'><small>Destinations</small><b>{counts.get('destinations', 0)}</b></div><div class='card metric'><small>Live</small><b>{counts.get('live', 0)}</b></div><div class='card metric'><small>Schedules</small><b>{counts.get('schedules', 0)}</b></div></div>
<div class='card'><h2>Deployment readiness</h2><p>Ingest configured: <b>{deployment['ingest_configured']}</b> · Relay enabled: <b>{deployment['relay_enabled']}</b> · FFmpeg available: <b>{deployment['ffmpeg_available']}</b> · Scheduler enabled: <b>{deployment['scheduler_enabled']}</b></p><p>Multi-host admission cap: <b>{deployment['multi_host_participant_capacity']}</b> total participants (0 = disabled) · Relay mode: {escape(str(relay_state.get('runtime_mode', 'unknown')))} · Active outputs: {escape(str(relay_state.get('active_outputs', 0)))}</p></div>
<div class='card'><h2>Scheduler workers</h2><p>Healthy workers: <b>{scheduler['healthy_workers']}</b> · Poll {scheduler['poll_seconds']}s · Lease {scheduler['lease_seconds']}s · Max attempts {scheduler['max_attempts']}</p><table><thead><tr><th>Worker</th><th>Status</th><th>Health</th><th>Last seen</th></tr></thead><tbody>{worker_rows}</tbody></table></div>
<div class='card'><h2>Live broadcasts</h2><table><thead><tr><th>Title</th><th>Broadcast ID</th><th>Started</th></tr></thead><tbody>{live_rows}</tbody></table></div>
<div class='card'><h2>Release truth</h2><p>Provider app/OAuth approval, production ingest, WebRTC/SFU guest media and pre-recorded playout remain separate deployment/provider gates. This page reports their state; it does not fabricate them.</p></div></div></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


def install_shared_sky_owner_ops(app: Any) -> None:
    """Bind owner runtime handlers directly to the canonical FastAPI app once."""
    existing = {
        (getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", set()) or set())))
        for route in app.router.routes
    }
    if ("/owner/shared-sky/api/runtime", ("GET",)) not in existing:
        app.add_api_route(
            "/owner/shared-sky/api/runtime",
            owner_shared_sky_runtime,
            methods=["GET"],
            tags=["Shared Skies Owner Operations"],
        )
    if ("/owner/shared-sky/runtime", ("GET",)) not in existing:
        app.add_api_route(
            "/owner/shared-sky/runtime",
            owner_shared_sky_runtime_page,
            methods=["GET"],
            response_class=HTMLResponse,
            include_in_schema=False,
            tags=["Shared Skies Owner Operations"],
        )


def install_shared_sky_media_plane(app: Any) -> None:
    """Mount Chat 10's fail-closed ingest/media-node control plane on the canonical app."""
    existing = {
        (getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", set()) or set())))
        for route in app.router.routes
    }
    for route in shared_sky_media_plane_router.routes:
        signature = (getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", set()) or set())))
        if signature not in existing:
            app.router.routes.append(route)
            existing.add(signature)


# ``app.py`` imports the canonical app before importing this module, so the app is fully created.
# Preserve neighbouring Shared Sky bootstraps and mount Chat 3 Studio routes on the same canonical
# application. Every handler retains its own membership/owner/node-secret gate.
from .api import app as _canonical_app
from .shared_sky_chat2_studio_integration import install_chat2_studio_integration
from .shared_sky_chat2_studio_operator import install_chat2_studio_operator
from .shared_sky_control_room import install_shared_sky_control_room
from .shared_sky_control_room_extensions import install_shared_sky_control_room_extensions
from .shared_sky_operator_profiles import install_shared_sky_operator_profiles
from .shared_sky_professional_canvas import install_shared_sky_professional_canvas
from .shared_sky_professional_operator_ui import install_professional_operator_ui
from .shared_sky_professional_transport_toolbar import install_professional_transport_toolbar
from .shared_sky_studio_history_graphics import install_shared_sky_studio_history_graphics
from .shared_sky_studio_ingest import install_shared_sky_studio_ingest
from .shared_sky_studio_recovery_hardening import install_history_recovery_versioning

install_history_recovery_versioning()
install_shared_sky_battle_routes(_canonical_app)
install_shared_sky_live_community(_canonical_app)
install_shared_sky_owner_ops(_canonical_app)
install_shared_sky_media_plane(_canonical_app)
install_shared_sky_control_room(_canonical_app)
install_shared_sky_control_room_extensions(_canonical_app)
install_shared_sky_operator_profiles(_canonical_app)
install_shared_sky_professional_canvas(_canonical_app)
install_professional_transport_toolbar(_canonical_app)
install_professional_operator_ui(_canonical_app)
install_shared_sky_studio_history_graphics(_canonical_app)
install_shared_sky_studio_ingest(_canonical_app)
install_chat2_studio_integration(_canonical_app)
install_chat2_studio_operator(_canonical_app)
install_shared_skies_branding(_canonical_app)


__all__ = ["install_shared_sky_media_plane", "install_shared_sky_owner_ops", "router"]
