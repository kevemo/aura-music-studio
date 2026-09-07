from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_EXPECTED_BRANCH = "development/full-site-build"
_REQUIRED_WORKFLOWS = ("Command Center CI", "Security Gates", "Self-Host Smoke")
_REQUIRED_HEAD_GATES = (
    "source_route_integrity",
    "database_migrations",
    "security_review",
    "privacy_review",
    "restore_drill",
    "observability",
    "commercial_acceptance",
    "standalone_acceptance",
    "branding_acceptance",
    "effects_systems_acceptance",
    "self_host_acceptance",
)
_REQUIRED_DEVICE_PATHS = (
    "desktop",
    "tablet",
    "mobile",
    "keyboard_only",
    "reduced_motion",
    "screen_reader_semantics",
)


@dataclass(frozen=True)
class ReleaseAdmissionResult:
    admissible: bool
    candidate_sha: str
    integration_branch: str
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "admissible": self.admissible,
            "candidate_sha": self.candidate_sha,
            "integration_branch": self.integration_branch,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "secret_values_exposed": False,
        }


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _passed_on_sha(section: Mapping[str, Any], candidate_sha: str) -> bool:
    return section.get("status") == "passed" and section.get("head_sha") == candidate_sha


def _evidence_ref_present(section: Mapping[str, Any]) -> bool:
    ref = section.get("evidence_ref")
    return isinstance(ref, str) and bool(ref.strip())


