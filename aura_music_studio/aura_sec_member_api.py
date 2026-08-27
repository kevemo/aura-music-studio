from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .accounts import AccountStore
from .aura_sec_approval import AuraSecApprovalGateway
from .aura_sec_approval_portal import router as approval_portal_router
from .aura_sec_catalog import AuraSecCatalog
from .aura_sec_health import evaluate_device_health, summarize_security_health
from .aura_sec_read_model import AuraSecReadModel
from .aura_sec_recovery import AuraSecRecoveryStore
from .aura_sec_store import AuraSecStore
from .aura_sec_vulnerability import VulnerabilityFinding, prioritize_vulnerability
from .aura_sec_vulnerability_store import AuraSecVulnerabilityStore
from .aura_sec_workflow_portal import router as workflow_portal_router

router = APIRouter(tags=["Aura Sec Member"])
api_router = APIRouter(prefix="/api/aura-sec/member", tags=["Aura Sec Member State"])
accounts = AccountStore()
security = AuraSecStore(accounts)
read_model = AuraSecReadModel(accounts)
recovery = AuraSecRecoveryStore(accounts, security)
vulnerabilities = AuraSecVulnerabilityStore(accounts, security)
approvals = AuraSecApprovalGateway(accounts, security)
MEMBER_COOKIE = "lss_session"


class ApprovalConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    approval_token: str = Field(min_length=16, max_length=256)
    password: str | None = Field(default=None, min_length=1, max_length=1024)


def _session_context(request: Request) -> tuple[dict, str]:
    token = request.cookies.get(MEMBER_COOKIE)
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    user = accounts.resolve_session(token)
    if not user or not token:
        raise HTTPException(401, "Sign in required")
    return user, token


def _session_user(request: Request) -> dict:
    return _session_context(request)[0]


@api_router.get("/licence")
def member_security_licence(request: Request):
    user = _session_user(request)
    licence = security.licence(user["id"])
    return {
        "licence": licence,
        "creative_membership_is_security_entitlement": False,
        "message": (
            "Aura Sec is a separate purchase. Creative membership level does not activate endpoint protection."
            if licence.get("status") != "active"
            else "Aura Sec licence is active; device protection still depends on enrolled clients with fresh verified heartbeats."
        ),
    }


@api_router.get("/catalog")
def member_security_catalog(request: Request):
    _session_user(request)
    try:
        return AuraSecCatalog.from_environment().public_state()
    except ValueError as exc:
        return {
            "sale_configured": False,
            "skus": [],
            "creative_plan_ids_accepted_as_security_sku": False,
            "checkout_enabled": False,
            "configuration_error": True,
            "truth": f"Aura Sec sale configuration is invalid and checkout remains disabled: {exc}",
        }


@api_router.get("/devices")
def member_security_devices(request: Request):
    user = _session_user(request)
    devices = security.list_devices(user["id"])
    return summarize_security_health(devices)


@api_router.get("/devices/{device_id}")
def member_security_device_detail(device_id: str, request: Request):
    user = _session_user(request)
    try:
        device = security.get_device(user["id"], device_id)
    except ValueError as exc:
        raise HTTPException(404, "Aura Sec device not found") from exc
    health = evaluate_device_health(device).__dict__.copy()
    findings = vulnerabilities.list(user["id"], device_id=device_id, status="open", limit=250)
    incidents = [
        item for item in read_model.incidents(user["id"], limit=500) if item.get("device_id") == device_id
    ]
    actions = [
        item for item in read_model.actions(user["id"], limit=500) if item.get("device_id") == device_id
    ]
    readiness = (
        {
            "state": "not_managed",
            "truth": "Recovery readiness is not evaluated for a revoked device.",
        }
        if device.get("status") == "revoked"
        else recovery.readiness(user["id"], device_id)
    )
    return {
        "device": device,
        "health": health,
        "vulnerabilities": {
            "count": len(findings),
            "urgent": sum(
                1 for item in findings if item.get("priority") in {"critical", "emergency", "incident"}
            ),
            "findings": findings,
        },
        "incidents": {
            "count": len(incidents),
            "open": sum(1 for item in incidents if item.get("status") == "open"),
            "items": incidents[:100],
        },
        "actions": {
            "count": len(actions),
            "awaiting_approval": sum(1 for item in actions if item.get("status") == "proposed"),
            "items": actions[:100],
        },
        "recovery": readiness,
        "truth": (
            "This device view aggregates member-owned verified state. It does not change the endpoint, approve an action, "
            "or infer health from the browser session."
        ),
    }


@api_router.get("/enrolment-readiness")
def member_security_enrolment_readiness(request: Request):
    user = _session_user(request)
    licence = security.licence(user["id"])
    devices = [item for item in security.list_devices(user["id"]) if item.get("status") != "revoked"]
    limit = int(licence.get("device_limit") or 0)
    remaining = max(0, limit - len(devices))
    return {
        "licence_status": licence.get("status") or "not_purchased",
        "device_limit": limit,
        "active_device_count": len(devices),
        "remaining_device_slots": remaining,
        "can_request_native_enrolment": licence.get("status") == "active" and remaining > 0,
        "browser_can_complete_device_attestation": False,
        "signed_native_client_released": False,
        "truth": (
            "The browser can display enrolment readiness but cannot attest a device. "
            "Completion requires the signed native client and verified device proof."
        ),
    }


