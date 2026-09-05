from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .aura_live_guardian_monitor import live_guardian_monitor
from .aura_live_guardian_status import build_live_session_status


def live_guardian_status(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")

    query_params = getattr(request, "query_params", {})
    if query_params.get("view") == "monitor":
        return live_guardian_monitor(request)

    database = Path(os.getenv("AURA_LIVE_MODERATOR_DB", "data/aura_live_moderator.sqlite3"))
    database.parent.mkdir(parents=True, exist_ok=True)
    status = build_live_session_status(database=database, user_id=member.user_id)
    return JSONResponse(
        {
            "safety_state": status.safety_state,
            "pre_live_ready": status.pre_live_ready,
            "provider_execution_ready": status.provider_execution_ready,
            "pending_reviews": status.pending_reviews,
            "critical_escalations": status.critical_escalations,
            "audit_integrity_ok": status.audit_integrity_ok,
            "mode": status.mode,
            "message": status.message,
        },
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


__all__ = ["live_guardian_status"]
