from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .owner_auth import owner_authorized
from .owner_identity import owner_actor
from .safety_reports import SafetyReportStore, _iso, _validate_ref

router = APIRouter(prefix="/owner/safety", tags=["Owner Safety Appeal Review"])


class OwnerAppealReviewInput(BaseModel):
    status: Literal["under_review", "upheld", "overturned"]
    reason: str = Field(min_length=1, max_length=2000)
    resolution_reference: str = Field(default="", max_length=160)


def _require_owner(request: Request) -> None:
    if not owner_authorized(request):
        raise HTTPException(403, "Owner authorization required")


def review_appeal(
    store: SafetyReportStore,
    *,
    appeal_id: str,
    status: str,
    reason: str,
    resolution_reference: str = "",
    reviewer_actor: str = "ESP Owner",
) -> dict:
    reason = str(reason or "").strip()
    reviewer_actor = str(reviewer_actor or "ESP Owner").strip() or "ESP Owner"
    if len(reviewer_actor) > 120:
        raise ValueError("Reviewer actor label is too long")
    if status not in {"under_review", "upheld", "overturned"}:
        raise ValueError("Unsupported appeal review state")
    if not reason:
        raise ValueError("Appeal review reason is required")
    resolution_reference = _validate_ref(
        resolution_reference, field_name="Appeal resolution reference", required=False
    )
    if status in {"upheld", "overturned"} and not resolution_reference:
        raise ValueError("A terminal appeal decision requires an opaque resolution evidence reference")

    with store._connect() as con:
        appeal = con.execute("SELECT * FROM safety_report_appeals WHERE id=?", (appeal_id,)).fetchone()
        if not appeal:
            raise KeyError("Safety appeal not found")
        current = str(appeal["status"])
        if current in {"upheld", "overturned"} and status != current:
            raise ValueError("Terminal appeal decisions cannot be changed by this endpoint")
        now = _iso()
        con.execute("UPDATE safety_report_appeals SET status=?,updated_at=? WHERE id=?", (status, now, appeal_id))
        store._append_event(
            con,
            report_id=str(appeal["report_id"]),
            actor_type="owner",
            action="appeal_review",
            data={
                "appeal_id": appeal_id,
                "from": current,
                "to": status,
                "reason": reason,
                "resolution_reference": resolution_reference,
                "reviewer_actor": reviewer_actor,
                "automatic_moderation_action_taken": False,
                "report_state_automatically_changed": False,
            },
        )
        updated = dict(con.execute("SELECT * FROM safety_report_appeals WHERE id=?", (appeal_id,)).fetchone())
    return {
        "appeal": updated,
        "automatic_moderation_action_taken": False,
        "report_state_automatically_changed": False,
        "grants_esp_role_or_permission": False,
        "changes_billing_or_membership": False,
    }


@router.post("/appeals/{appeal_id}/review", include_in_schema=False)
def owner_review_safety_appeal(appeal_id: str, request: Request, payload: OwnerAppealReviewInput):
    _require_owner(request)
    try:
        return review_appeal(
            SafetyReportStore(),
            appeal_id=appeal_id,
            status=payload.status,
            reason=payload.reason,
            resolution_reference=payload.resolution_reference,
            reviewer_actor=owner_actor(),
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


__all__ = ["OwnerAppealReviewInput", "review_appeal", "router"]
