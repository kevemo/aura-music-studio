from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Mapping


ProviderKind = Literal[
    "url_reputation",
    "phishing_feed",
    "domain_reputation",
    "malware_intelligence",
]
CommercialStatus = Literal[
    "commercial_product",
    "commercial_contract_required",
    "commercial_use_prohibited",
]
ProviderState = Literal[
    "blocked_commercial_use",
    "disabled",
    "commercial_approval_required",
    "credentials_missing",
    "configured_not_verified",
    "adapter_unhealthy",
    "ready",
]

_HEALTH_MAX_AGE = timedelta(minutes=15)
_HEALTH_FUTURE_SKEW = timedelta(minutes=2)
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise PermissionError("Aura Sec provider-health timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _flag(env: Mapping[str, str], name: str) -> bool:
    return str(env.get(name, "")).strip().lower() in _TRUE_VALUES


def _credential_present(env: Mapping[str, str], name: str) -> bool:
    return bool(str(env.get(name, "")).strip())


@dataclass(frozen=True)
class ReputationProviderSpec:
    """Static, server-owned provider metadata.

    This registry describes *readiness only*. It contains no network client and no credential
    values. Terms/licensing decisions remain owner/legal configuration, never browser input.
    """

    provider_id: str
    display_name: str
    kind: ProviderKind
    commercial_status: CommercialStatus
    enable_env_name: str
    commercial_approval_env_name: str | None
    credential_env_names: tuple[str, ...]
    network_mode: str
    data_use_note: str
    terms_basis: str

    def __post_init__(self) -> None:
        if not self.provider_id or self.provider_id.strip() != self.provider_id:
            raise ValueError("Aura Sec provider_id is invalid")
        if not self.display_name.strip():
            raise ValueError("Aura Sec provider display name is required")
        if not self.enable_env_name.startswith("AURA_SEC_"):
            raise ValueError("Aura Sec provider enable flag must use the Aura Sec namespace")
        if self.commercial_status == "commercial_use_prohibited":
            if self.commercial_approval_env_name is not None or self.credential_env_names:
                raise ValueError("Commercially prohibited provider cannot define runtime credentials")
        else:
            if not self.commercial_approval_env_name:
                raise ValueError("Commercial provider requires an explicit approval flag")
            if not self.credential_env_names:
                raise ValueError("Commercial provider requires a credential boundary")
        for name in self.credential_env_names:
            if not name.startswith("AURA_SEC_"):
                raise ValueError("Credential environment name must use the Aura Sec namespace")


@dataclass(frozen=True)
class ProviderHealthEvidence:
    """Short-lived internal evidence from a real provider adapter health check."""

    provider_id: str
    checked_at: datetime
    service_responding: bool
    credential_verified: bool
    transport_verified: bool

    def verified_at(self, *, now: datetime) -> bool:
        current = _utc(now)
        checked = _utc(self.checked_at)
        if checked > current + _HEALTH_FUTURE_SKEW:
            return False
        if current - checked > _HEALTH_MAX_AGE:
            return False
        return bool(
            self.service_responding and self.credential_verified and self.transport_verified
        )


@dataclass(frozen=True)
class ProviderReadiness:
    provider_id: str
    display_name: str
    kind: ProviderKind
    state: ProviderState
    enabled: bool
    commercial_runtime_allowed: bool
    commercial_approval_recorded: bool
    credentials_configured: bool
    adapter_health_verified: bool
    credential_env_names: tuple[str, ...]
    network_mode: str
    data_use_note: str
    terms_basis: str

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    def public_dict(self) -> dict:
        """Safe diagnostics: names and booleans only; never environment values."""
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "kind": self.kind,
            "state": self.state,
            "enabled": self.enabled,
            "commercial_runtime_allowed": self.commercial_runtime_allowed,
            "commercial_approval_recorded": self.commercial_approval_recorded,
            "credentials_configured": self.credentials_configured,
            "adapter_health_verified": self.adapter_health_verified,
            "credential_env_names": list(self.credential_env_names),
            "network_mode": self.network_mode,
            "data_use_note": self.data_use_note,
            "terms_basis": self.terms_basis,
            "credential_values_exposed": False,
        }


