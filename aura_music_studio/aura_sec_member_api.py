from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .accounts import AccountStore
from .aura_sec_catalog import AuraSecCatalog
from .aura_sec_health import summarize_security_health
from .aura_sec_store import AuraSecStore
from .aura_sec_vulnerability import VulnerabilityFinding, prioritize_vulnerability
from .aura_sec_vulnerability_store import AuraSecVulnerabilityStore

router = APIRouter(prefix="/api/aura-sec/member", tags=["Aura Sec Member State"])
accounts = AccountStore()
security = AuraSecStore(accounts)
vulnerabilities = AuraSecVulnerabilityStore(accounts, security)
MEMBER_COOKIE = "lss_session"


def _session_user(request: Request) -> dict:
    token = request.cookies.get(MEMBER_COOKIE)
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    user = accounts.resolve_session(token)
    if not user:
        raise HTTPException(401, "Sign in required")
    return user


@router.get("/licence")
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


@router.get("/catalog")
def member_security_catalog(request: Request):
    _session_user(request)
    try:
        return AuraSecCatalog.from_environment().public_state()
    except ValueError as exc:
        # Invalid commercial configuration fails closed instead of exposing a partially
        # configured product or falling back to creative-plan pricing.
        return {
            "sale_configured": False,
            "skus": [],
            "creative_plan_ids_accepted_as_security_sku": False,
            "checkout_enabled": False,
            "configuration_error": True,
            "truth": f"Aura Sec sale configuration is invalid and checkout remains disabled: {exc}",
        }


@router.get("/devices")
def member_security_devices(request: Request):
    user = _session_user(request)
    devices = security.list_devices(user["id"])
    return summarize_security_health(devices)


@router.get("/overview")
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


@router.get("/vulnerabilities")
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


@router.post("/vulnerability-priority")
def vulnerability_priority(payload: VulnerabilityFinding, request: Request):
    _session_user(request)
    return prioritize_vulnerability(payload).model_dump()


__all__ = ["router"]
