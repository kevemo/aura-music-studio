from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Mapping

from .aura_sec_reputation_providers import (
    ProviderHealthEvidence,
    ProviderReadiness,
    cloud_reputation_readiness,
    provider_registry_snapshot,
)


def _safe_provider(item: ProviderReadiness) -> dict:
    """Return member-safe readiness only; never expose credential names or values."""

    return {
        "provider_id": item.provider_id,
        "display_name": item.display_name,
        "kind": item.kind,
        "state": item.state,
        "enabled": item.enabled,
        "commercial_runtime_allowed": item.commercial_runtime_allowed,
        "commercial_approval_recorded": item.commercial_approval_recorded,
        "credentials_configured": item.credentials_configured,
        "adapter_health_verified": item.adapter_health_verified,
        "ready": item.ready,
        "credential_names_exposed": False,
        "credential_values_exposed": False,
        "raw_provider_response_exposed": False,
    }


def browser_guard_cloud_status(
    *,
    env: Mapping[str, str] | None = None,
    health: Mapping[str, ProviderHealthEvidence] | None = None,
    now: datetime | None = None,
) -> dict:
    """Project provider readiness for the Browser Guard member/control-plane UI.

    This function performs no network lookups and cannot activate a provider. Readiness comes
    only from the server-owned commercial/provider registry plus fresh health evidence supplied
    by trusted adapter infrastructure.
    """

    current = now or datetime.now(timezone.utc)
    snapshot = provider_registry_snapshot(env=env, health=health, now=current)
    cloud = cloud_reputation_readiness(env=env, health=health, now=current)
    state_counts = Counter(item.state for item in snapshot)
    providers = [_safe_provider(item) for item in snapshot]

    active = bool(cloud["cloud_reputation_active"])
    corroboration = bool(cloud["independent_corroboration_ready"])
    if corroboration:
        state = "corroboration_ready"
        truth = (
            "At least two independently configured reputation providers have fresh verified adapter health. "
            "This readiness state does not mean every URL is malicious or safe; each lookup still requires verified evidence and fusion policy."
        )
    elif active:
        state = "single_provider_ready"
        truth = (
            "One cloud reputation provider has fresh verified adapter health. Single-provider harmful evidence is not automatically treated as corroborated block authority."
        )
    else:
        state = "inactive"
        truth = (
            "Cloud reputation is not active. Browser Guard remains local-first and does not claim cloud verification until a commercially approved, credentialed provider has fresh verified adapter health."
        )

    return {
        "engine_state": "local_inspection_available",
        "manual_link_inspection": True,
        "server_fetches_submitted_url": False,
        "cloud_reputation_state": state,
        "cloud_reputation_active": active,
        "independent_corroboration_ready": corroboration,
        "ready_provider_ids": list(cloud["ready_provider_ids"]),
        "provider_count_ready": int(cloud["provider_count_ready"]),
        "provider_count_known": int(cloud["provider_count_known"]),
        "provider_state_counts": dict(sorted(state_counts.items())),
        "providers": providers,
        "automatic_navigation_protection": False,
        "automatic_browser_extension_protection_released": False,
        "signed_download_available": False,
        "credential_names_exposed": False,
        "credential_values_exposed": False,
        "raw_provider_responses_exposed": False,
        "provider_execution_available_from_status_view": False,
        "truth": truth,
    }


__all__ = ["browser_guard_cloud_status"]