# These definitions are intentionally conservative. "Approval" is an internal commercial/legal
# release gate; it does not imply the provider granted a licence merely because an env flag exists.
PROVIDERS: dict[str, ReputationProviderSpec] = {
    "google-web-risk": ReputationProviderSpec(
        provider_id="google-web-risk",
        display_name="Google Web Risk",
        kind="url_reputation",
        commercial_status="commercial_product",
        enable_env_name="AURA_SEC_GOOGLE_WEB_RISK_ENABLED",
        commercial_approval_env_name="AURA_SEC_GOOGLE_WEB_RISK_COMMERCIAL_APPROVED",
        credential_env_names=("AURA_SEC_GOOGLE_WEB_RISK_CREDENTIAL",),
        network_mode="licensed_remote_lookup_or_update_adapter",
        data_use_note="Use only within the Google Cloud Web Risk agreement and configured caching/attribution rules.",
        terms_basis="Google directs commercial malicious-URL detection products to Web Risk.",
    ),
    "openphish-commercial": ReputationProviderSpec(
        provider_id="openphish-commercial",
        display_name="OpenPhish Commercial Feed",
        kind="phishing_feed",
        commercial_status="commercial_contract_required",
        enable_env_name="AURA_SEC_OPENPHISH_COMMERCIAL_ENABLED",
        commercial_approval_env_name="AURA_SEC_OPENPHISH_COMMERCIAL_APPROVED",
        credential_env_names=("AURA_SEC_OPENPHISH_COMMERCIAL_CREDENTIAL",),
        network_mode="licensed_feed_adapter",
        data_use_note="No redistribution or customer-facing reuse beyond the executed commercial agreement.",
        terms_basis="OpenPhish community/personal use is not a commercial-production entitlement; commercial use requires permission/feed terms.",
    ),
    "spamhaus-intelligence": ReputationProviderSpec(
        provider_id="spamhaus-intelligence",
        display_name="Spamhaus Intelligence API",
        kind="domain_reputation",
        commercial_status="commercial_contract_required",
        enable_env_name="AURA_SEC_SPAMHAUS_INTELLIGENCE_ENABLED",
        commercial_approval_env_name="AURA_SEC_SPAMHAUS_INTELLIGENCE_COMMERCIAL_APPROVED",
        credential_env_names=("AURA_SEC_SPAMHAUS_INTELLIGENCE_CREDENTIAL",),
        network_mode="licensed_remote_intelligence_adapter",
        data_use_note="Production/high-volume use requires the applicable commercial subscription and its data-use restrictions.",
        terms_basis="Spamhaus Developer Licence is evaluation-only; production/commercial use requires commercial access.",
    ),
    "abusech-commercial": ReputationProviderSpec(
        provider_id="abusech-commercial",
        display_name="abuse.ch Commercial API via Spamhaus",
        kind="malware_intelligence",
        commercial_status="commercial_contract_required",
        enable_env_name="AURA_SEC_ABUSECH_COMMERCIAL_ENABLED",
        commercial_approval_env_name="AURA_SEC_ABUSECH_COMMERCIAL_APPROVED",
        credential_env_names=("AURA_SEC_ABUSECH_COMMERCIAL_CREDENTIAL",),
        network_mode="licensed_remote_malware_intelligence_adapter",
        data_use_note="Commercial abuse.ch data is consumed through the licensed Spamhaus alliance service; community feeds are not silently repurposed.",
        terms_basis="Spamhaus provides a commercial abuse.ch API path for production use.",
    ),
    "google-safe-browsing": ReputationProviderSpec(
        provider_id="google-safe-browsing",
        display_name="Google Safe Browsing API",
        kind="url_reputation",
        commercial_status="commercial_use_prohibited",
        enable_env_name="AURA_SEC_GOOGLE_SAFE_BROWSING_ENABLED",
        commercial_approval_env_name=None,
        credential_env_names=(),
        network_mode="blocked_from_commercial_runtime",
        data_use_note="Do not enable for the revenue-generating Aura Sec product unless a separate Google agreement explicitly changes the applicable terms.",
        terms_basis="Google Safe Browsing API is non-commercial by default; Google directs commercial use to Web Risk.",
    ),
}


