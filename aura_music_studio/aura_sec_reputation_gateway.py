from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal, Mapping

from .aura_sec_browser_guard import ReputationVerificationContext, VerifiedReputationEvidence
from .aura_sec_reputation_fusion import (
    ProviderVerifier,
    ReputationProviderPolicy,
    build_reputation_fusion_verifier,
)
from .aura_sec_reputation_providers import (
    PROVIDERS,
    ProviderHealthEvidence,
    ProviderReadiness,
    provider_readiness,
)


IndicatorType = Literal["host", "url"]
ProviderLookup = Callable[["ReputationLookupRequest"], dict | None]
_MAX_RUNTIME_PROVIDERS = 8
_ALLOWED_SOURCES = {"navigation", "link", "qr", "email", "download_redirect"}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Aura Sec reputation gateway timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ReputationLookupRequest:
    """Minimal indicator supplied to one provider adapter.

    Provider credentials are intentionally absent. Future adapters close over their own secret
    material and receive only the indicator required for the lookup.
    """

    provider_id: str
    indicator_type: IndicatorType
    indicator: str
    source: str

    def __post_init__(self) -> None:
        if not self.provider_id or len(self.provider_id) > 160:
            raise ValueError("Aura Sec reputation provider identity is invalid")
        if self.indicator_type not in {"host", "url"}:
            raise ValueError("Aura Sec reputation indicator type is invalid")
        if not self.indicator or len(self.indicator) > 8192:
            raise ValueError("Aura Sec reputation indicator is invalid")
        if self.source not in _ALLOWED_SOURCES:
            raise ValueError("Aura Sec Browser Guard source is invalid")


@dataclass(frozen=True)
class ReputationRuntimeAdapter:
    """Server-owned runtime wiring for one licensed reputation provider."""

    provider_id: str
    indicator_type: IndicatorType
    lookup: ProviderLookup
    verifier: ProviderVerifier
    policy: ReputationProviderPolicy

    def __post_init__(self) -> None:
        if self.provider_id not in PROVIDERS:
            raise ValueError("Aura Sec reputation runtime references an unknown provider")
        if self.policy.provider_id != self.provider_id:
            raise ValueError("Aura Sec reputation runtime policy must match provider_id")
        if self.indicator_type not in {"host", "url"}:
            raise ValueError("Aura Sec reputation runtime indicator type is invalid")
        if not callable(self.lookup) or not callable(self.verifier):
            raise ValueError("Aura Sec reputation runtime requires lookup and verifier callables")


@dataclass(frozen=True)
class ProviderExecutionState:
    provider_id: str
    readiness_state: str
    queried: bool
    returned_data: bool
    lookup_failed: bool

    def public_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "readiness_state": self.readiness_state,
            "queried": self.queried,
            "returned_data": self.returned_data,
            "lookup_failed": self.lookup_failed,
        }


@dataclass(frozen=True)
class ReputationGatewayResult:
    evidence: VerifiedReputationEvidence | None
    provider_states: tuple[ProviderExecutionState, ...]
    fusion_failed: bool = False

    @property
    def queried_provider_ids(self) -> tuple[str, ...]:
        return tuple(item.provider_id for item in self.provider_states if item.queried)

    @property
    def ready_provider_ids(self) -> tuple[str, ...]:
        return tuple(
            item.provider_id
            for item in self.provider_states
            if item.readiness_state == "ready"
        )

    @property
    def independent_corroboration_attempted(self) -> bool:
        return len(self.queried_provider_ids) >= 2

    def public_summary(self) -> dict:
        """Safe diagnostics with no raw provider responses, secrets or verifier internals."""

        return {
            "evidence_available": self.evidence is not None,
            "fusion_failed": self.fusion_failed,
            "ready_provider_count": len(self.ready_provider_ids),
            "queried_provider_count": len(self.queried_provider_ids),
            "independent_corroboration_attempted": self.independent_corroboration_attempted,
            "provider_states": [item.public_dict() for item in self.provider_states],
            "raw_provider_responses_exposed": False,
            "credential_values_exposed": False,
        }