def evaluate_final_release_admission(
    evidence: Mapping[str, Any] | None,
    *,
    candidate_sha: str,
    expected_branch: str = _EXPECTED_BRANCH,
) -> ReleaseAdmissionResult:
    """Validate a final release evidence package against one exact integration SHA.

    This validator intentionally does not create evidence. It only admits a package when all
    required gates are explicitly bound to the same candidate SHA. Provider approvals, restore
    drills, browser/device tests, independent reviews and similar external facts must be produced
    by their real systems/operators and referenced by the package.
    """

    blockers: list[str] = []
    warnings: list[str] = []
    payload = _mapping(evidence)

    normalized_sha = str(candidate_sha or "").strip().lower()
    if not _SHA_RE.fullmatch(normalized_sha):
        blockers.append("candidate_sha must be a full 40-character lowercase Git commit SHA")

    if payload.get("schema_version") != 1:
        blockers.append("release evidence must use schema_version=1")

    candidate = _mapping(payload.get("candidate"))
    evidence_sha = str(candidate.get("git_sha") or "").strip().lower()
    branch = str(candidate.get("integration_branch") or "").strip()
    if evidence_sha != normalized_sha:
        blockers.append("release evidence git_sha does not match the exact candidate SHA")
    if branch != expected_branch:
        blockers.append(f"release evidence must target {expected_branch}")

    workflows = _mapping(payload.get("workflows"))
    for name in _REQUIRED_WORKFLOWS:
        row = _mapping(workflows.get(name))
        if row.get("conclusion") != "success" or row.get("head_sha") != normalized_sha:
            blockers.append(f"{name} must be successful on the exact candidate SHA")
        if not _evidence_ref_present(row):
            blockers.append(f"{name} must include a concrete evidence_ref")

    gates = _mapping(payload.get("gates"))
    for name in _REQUIRED_HEAD_GATES:
        row = _mapping(gates.get(name))
        if not _passed_on_sha(row, normalized_sha):
            blockers.append(f"{name} must be passed on the exact candidate SHA")
        if not _evidence_ref_present(row):
            blockers.append(f"{name} must include a concrete evidence_ref")

    restore = _mapping(gates.get("restore_drill"))
    if restore.get("encrypted_backup_restored") is not True:
        blockers.append("restore_drill must prove an encrypted backup was restored")
    if restore.get("restore_integrity_verified") is not True:
        blockers.append("restore_drill must prove restored-data integrity verification")

    security = _mapping(gates.get("security_review"))
    if security.get("critical_findings_open") != 0:
        blockers.append("security_review must report zero open critical findings")
    if security.get("secret_scan_passed") is not True:
        blockers.append("security_review must include a passing committed-secret scan")

    privacy = _mapping(gates.get("privacy_review"))
    for flag in ("deletion_verified", "export_verified", "retention_verified", "breach_response_verified"):
        if privacy.get(flag) is not True:
            blockers.append(f"privacy_review must verify {flag}")

    commercial = _mapping(gates.get("commercial_acceptance"))
    for flag in (
        "server_authoritative_pricing_verified",
        "provider_payment_verified",
        "webhook_authenticity_verified",
        "entitlement_projection_verified",
        "refund_reversal_verified",
        "receipt_history_verified",
    ):
        if commercial.get(flag) is not True:
            blockers.append(f"commercial_acceptance must verify {flag}")

    effects = _mapping(gates.get("effects_systems_acceptance"))
    if effects.get("metadata_required_coverage_met") is not True:
        blockers.append("effects/system metadata required coverage is not evidenced")
    if effects.get("executable_required_coverage_met") is not True:
        blockers.append("effects/system executable required coverage is not evidenced")
    if effects.get("metadata_only_counted_as_executable") is not False:
        blockers.append("metadata-only effects must never be counted as executable")

    self_host = _mapping(gates.get("self_host_acceptance"))
    if self_host.get("claimed_capabilities_smoke_verified") is not True:
        blockers.append("self-host acceptance must smoke-test every claimed capability")
    if self_host.get("unverified_capabilities_claimed_ready") is not False:
        blockers.append("unverified self-host capabilities must not be claimed ready")

    devices = _mapping(payload.get("device_accessibility"))
    for name in _REQUIRED_DEVICE_PATHS:
        row = _mapping(devices.get(name))
        if not _passed_on_sha(row, normalized_sha):
            blockers.append(f"device/accessibility path {name} must pass on the exact candidate SHA")
        if not _evidence_ref_present(row):
            blockers.append(f"device/accessibility path {name} must include a concrete evidence_ref")

    internal = _mapping(payload.get("internal_gaps"))
    open_internal = internal.get("open_count")
    if open_internal != 0:
        blockers.append("all mandatory internal gaps must be closed before release admission")

    duplicate_authority = _mapping(payload.get("duplicate_authority"))
    if duplicate_authority.get("consequential_duplicates_open") != 0:
        blockers.append("no consequential duplicate authority may remain open")
    if duplicate_authority.get("audit_head_sha") != normalized_sha:
        blockers.append("duplicate-authority audit must be bound to the exact candidate SHA")
    if not _evidence_ref_present(duplicate_authority):
        blockers.append("duplicate-authority audit must include a concrete evidence_ref")

    external = _mapping(payload.get("external_blockers"))
    blocking_items = _sequence(external.get("blocking"))
    if blocking_items:
        blockers.append("one or more blocking external dependencies remain unresolved")
    non_blocking_items = _sequence(external.get("non_blocking"))
    if non_blocking_items:
        warnings.append(f"{len(non_blocking_items)} non-blocking external dependency item(s) remain documented")

    approval = _mapping(payload.get("release_controller"))
    if approval.get("recommendation") != "approve":
        blockers.append("release controller recommendation must explicitly be approve")
    if approval.get("candidate_sha") != normalized_sha:
        blockers.append("release controller recommendation must target the exact candidate SHA")
    if not _evidence_ref_present(approval):
        blockers.append("release controller recommendation must include a concrete evidence_ref")

    unique_blockers = tuple(dict.fromkeys(blockers))
    unique_warnings = tuple(dict.fromkeys(warnings))
    return ReleaseAdmissionResult(
        admissible=not unique_blockers,
        candidate_sha=normalized_sha,
        integration_branch=branch or expected_branch,
        blockers=unique_blockers,
        warnings=unique_warnings,
    )


__all__ = ["ReleaseAdmissionResult", "evaluate_final_release_admission"]