def provider_spec(provider_id: str) -> ReputationProviderSpec:
    try:
        return PROVIDERS[provider_id]
    except KeyError as exc:
        raise PermissionError("Unknown Aura Sec reputation provider") from exc


def provider_readiness(
    provider_id: str,
    *,
    env: Mapping[str, str] | None = None,
    health: Mapping[str, ProviderHealthEvidence] | None = None,
    now: datetime | None = None,
) -> ProviderReadiness:
    spec = provider_spec(provider_id)
    values = os.environ if env is None else env
    evidence_map = {} if health is None else health
    current = now or datetime.now(timezone.utc)

    if spec.commercial_status == "commercial_use_prohibited":
        return ProviderReadiness(
            provider_id=spec.provider_id,
            display_name=spec.display_name,
            kind=spec.kind,
            state="blocked_commercial_use",
            enabled=False,
            commercial_runtime_allowed=False,
            commercial_approval_recorded=False,
            credentials_configured=False,
            adapter_health_verified=False,
            credential_env_names=(),
            network_mode=spec.network_mode,
            data_use_note=spec.data_use_note,
            terms_basis=spec.terms_basis,
        )

    enabled = _flag(values, spec.enable_env_name)
    approval_name = spec.commercial_approval_env_name or ""
    approved = bool(approval_name and _flag(values, approval_name))
    configured = all(_credential_present(values, name) for name in spec.credential_env_names)

    evidence = evidence_map.get(spec.provider_id)
    health_verified = bool(
        evidence
        and evidence.provider_id == spec.provider_id
        and evidence.verified_at(now=current)
    )

    if not enabled:
        state: ProviderState = "disabled"
    elif not approved:
        state = "commercial_approval_required"
    elif not configured:
        state = "credentials_missing"
    elif evidence is None:
        state = "configured_not_verified"
    elif not health_verified:
        state = "adapter_unhealthy"
    else:
        state = "ready"

    return ProviderReadiness(
        provider_id=spec.provider_id,
        display_name=spec.display_name,
        kind=spec.kind,
        state=state,
        enabled=enabled,
        commercial_runtime_allowed=True,
        commercial_approval_recorded=approved,
        credentials_configured=configured,
        adapter_health_verified=health_verified,
        credential_env_names=spec.credential_env_names,
        network_mode=spec.network_mode,
        data_use_note=spec.data_use_note,
        terms_basis=spec.terms_basis,
    )


def provider_registry_snapshot(
    *,
    env: Mapping[str, str] | None = None,
    health: Mapping[str, ProviderHealthEvidence] | None = None,
    now: datetime | None = None,
) -> list[ProviderReadiness]:
    return [
        provider_readiness(provider_id, env=env, health=health, now=now)
        for provider_id in PROVIDERS
    ]


def cloud_reputation_readiness(
    *,
    env: Mapping[str, str] | None = None,
    health: Mapping[str, ProviderHealthEvidence] | None = None,
    now: datetime | None = None,
) -> dict:
    snapshot = provider_registry_snapshot(env=env, health=health, now=now)
    ready_ids = tuple(item.provider_id for item in snapshot if item.ready)
    return {
        "cloud_reputation_active": bool(ready_ids),
        "ready_provider_ids": list(ready_ids),
        "independent_corroboration_ready": len(ready_ids) >= 2,
        "automatic_browser_extension_protection_released": False,
        "provider_count_ready": len(ready_ids),
        "provider_count_known": len(snapshot),
        "credential_values_exposed": False,
    }


__all__ = [
    "PROVIDERS",
    "ProviderHealthEvidence",
    "ProviderReadiness",
    "ReputationProviderSpec",
    "cloud_reputation_readiness",
    "provider_readiness",
    "provider_registry_snapshot",
    "provider_spec",
]
