from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


REQUIRED_RELEASE_GATES = (
    "native_clients_built",
    "platform_signing_ready",
    "notarisation_ready",
    "device_attestation_verified",
    "secure_updater_verified",
    "signed_release_manifest_verified",
    "sbom_and_provenance_published",
    "threat_feeds_configured",
    "security_skus_configured",
    "verified_checkout_connected",
    "passkey_recovery_policy_ready",
    "penetration_test_passed",
    "independent_malware_benchmark_passed",
    "independent_phishing_benchmark_passed",
    "independent_ransomware_benchmark_passed",
    "privacy_legal_review_complete",
    "incident_response_ready",
    "support_runbook_ready",
    "backup_restore_drill_passed",
    "commercial_name_clearance_complete",
)


@dataclass(frozen=True)
class ReleaseReadinessResult:
    releasable: bool
    completed: tuple[str, ...]
    missing: tuple[str, ...]

    @property
    def percentage(self) -> int:
        if not REQUIRED_RELEASE_GATES:
            return 100
        return round((len(self.completed) / len(REQUIRED_RELEASE_GATES)) * 100)


def evaluate_release_readiness(evidence: Mapping[str, bool]) -> ReleaseReadinessResult:
    """Fail-closed production release gate for Aura Sec.

    Evidence is intentionally boolean and externally supplied by governed release tooling.
    This function never fabricates completion from configuration presence alone. Unknown
    fields are ignored and every required gate must be explicitly true before release.
    """
    completed = tuple(gate for gate in REQUIRED_RELEASE_GATES if evidence.get(gate) is True)
    missing = tuple(gate for gate in REQUIRED_RELEASE_GATES if evidence.get(gate) is not True)
    return ReleaseReadinessResult(
        releasable=not missing,
        completed=completed,
        missing=missing,
    )


def assert_release_ready(evidence: Mapping[str, bool]) -> ReleaseReadinessResult:
    result = evaluate_release_readiness(evidence)
    if not result.releasable:
        raise RuntimeError(
            "Aura Sec production release is blocked; missing gates: " + ", ".join(result.missing)
        )
    return result


__all__ = [
    "REQUIRED_RELEASE_GATES",
    "ReleaseReadinessResult",
    "evaluate_release_readiness",
    "assert_release_ready",
]
