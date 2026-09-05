from __future__ import annotations

"""Owner-controlled recruitment lead routing for Chat 9.

This module deliberately does not invent an automatic routing or capacity model. It exposes
currently eligible ESP Agent/Both/Owner accounts to an authenticated Owner, reports region
alignment as advisory metadata only, and performs an optimistic, audited manual transfer.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .esp_product_workflows import StaleVersionError, workflows
from .esp_product_workflows_lead_hardening import _assignment_membership_allowed
from .owner_identity import owner_actor, owner_session_authorized

router = APIRouter(tags=["Chat 9 Owner Lead Routing"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _region_alignment(lead_region: str | None, agent_region: str | None) -> str:
    lead = _normalized(lead_region)
    agent = _normalized(agent_region)
    if not lead or not agent:
        return "unknown"
    return "match" if lead == agent else "different"


def _routing_limits() -> dict:
    return {
        "automatic_assignment": False,
        "region": "advisory_only",
        "capacity": "not_modeled",
        "recruiting_niche_specialization": "not_modeled",
        "selection_authority": "authenticated_owner_only",
    }


def _require_owner(request: Request) -> None:
    if not owner_session_authorized(request):
        raise HTTPException(403, "Owner session required")


class LeadTransferRequest(BaseModel):
    expected_version: int = Field(ge=1)
    assigned_agent_user_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=2, max_length=1000)


def routing_candidates(lead_id: str) -> dict:
    lead = workflows.lead(lead_id)
    with workflows._connect() as con:
        rows = con.execute(
            """
            SELECT u.id AS user_id,u.display_name,m.status,m.roles,m.region
              FROM esp_memberships m
              JOIN users u ON u.id=m.user_id
             WHERE m.status='owner'
                OR (m.status='active' AND lower(trim(COALESCE(m.roles,''))) IN ('agent','both'))
             ORDER BY u.display_name COLLATE NOCASE,u.id
            """
        ).fetchall()
    candidates = []
    for row in rows:
        item = dict(row)
        item["region_alignment"] = _region_alignment(lead.get("region"), item.get("region"))
        item["currently_assigned"] = item["user_id"] == lead.get("assigned_agent_user_id")
        candidates.append(item)
    return {
        "lead": {
            "id": lead["id"],
            "platform": lead["platform"],
            "handle": lead["handle"],
            "region": lead.get("region") or "",
            "niche": lead.get("niche") or "",
            "assigned_agent_user_id": lead.get("assigned_agent_user_id"),
            "version": int(lead["version"]),
        },
        "eligible_assignees": candidates,
        "routing_limits": _routing_limits(),
    }


def transfer_lead(
    lead_id: str,
    *,
    expected_version: int,
    assigned_agent_user_id: str,
    reason: str,
    actor: str,
) -> dict:
    if not _assignment_membership_allowed(workflows, assigned_agent_user_id):
        raise ValueError("Lead assignee must have active ESP Agent/Both/Owner authority")
    clean_reason = " ".join((reason or "").split())[:1000]
    if len(clean_reason) < 2:
        raise ValueError("Transfer reason is required")

    with workflows._connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM esp_recruitment_leads WHERE id=?", (lead_id,)).fetchone()
        if row is None:
            raise KeyError("Lead not found")
        if int(row["version"]) != int(expected_version):
            raise StaleVersionError("stale_version")
        previous_agent = row["assigned_agent_user_id"]
        if previous_agent == assigned_agent_user_id:
            raise ValueError("Lead is already assigned to this Agent")

        membership = workflows.esp.membership(assigned_agent_user_id) or {}
        alignment = _region_alignment(row["region"], membership.get("region"))
        now = _now()
        con.execute(
            """UPDATE esp_recruitment_leads
                  SET assigned_agent_user_id=?,version=version+1,updated_at=?
                WHERE id=?""",
            (assigned_agent_user_id, now, lead_id),
        )
        workflows._lead_event(
            con,
            lead_id,
            actor,
            "lead_transferred",
            {
                "previous_agent_user_id": previous_agent,
                "assigned_agent_user_id": assigned_agent_user_id,
                "reason": clean_reason,
                "region_alignment": alignment,
                "capacity_model": "not_modeled",
                "recruiting_niche_specialization": "not_modeled",
            },
        )

    workflows.audit.append(
        actor=actor,
        action="chat9.lead_transferred",
        details={
            "lead_id": lead_id,
            "previous_agent_user_id": previous_agent,
            "assigned_agent_user_id": assigned_agent_user_id,
            "reason": clean_reason[:300],
            "region_alignment": alignment,
        },
    )
    return {
        "lead": workflows.lead(lead_id),
        "routing_advisory": {
            "region_alignment": alignment,
            **_routing_limits(),
        },
    }


@router.get("/owner/api/chat9-lead-routing/{lead_id}")
def owner_lead_routing_candidates(lead_id: str, request: Request):
    _require_owner(request)
    try:
        return routing_candidates(lead_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/owner/api/chat9-lead-routing/{lead_id}")
def owner_transfer_lead(lead_id: str, body: LeadTransferRequest, request: Request):
    _require_owner(request)
    try:
        return transfer_lead(
            lead_id,
            expected_version=body.expected_version,
            assigned_agent_user_id=body.assigned_agent_user_id,
            reason=body.reason,
            actor=owner_actor("ESP Owner"),
        )
    except StaleVersionError as exc:
        raise HTTPException(409, {"code": "stale_version"}) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


__all__ = ["router", "routing_candidates", "transfer_lead", "LeadTransferRequest"]
