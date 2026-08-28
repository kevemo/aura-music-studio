from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal, Mapping

from .aura_sec_browser_guard import ReputationVerificationContext, VerifiedReputationEvidence
from .aura_sec_threat_intel import DEFAULT_MAX_AGE, IntelSource


Verdict = Literal["benign", "suspicious", "scam", "phishing", "malicious"]
FusionState = Literal[
    "benign_consensus",
    "caution",
    "corroborated_threat",
    "authoritative_threat",
    "provider_conflict",
]
ProviderVerifier = Callable[[dict, ReputationVerificationContext], VerifiedReputationEvidence | None]
Clock = Callable[[], datetime]

_ALLOWED_VERDICTS = {"benign", "suspicious", "scam", "phishing", "malicious"}
_HARMFUL_VERDICTS = {"scam", "phishing", "malicious"}
_MAX_PROVIDER_RESULTS = 8
_MAX_FUTURE_SKEW = timedelta(minutes=5)
_URL_MAX_AGE = DEFAULT_MAX_AGE[IntelSource.URL_REPUTATION]
_FUSION_PROVIDER_ID = "aura-sec-reputation-fusion-v1"
_SEVERITY = {"benign": 0, "suspicious": 1, "scam": 2, "phishing": 3, "malicious": 4}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise PermissionError("Aura Sec reputation timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ReputationProviderPolicy:
    """Server-owned trust policy for one reputation adapter.

    Trust attributes never come from browser/provider payloads. A provider may be allowed to
    contribute evidence without being allowed to hard-block by itself.
    """

    provider_id: str
    weight: float = 1.0
    minimum_confidence: float = 0.40
    can_single_source_block: bool = False
    single_source_block_confidence: float = 0.98
    enabled: bool = True

    def __post_init__(self) -> None:
        provider_id = self.provider_id.strip()
        if not provider_id or provider_id != self.provider_id or len(provider_id) > 160:
            raise ValueError("Aura Sec reputation provider_id is invalid")
        if not 0.1 <= float(self.weight) <= 5.0:
            raise ValueError("Aura Sec reputation provider weight must be between 0.1 and 5.0")
        if not 0.0 <= float(self.minimum_confidence) <= 1.0:
            raise ValueError("Aura Sec reputation minimum confidence must be between zero and one")
        if not 0.70 <= float(self.single_source_block_confidence) <= 1.0:
            raise ValueError("Single-source block confidence must be at least 0.70")


@dataclass(frozen=True)
class ReputationFusionResult:
    evidence: VerifiedReputationEvidence
    state: FusionState
    provider_count: int
    supporting_providers: tuple[str, ...]
    conflicting_providers: tuple[str, ...]


@dataclass(frozen=True)
class _AcceptedEvidence:
    policy: ReputationProviderPolicy
    evidence: VerifiedReputationEvidence


def _validate_provider_evidence(
    *,
    evidence: VerifiedReputationEvidence,
    policy: ReputationProviderPolicy,
    context: ReputationVerificationContext,
    now: datetime,
) -> VerifiedReputationEvidence:
    if evidence.provider_id != policy.provider_id:
        raise PermissionError("Reputation adapter returned evidence for the wrong provider")
    if evidence.indicator_type not in {"url", "host"}:
        raise PermissionError("Unsupported reputation indicator type")
    if evidence.verdict not in _ALLOWED_VERDICTS:
        raise PermissionError("Unsupported reputation verdict")

    confidence = float(evidence.confidence)
    if not 0.0 <= confidence <= 1.0:
        raise PermissionError("Reputation confidence must be between zero and one")
    if confidence < policy.minimum_confidence:
        raise PermissionError("Reputation evidence is below the configured provider confidence floor")

    observed = _utc(evidence.observed_at)
    expires = _utc(evidence.expires_at)
    current = _utc(now)
    if observed > current + _MAX_FUTURE_SKEW:
        raise PermissionError("Reputation evidence is issued too far in the future")
    if expires <= observed or expires - observed > _URL_MAX_AGE:
        raise PermissionError("Reputation evidence lifetime is invalid")
    if current >= expires:
        raise PermissionError("Reputation evidence has expired")

    expected_indicator = context.host if evidence.indicator_type == "host" else context.normalized_url
    if evidence.indicator != expected_indicator:
        raise PermissionError("Reputation evidence does not match the inspected destination")

    expected_digest = _digest(
        context.evidence_payload(
            indicator_type=evidence.indicator_type,
            indicator=evidence.indicator,
            verdict=evidence.verdict,
            confidence=confidence,
            provider_id=policy.provider_id,
            observed_at=observed,
            expires_at=expires,
        )
    )
    supplied_digest = (evidence.evidence_digest or "").lower()
    if supplied_digest != expected_digest:
        raise PermissionError("Reputation evidence digest does not match the verified provider result")
    return evidence


def _weighted_mean(items: list[_AcceptedEvidence]) -> float:
    total_weight = sum(item.policy.weight for item in items)
    if total_weight <= 0:
        return 0.0
    return sum(item.policy.weight * float(item.evidence.confidence) for item in items) / total_weight


def _support(items: list[_AcceptedEvidence]) -> float:
    return sum(item.policy.weight * float(item.evidence.confidence) for item in items)


def _highest_harmful_verdict(items: list[_AcceptedEvidence]) -> Verdict:
    return max(
        (item.evidence.verdict for item in items),
        key=lambda verdict: _SEVERITY[verdict],
    )  # type: ignore[return-value]


def fuse_verified_reputation(
    accepted: list[tuple[ReputationProviderPolicy, VerifiedReputationEvidence]],
    *,
    context: ReputationVerificationContext,
    now: datetime,
) -> ReputationFusionResult | None:
    """Fuse already-verified independent provider evidence into one Browser Guard proof.

    Harmful single-source evidence warns by default. It may block only when its server-owned
    policy explicitly grants single-source authority at a very high confidence threshold.
    Multi-provider harmful evidence can block when corroborated and not materially contradicted.
    """

    items = [_AcceptedEvidence(policy, evidence) for policy, evidence in accepted]
    if not items:
        return None

    harmful = [item for item in items if item.evidence.verdict in _HARMFUL_VERDICTS]
    suspicious = [item for item in items if item.evidence.verdict == "suspicious"]
    benign = [item for item in items if item.evidence.verdict == "benign"]
    strong_benign = [item for item in benign if float(item.evidence.confidence) >= 0.80]

    harmful_support = _support(harmful)
    benign_support = _support(benign)
    harmful_ids = tuple(sorted(item.policy.provider_id for item in harmful))
    benign_ids = tuple(sorted(item.policy.provider_id for item in benign))

    state: FusionState
    verdict: Verdict
    confidence: float
    supporters: tuple[str, ...]
    conflicts: tuple[str, ...] = ()

    authoritative = [
        item
        for item in harmful
        if item.policy.can_single_source_block
        and float(item.evidence.confidence) >= item.policy.single_source_block_confidence
    ]

    if harmful and strong_benign:
        # A high-confidence disagreement is never collapsed into a hard block. Surface caution
        # and preserve both provider identities for operational review.
        state = "provider_conflict"
        verdict = "suspicious"
        confidence = max(
            0.40,
            min(0.69, (_weighted_mean(harmful) + _weighted_mean(strong_benign)) / 2),
        )
        supporters = harmful_ids
        conflicts = benign_ids
    elif authoritative:
        winner = max(
            authoritative,
            key=lambda item: float(item.evidence.confidence) * item.policy.weight,
        )
        state = "authoritative_threat"
        verdict = winner.evidence.verdict  # type: ignore[assignment]
        confidence = max(0.70, min(0.99, float(winner.evidence.confidence)))
        supporters = (winner.policy.provider_id,)
    elif (
        len(harmful) >= 2
        and harmful_support >= 1.35
        and harmful_support >= max(0.01, benign_support * 1.5)
    ):
        state = "corroborated_threat"
        verdict = _highest_harmful_verdict(harmful)
        confidence = max(0.70, min(0.99, _weighted_mean(harmful)))
        supporters = harmful_ids
        conflicts = benign_ids
    elif harmful or suspicious:
        caution_items = harmful + suspicious
        state = "caution"
        verdict = "suspicious"
        confidence = max(0.40, min(0.69, _weighted_mean(caution_items) * 0.70))
        supporters = tuple(sorted(item.policy.provider_id for item in caution_items))
        conflicts = benign_ids
    else:
        state = "benign_consensus"
        verdict = "benign"
        confidence = max(0.0, min(0.99, _weighted_mean(benign)))
        supporters = benign_ids

    observed = max(_utc(item.evidence.observed_at) for item in items)
    expires = min(_utc(item.evidence.expires_at) for item in items)
    final_digest = _digest(
        context.evidence_payload(
            indicator_type="host",
            indicator=context.host,
            verdict=verdict,
            confidence=confidence,
            provider_id=_FUSION_PROVIDER_ID,
            observed_at=observed,
            expires_at=expires,
        )
    )
    fused = VerifiedReputationEvidence(
        indicator_type="host",
        indicator=context.host,
        verdict=verdict,
        confidence=confidence,
        provider_id=_FUSION_PROVIDER_ID,
        observed_at=observed,
        expires_at=expires,
        evidence_digest=final_digest,
    )
    return ReputationFusionResult(
        evidence=fused,
        state=state,
        provider_count=len(items),
        supporting_providers=supporters,
        conflicting_providers=conflicts,
    )


def build_reputation_fusion_verifier(
    *,
    provider_policies: Mapping[str, ReputationProviderPolicy],
    provider_verifiers: Mapping[str, ProviderVerifier],
    clock: Clock | None = None,
) -> ProviderVerifier:
    """Build the Browser Guard verifier boundary for configured reputation adapters.

    Payloads may select only a configured provider and carry its opaque adapter response. They
    cannot supply trust weights, block authority, confidence floors or a final verdict directly.
    """

    policies = dict(provider_policies)
    verifiers = dict(provider_verifiers)
    for provider_id, policy in policies.items():
        if provider_id != policy.provider_id:
            raise ValueError("Reputation provider policy key must match provider_id")
        if provider_id not in verifiers:
            raise ValueError(f"Reputation verifier is missing for configured provider: {provider_id}")
    if set(verifiers) - set(policies):
        raise ValueError("Reputation verifier exists without a server-owned provider policy")

    now_fn = clock or (lambda: datetime.now(timezone.utc))

    def verify(payload: dict, context: ReputationVerificationContext) -> VerifiedReputationEvidence | None:
        results = payload.get("provider_results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results:
            return None
        if len(results) > _MAX_PROVIDER_RESULTS:
            raise PermissionError("Aura Sec reputation payload exceeds the provider evidence limit")

        seen: set[str] = set()
        accepted: list[tuple[ReputationProviderPolicy, VerifiedReputationEvidence]] = []
        current = _utc(now_fn())

        for item in results:
            if not isinstance(item, dict) or set(item) != {"provider_id", "response"}:
                raise PermissionError("Aura Sec reputation provider result has an invalid shape")
            provider_id = item.get("provider_id")
            response = item.get("response")
            if not isinstance(provider_id, str) or provider_id not in policies:
                raise PermissionError("Aura Sec reputation payload references an unconfigured provider")
            if provider_id in seen:
                raise PermissionError("Aura Sec reputation payload contains duplicate provider evidence")
            seen.add(provider_id)
            policy = policies[provider_id]
            if not policy.enabled:
                continue
            if not isinstance(response, dict):
                raise PermissionError("Aura Sec reputation provider response must be an object")

            try:
                evidence = verifiers[provider_id](dict(response), context)
                if evidence is None:
                    continue
                validated = _validate_provider_evidence(
                    evidence=evidence,
                    policy=policy,
                    context=context,
                    now=current,
                )
            except Exception:
                # Verification failure in one adapter is isolated. It never becomes evidence and
                # cannot make another independent provider less trustworthy.
                continue
            accepted.append((policy, validated))

        fused = fuse_verified_reputation(accepted, context=context, now=current)
        return fused.evidence if fused else None

    return verify


__all__ = [
    "FusionState",
    "ProviderVerifier",
    "ReputationFusionResult",
    "ReputationProviderPolicy",
    "build_reputation_fusion_verifier",
    "fuse_verified_reputation",
]
