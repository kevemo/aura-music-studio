from __future__ import annotations

from dataclasses import asdict, dataclass

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .owner_auth import owner_authorized

RUNTIME_POLICY_VERSION = "aura-self-hosted-safeguarding-v1-2026-08-29"


@dataclass(frozen=True)
class SafeguardingComponent:
    component_id: str
    purpose: str
    execution_surface: str
    critical: bool = True
    self_hosted: bool = True
    remote_dependency_required: bool = False
    external_can_override_local_block: bool = False


CORE_COMPONENTS: tuple[SafeguardingComponent, ...] = (
    SafeguardingComponent(
        "creative_request_safety",
        "High-confidence abuse, non-consensual intimate synthesis, fraud/impersonation and professional-creation policy preflight.",
        "local_python",
    ),
    SafeguardingComponent(
        "creative_ip_rights_preflight",
        "Copyright, imitation, reference-rights and likeness/voice authorization preflight.",
        "local_python",
    ),
    SafeguardingComponent(
        "creative_route_safety_overlay",
        "First-match creative admission gate before billing, capacity reservation, project mutation or renderer submission.",
        "local_fastapi",
    ),
    SafeguardingComponent(
        "privacy_rights_case_management",
        "Privacy requests, identity/context review, holds, fulfilment evidence and tamper-evident case events.",
        "local_sqlite_and_python",
    ),
    SafeguardingComponent(
        "privacy_access_portability_fulfilment",
        "Allowlisted privacy access/portability packages and delivery-integrity evidence.",
        "local_sqlite_and_python",
    ),
    SafeguardingComponent(
        "safety_reports_and_appeals",
        "Member safety reports, owner review, appeals and hash-chained evidence.",
        "local_sqlite_and_python",
    ),
    SafeguardingComponent(
        "ip_notice_and_counter_notice",
        "IP-rights notices, counter-notices and review evidence without automatic takedown/restoration.",
        "local_sqlite_and_python",
    ),
    SafeguardingComponent(
        "export_provenance",
        "SHA-256 provenance, exact-duplicate evidence and commercial-review state.",
        "local_hashing_and_sqlite",
    ),
    SafeguardingComponent(
        "privacy_consent_and_gpc",
        "Regional consent preference evidence and GPC decision logic.",
        "local_sqlite_and_python",
    ),
    SafeguardingComponent(
        "esp_live_compliance",
        "ESP LIVE compliance training, acknowledgement and readiness evidence.",
        "local_sqlite_and_python",
    ),
    SafeguardingComponent(
        "aura_live_guardian_decisioning",
        "Creator-configured LIVE Guardian policy, local moderation decisioning, review queues and audit evidence.",
        "local_sqlite_and_python",
    ),
)

# Some decisions still require qualified people, platform-side execution or optional external
# evidence. Those dependencies may add review/restrictions but must never become the sole core
# safeguarding engine or disable a local Aura block decision.
EXTERNAL_EVIDENCE_BOUNDARIES: tuple[dict[str, object], ...] = (
    {
        "boundary_id": "qualified_legal_review",
        "may_be_required": True,
        "reason": "Jurisdiction-specific legal conclusions and release decisions can require a qualified human reviewer.",
        "core_execution_dependency": False,
        "can_disable_local_safety_block": False,
    },
    {
        "boundary_id": "platform_side_enforcement",
        "may_be_required": True,
        "reason": "TikTok and other platforms remain authoritative for their own moderation, account and LIVE actions.",
        "core_execution_dependency": False,
        "can_disable_local_safety_block": False,
    },
    {
        "boundary_id": "external_similarity_or_catalog_review",
        "may_be_required": False,
        "reason": "Commercial export review may optionally use external similarity or licensed-catalog evidence.",
        "core_execution_dependency": False,
        "can_disable_local_safety_block": False,
    },
)


def self_hosted_core_failures(
    components: tuple[SafeguardingComponent, ...] = CORE_COMPONENTS,
) -> tuple[str, ...]:
    failures: list[str] = []
    for component in components:
        if not component.critical:
            continue
        if not component.self_hosted:
            failures.append(f"{component.component_id}:critical_component_not_self_hosted")
        if component.remote_dependency_required:
            failures.append(f"{component.component_id}:remote_dependency_required")
        if component.external_can_override_local_block:
            failures.append(f"{component.component_id}:external_override_enabled")
    return tuple(failures)


def assert_self_hosted_core_ready(
    components: tuple[SafeguardingComponent, ...] = CORE_COMPONENTS,
) -> None:
    failures = self_hosted_core_failures(components)
    if failures:
        raise RuntimeError(
            "Aura safeguarding core is not self-hosted/fail-closed: " + ", ".join(failures)
        )


def build_safeguarding_runtime_status() -> dict:
    failures = self_hosted_core_failures()
    return {
        "policy_version": RUNTIME_POLICY_VERSION,
        "self_hosted_by_aura": True,
        "core_ready": not failures,
        "core_execution_offline_capable": not failures,
        "remote_moderation_api_required": False,
        "external_services_are_not_core_execution_dependencies": True,
        "local_safety_blocks_are_authoritative": True,
        "external_evidence_can_add_restrictions_but_not_disable_local_blocks": True,
        "components": [asdict(component) for component in CORE_COMPONENTS],
        "external_evidence_boundaries": [dict(item) for item in EXTERNAL_EVIDENCE_BOUNDARIES],
        "failures": list(failures),
        "legal_certification": False,
        "legal_advice": False,
        "note": (
            "Aura runs the critical safeguarding engine locally. Qualified legal review, platform-side enforcement "
            "and optional external similarity/catalog evidence remain separate controlled inputs and cannot replace "
            "or weaken local safety decisions."
        ),
    }


owner_router = APIRouter(prefix="/owner/safeguarding", tags=["Owner Safeguarding Runtime"])


@owner_router.get("/runtime", include_in_schema=False)
def owner_safeguarding_runtime(request: Request):
    if not owner_authorized(request):
        raise HTTPException(403, "Owner authorization required")
    return JSONResponse(
        build_safeguarding_runtime_status(),
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


# Startup/release invariant: application import must fail closed if a future change turns a
# critical safeguard into remote-only execution or allows an external service to bypass Aura.
assert_self_hosted_core_ready()


__all__ = [
    "CORE_COMPONENTS",
    "EXTERNAL_EVIDENCE_BOUNDARIES",
    "RUNTIME_POLICY_VERSION",
    "SafeguardingComponent",
    "assert_self_hosted_core_ready",
    "build_safeguarding_runtime_status",
    "owner_router",
    "self_hosted_core_failures",
]
