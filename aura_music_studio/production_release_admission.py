from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Iterable, Mapping
from urllib.parse import urlparse

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_GATE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,79}$")

REQUIRED_PRODUCTION_RELEASE_GATES = frozenset(
    {
        "domain_tls",
        "production_secrets",
        "monitoring_alerting",
        "backup_restore_drill",
        "deployment_rollback",
        "capacity_failure_testing",
        "privacy_security_review",
        "incident_support_readiness",
        "provider_payment_e2e",
        "production_data_ai_infrastructure",
    }
)


@dataclass(frozen=True)
class ProductionReleaseEvidence:
    gate: str
    release_sha: str
    environment: str
    outcome: str
    evidence_digest: str
    evidence_ref: str
    verifier: str
    observed_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class ProductionReleaseAdmission:
    release_sha: str
    admitted: bool
    evaluated_at: datetime
    accepted_gates: tuple[str, ...]
    missing_gates: tuple[str, ...]
    rejected_gates: Mapping[str, tuple[str, ...]]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("release evidence timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _clean_token(value: str, *, field: str, minimum: int = 1, maximum: int = 200) -> str:
    clean = " ".join(str(value or "").split())
    if not minimum <= len(clean) <= maximum:
        raise ValueError(f"{field} must be {minimum}-{maximum} characters")
    return clean


def _validate_ref(value: str) -> str:
    clean = _clean_token(value, field="evidence_ref", minimum=8, maximum=1000)
    parsed = urlparse(clean)
    if parsed.scheme == "https" and parsed.netloc:
        return clean
    if clean.startswith("urn:evidence:") and len(clean) > len("urn:evidence:"):
        return clean
    raise ValueError("release evidence reference must be HTTPS or urn:evidence")


def normalize_release_evidence(item: ProductionReleaseEvidence) -> ProductionReleaseEvidence:
    gate = _clean_token(item.gate, field="gate", minimum=3, maximum=80).lower()
    if not _GATE_RE.fullmatch(gate):
        raise ValueError("invalid production release gate identifier")

    release_sha = _clean_token(item.release_sha, field="release_sha", minimum=40, maximum=40).lower()
    if not _COMMIT_RE.fullmatch(release_sha):
        raise ValueError("release_sha must be a full lowercase Git commit SHA")

    environment = _clean_token(item.environment, field="environment", minimum=2, maximum=40).lower()
    outcome = _clean_token(item.outcome, field="outcome", minimum=2, maximum=40).lower()
    evidence_digest = str(item.evidence_digest or "").strip().lower()
    if not _SHA256_RE.fullmatch(evidence_digest):
        raise ValueError("evidence_digest must be a lowercase SHA-256 digest")

    verifier = _clean_token(item.verifier, field="verifier", minimum=3, maximum=200)
    observed_at = _utc(item.observed_at)
    expires_at = _utc(item.expires_at)
    if expires_at <= observed_at:
        raise ValueError("release evidence expiry must be after observation time")

    return ProductionReleaseEvidence(
        gate=gate,
        release_sha=release_sha,
        environment=environment,
        outcome=outcome,
        evidence_digest=evidence_digest,
        evidence_ref=_validate_ref(item.evidence_ref),
        verifier=verifier,
        observed_at=observed_at,
        expires_at=expires_at,
    )


def assess_production_release(
    release_sha: str,
    evidence: Iterable[ProductionReleaseEvidence],
    *,
    evaluated_at: datetime | None = None,
    required_gates: Iterable[str] = REQUIRED_PRODUCTION_RELEASE_GATES,
) -> ProductionReleaseAdmission:
    """Fail closed unless every required production gate has fresh exact-SHA evidence.

    This is an admission contract, not proof that any production check happened. Evidence
    must be supplied by deployment/operations systems after the real check. Repository CI
    may exercise this contract, but CI alone cannot manufacture production readiness.
    """

    release_sha = _clean_token(release_sha, field="release_sha", minimum=40, maximum=40).lower()
    if not _COMMIT_RE.fullmatch(release_sha):
        raise ValueError("release_sha must be a full lowercase Git commit SHA")
    now = _utc(evaluated_at or datetime.now(timezone.utc))

    required: set[str] = set()
    for raw_gate in required_gates:
        gate = _clean_token(raw_gate, field="required gate", minimum=3, maximum=80).lower()
        if not _GATE_RE.fullmatch(gate):
            raise ValueError("invalid required production release gate identifier")
        required.add(gate)
    if not required:
        raise ValueError("at least one production release gate is required")

    grouped: dict[str, list[ProductionReleaseEvidence]] = {}
    for raw in evidence:
        item = normalize_release_evidence(raw)
        grouped.setdefault(item.gate, []).append(item)

    accepted: list[str] = []
    rejected: dict[str, tuple[str, ...]] = {}
    for gate in sorted(required):
        rows = grouped.get(gate, [])
        reasons: set[str] = set()
        if not rows:
            continue
        if len(rows) != 1:
            reasons.add("duplicate_evidence")
        for row in rows:
            if row.release_sha != release_sha:
                reasons.add("release_sha_mismatch")
            if row.environment != "production":
                reasons.add("non_production_environment")
            if row.outcome != "passed":
                reasons.add("outcome_not_passed")
            if row.observed_at > now:
                reasons.add("observation_in_future")
            if row.expires_at <= now:
                reasons.add("evidence_expired")
        if reasons:
            rejected[gate] = tuple(sorted(reasons))
        else:
            accepted.append(gate)

    missing = tuple(sorted(required.difference(grouped)))
    admitted = not missing and not rejected and set(accepted) == required
    return ProductionReleaseAdmission(
        release_sha=release_sha,
        admitted=admitted,
        evaluated_at=now,
        accepted_gates=tuple(sorted(accepted)),
        missing_gates=missing,
        rejected_gates=MappingProxyType(dict(sorted(rejected.items()))),
    )