def _request_for(
    runtime: ReputationRuntimeAdapter,
    context: ReputationVerificationContext,
) -> ReputationLookupRequest:
    indicator = context.host if runtime.indicator_type == "host" else context.normalized_url
    return ReputationLookupRequest(
        provider_id=runtime.provider_id,
        indicator_type=runtime.indicator_type,
        indicator=indicator,
        source=context.source,
    )


def _readiness_for(
    provider_id: str,
    *,
    env: Mapping[str, str] | None,
    health: Mapping[str, ProviderHealthEvidence] | None,
    now: datetime,
) -> ProviderReadiness:
    return provider_readiness(provider_id, env=env, health=health, now=now)


def run_reputation_gateway(
    *,
    context: ReputationVerificationContext,
    runtimes: Mapping[str, ReputationRuntimeAdapter],
    env: Mapping[str, str] | None = None,
    health: Mapping[str, ProviderHealthEvidence] | None = None,
    now: datetime | None = None,
) -> ReputationGatewayResult:
    """Query only ready server-owned providers and fuse their verified evidence.

    The browser/member cannot choose providers, trust weights, block authority, credentials or
    health state. A provider that is disabled, commercially unapproved, missing credentials or
    lacking fresh health proof is never called. Lookup failures are isolated and never become
    threat evidence.
    """

    if not isinstance(context, ReputationVerificationContext):
        raise TypeError("Aura Sec reputation gateway requires a Browser Guard verification context")
    if len(runtimes) > _MAX_RUNTIME_PROVIDERS:
        raise ValueError("Aura Sec reputation runtime exceeds the provider limit")

    runtime_map = dict(runtimes)
    for provider_id, runtime in runtime_map.items():
        if provider_id != runtime.provider_id:
            raise ValueError("Aura Sec reputation runtime key must match provider_id")

    current = _utc(now or datetime.now(timezone.utc))
    provider_states: list[ProviderExecutionState] = []
    provider_results: list[dict] = []
    policies: dict[str, ReputationProviderPolicy] = {}
    verifiers: dict[str, ProviderVerifier] = {}

    # Stable ordering makes audit output deterministic and prevents caller-controlled priority.
    for provider_id in sorted(runtime_map):
        runtime = runtime_map[provider_id]
        readiness = _readiness_for(provider_id, env=env, health=health, now=current)
        if not readiness.ready:
            provider_states.append(
                ProviderExecutionState(
                    provider_id=provider_id,
                    readiness_state=readiness.state,
                    queried=False,
                    returned_data=False,
                    lookup_failed=False,
                )
            )
            continue

        request = _request_for(runtime, context)
        lookup_failed = False
        response: dict | None = None
        try:
            candidate = runtime.lookup(request)
            if candidate is not None and not isinstance(candidate, dict):
                raise TypeError("Aura Sec provider adapter must return an object or no result")
            response = dict(candidate) if candidate is not None else None
        except Exception:
            lookup_failed = True

        provider_states.append(
            ProviderExecutionState(
                provider_id=provider_id,
                readiness_state=readiness.state,
                queried=True,
                returned_data=response is not None,
                lookup_failed=lookup_failed,
            )
        )
        if lookup_failed or response is None:
            continue

        policies[provider_id] = runtime.policy
        verifiers[provider_id] = runtime.verifier
        provider_results.append({"provider_id": provider_id, "response": response})

    if not provider_results:
        return ReputationGatewayResult(
            evidence=None,
            provider_states=tuple(provider_states),
            fusion_failed=False,
        )

    try:
        verifier = build_reputation_fusion_verifier(
            provider_policies=policies,
            provider_verifiers=verifiers,
            clock=lambda: current,
        )
        evidence = verifier({"provider_results": provider_results}, context)
    except Exception:
        # Gateway-controlled payloads should not fail structurally, but a verifier/fusion defect
        # must still fail closed rather than produce authoritative reputation.
        return ReputationGatewayResult(
            evidence=None,
            provider_states=tuple(provider_states),
            fusion_failed=True,
        )

    return ReputationGatewayResult(
        evidence=evidence,
        provider_states=tuple(provider_states),
        fusion_failed=False,
    )


__all__ = [
    "ProviderExecutionState",
    "ProviderLookup",
    "ReputationGatewayResult",
    "ReputationLookupRequest",
    "ReputationRuntimeAdapter",
    "run_reputation_gateway",
]