@api_router.get("/overview")
def member_security_overview(request: Request):
    user = _session_user(request)
    licence = security.licence(user["id"])
    devices = security.list_devices(user["id"])
    health = summarize_security_health(devices)
    protection_active = licence.get("status") == "active" and health.get("healthy_devices", 0) > 0
    return {
        "licence": licence,
        "health": health,
        "at_least_one_device_currently_verified_healthy": protection_active,
        "all_managed_devices_currently_verified_healthy": (
            licence.get("status") == "active"
            and health.get("managed_devices", 0) > 0
            and health.get("overall") == "healthy"
        ),
        "truth": (
            "Protection status is derived from separate licence state plus fresh verified native-client heartbeat evidence."
        ),
    }


@api_router.get("/incidents")
def member_verified_incidents(request: Request):
    user = _session_user(request)
    incidents = read_model.incidents(user["id"], limit=250)
    counts = read_model.counts(user["id"])
    return {
        "incidents": incidents,
        "count": len(incidents),
        "open": counts["incidents"]["open"],
        "urgent": counts["incidents"]["urgent"],
        "truth": "Only persisted member-owned incident evidence appears here; loading the API cannot create a detection.",
    }


@api_router.get("/incidents/{incident_id}")
def member_verified_incident_detail(incident_id: str, request: Request):
    user = _session_user(request)
    incident = read_model.incident(user["id"], incident_id)
    if not incident:
        raise HTTPException(404, "Aura Sec incident not found")
    actions = read_model.actions_for_incident(user["id"], incident_id, limit=250)
    return {
        "incident": incident,
        "actions": actions,
        "response_summary": {
            "linked_actions": len(actions),
            "proposed": sum(1 for item in actions if item.get("status") == "proposed"),
            "approved": sum(1 for item in actions if item.get("status") == "approved"),
            "executed": sum(1 for item in actions if item.get("status") == "executed"),
            "verified": sum(1 for item in actions if item.get("status") == "verified"),
        },
        "truth": (
            "Incident evidence, Aura explanation, proposed actions, execution and verification are separate states. "
            "This read-only endpoint cannot execute remediation."
        ),
    }


@api_router.get("/actions/awaiting-approval")
def member_actions_awaiting_approval(request: Request):
    user = _session_user(request)
    actions = [
        item for item in read_model.actions(user["id"], limit=500) if item.get("status") == "proposed"
    ]
    return {
        "actions": actions,
        "count": len(actions),
        "approval_endpoint_exposed_here": False,
        "generic_command_execution_available": False,
        "truth": (
            "This endpoint only projects proposed bounded actions. Approval must pass the dedicated policy and authentication "
            "path; loading this list cannot approve or execute anything."
        ),
    }


@api_router.post("/actions/{action_id}/approval-challenge")
def member_action_approval_challenge(action_id: str, request: Request):
    user, session_token = _session_context(request)
    try:
        challenge = approvals.create_challenge(
            user["id"],
            action_id,
            session_token=session_token,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {
        **challenge,
        "generic_command_execution_available": False,
        "truth": (
            "This one-time challenge authorizes review of one bounded action only. "
            "It cannot issue or execute a native command."
        ),
    }


@api_router.post("/actions/{action_id}/approve")
def member_action_approve(action_id: str, payload: ApprovalConfirmRequest, request: Request):
    user, session_token = _session_context(request)
    try:
        return approvals.approve(
            user["id"],
            action_id,
            session_token=session_token,
            approval_token=payload.approval_token,
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@api_router.get("/vulnerabilities")
def member_verified_vulnerabilities(request: Request):
    user = _session_user(request)
    findings = vulnerabilities.list(user["id"], status="open", limit=250)
    return {
        "findings": findings,
        "count": len(findings),
        "critical_or_emergency": sum(
            1 for item in findings if item.get("priority") in {"critical", "emergency", "incident"}
        ),
        "known_exploited": sum(1 for item in findings if item.get("cisa_kev")),
        "truth": (
            "Only device-applicable findings backed by verified native inventory and threat-intelligence evidence appear here."
        ),
    }


@api_router.get("/vulnerabilities/{finding_id}")
def member_verified_vulnerability_detail(finding_id: str, request: Request):
    user = _session_user(request)
    try:
        finding = vulnerabilities.get(user["id"], finding_id)
    except ValueError as exc:
        raise HTTPException(404, "Aura Sec vulnerability finding not found") from exc
    return {
        "finding": finding,
        "automatic_patch_permission": False,
        "resolution_requires_verified_fixed_version": True,
        "truth": (
            "Priority is advisory. A finding cannot become resolved until signed native evidence verifies the installed version is fixed."
        ),
    }


@api_router.post("/vulnerability-priority")
def vulnerability_priority(payload: VulnerabilityFinding, request: Request):
    _session_user(request)
    return prioritize_vulnerability(payload).model_dump()


router.include_router(api_router)
router.include_router(workflow_portal_router)
router.include_router(approval_portal_router)


__all__ = ["router"]
