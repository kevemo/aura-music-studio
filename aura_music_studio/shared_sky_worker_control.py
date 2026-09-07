from __future__ import annotations

from fastapi import HTTPException, Request

from .owner_identity import owner_session_authorized
from .shared_sky_worker import SharedSkyWorker

worker = SharedSkyWorker()


def _owner(request: Request) -> None:
    if not owner_session_authorized(request):
        raise HTTPException(401, "Owner authentication required")


def _safe_health(rows: list[dict]) -> list[dict]:
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


def _safe_run_result(result: dict) -> dict:
    return {
        "claimed": bool(result.get("claimed")),
        "ok": result.get("ok"),
        "schedule_id": result.get("schedule_id"),
        "broadcast_id": result.get("broadcast_id"),
        "error_present": bool(str(result.get("error") or "")),
    }


def scheduler_status(request: Request):
    _owner(request)
    settings = worker.settings
    health = _safe_health(worker.worker_health())
    return {
        "scheduler": {
            "enabled": settings.enabled,
            "runtime_mode": "dedicated-worker",
            "poll_seconds": settings.poll_seconds,
            "lease_seconds": settings.lease_seconds,
            "max_attempts": settings.max_attempts,
            "retry_seconds": settings.retry_seconds,
            "workers": health,
            "healthy_workers": sum(1 for row in health if row["healthy"]),
            "raw_worker_errors_exposed": False,
        }
    }


def scheduler_run_due(request: Request):
    _owner(request)
    if not worker.settings.enabled:
        raise HTTPException(
            503,
            "Shared Sky scheduler is disabled until relay/ingest/provider prerequisites are validated",
        )
    try:
        return _safe_run_result(worker.run_once())
    except Exception as exc:
        worker.heartbeat(status="error", error=str(exc))
        raise HTTPException(
            503,
            "Shared Sky scheduler run failed; inspect server-side worker evidence",
        ) from exc


__all__ = ["scheduler_status", "scheduler_run_due", "worker"]
