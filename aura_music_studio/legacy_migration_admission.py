from __future__ import annotations

import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any

REGISTER_PATH = Path("docs/LEGACY_MIGRATION_PROVENANCE_REGISTER.json")
_ALLOWED_DECISIONS = {"Port", "Rewrite", "Reference Only", "Reject", "Already Exists", "Superseded"}
_ALLOWED_SANITISATION = {"sanitised", "raw_quarantined", "reference_scanned", "not_applicable"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RAW_BLOCKED_ARCHIVES = {
    "auracoreai_complete_working_build-1.zip",
    "auracoreai_deployment (2)-1.zip",
}
_FORBIDDEN_ARCHIVE_PARTS = {".git", "node_modules"}
_FORBIDDEN_ARCHIVE_NAMES = {".env", ".env.local", ".env.production", ".env.staging"}
_SECRET_FIELD_FRAGMENTS = {"secret_value", "credential_value", "token_value", "password_value", "private_key"}


def _tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [root / value.decode("utf-8") for value in result.stdout.split(b"\0") if value]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_string(record: dict[str, Any], key: str, errors: list[str], prefix: str) -> None:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{prefix}.{key}: required non-empty string")


def validate_register(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") != 1:
        errors.append("schema_version: must equal 1")

    resources = data.get("resources")
    if not isinstance(resources, list) or not resources:
        errors.append("resources: non-empty list required")
        resources = []

    seen_ids: set[str] = set()
    for index, record in enumerate(resources):
        prefix = f"resources[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{prefix}: object required")
            continue
        for key in (
            "legacy_resource_id",
            "archive",
            "source_date",
            "sanitised_state",
            "target_component",
            "risk_class",
            "licence_provenance",
            "migration_decision",
            "security_review",
            "deployment_state",
            "rollback_state",
            "chat8_audit_state",
        ):
            _required_string(record, key, errors, prefix)

        resource_id = str(record.get("legacy_resource_id") or "")
        if resource_id in seen_ids:
            errors.append(f"{prefix}.legacy_resource_id: duplicate {resource_id!r}")
        seen_ids.add(resource_id)

        decision = record.get("migration_decision")
        if decision not in _ALLOWED_DECISIONS:
            errors.append(f"{prefix}.migration_decision: unsupported decision")
        sanitised_state = record.get("sanitised_state")
        if sanitised_state not in _ALLOWED_SANITISATION:
            errors.append(f"{prefix}.sanitised_state: unsupported state")

        owners = record.get("owning_chats")
        if not isinstance(owners, list) or not owners or not all(isinstance(v, int) and 1 <= v <= 7 for v in owners):
            errors.append(f"{prefix}.owning_chats: one or more Chat numbers 1-7 required")

        checksum = record.get("checksum_sha256")
        checksum_status = record.get("checksum_status")
        if checksum is not None and (not isinstance(checksum, str) or not _SHA256.fullmatch(checksum)):
            errors.append(f"{prefix}.checksum_sha256: must be null or lowercase SHA-256")
        if checksum is None and checksum_status != "pending_authoritative_manifest":
            errors.append(f"{prefix}.checksum_status: missing checksum must be explicitly pending_authoritative_manifest")
        if checksum is None and decision in {"Port", "Rewrite", "Already Exists"}:
            errors.append(f"{prefix}: executable migration decision requires a verified checksum")

        archive = str(record.get("archive") or "").lower()
        if archive in _RAW_BLOCKED_ARCHIVES:
            if sanitised_state != "raw_quarantined" or decision != "Reject":
                errors.append(f"{prefix}: raw credential-bearing archive must remain quarantined and rejected")

        if "aurasec-1.zip" in archive and int(record.get("executable_security_credit", -1)) != 0:
            errors.append(f"{prefix}: AuraSec executable_security_credit must equal 0")

        if record.get("launch_state") == "launch_ready" and (
            decision in {"Reference Only", "Reject", "Superseded"} or checksum is None
        ):
            errors.append(f"{prefix}: reference/rejected/unverified resource cannot be launch_ready")

    remediation = data.get("credential_remediation")
    if not isinstance(remediation, list) or not remediation:
        errors.append("credential_remediation: non-empty list required for the known incident")
        remediation = []
    for index, record in enumerate(remediation):
        prefix = f"credential_remediation[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{prefix}: object required")
            continue
        for key in ("credential_class", "provider_system", "current_validity", "revoked", "rotated", "replacement_stored_securely", "dependent_service_retested", "incident_closed"):
            if key not in record:
                errors.append(f"{prefix}.{key}: required")
        for key in record:
            lowered = key.lower()
            if any(fragment in lowered for fragment in _SECRET_FIELD_FRAGMENTS):
                errors.append(f"{prefix}.{key}: secret values must never be stored in the remediation register")
        if record.get("current_validity") not in {"unknown", "confirmed_invalid", "confirmed_valid"}:
            errors.append(f"{prefix}.current_validity: unsupported state")
        for key in ("revoked", "rotated", "replacement_stored_securely", "dependent_service_retested"):
            if record.get(key) not in {"unknown", "yes", "no", "not_applicable"}:
                errors.append(f"{prefix}.{key}: unsupported state")
        if record.get("incident_closed") is True:
            if record.get("current_validity") == "unknown" or record.get("revoked") == "unknown" or record.get("rotated") == "unknown":
                errors.append(f"{prefix}: incident cannot close while compromise/remediation state is unknown")

    return errors


def validate_tracked_legacy_archives(root: Path, data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    resource_by_name = {
        str(row.get("archive") or "").lower(): row
        for row in data.get("resources", [])
        if isinstance(row, dict)
    }
    for path in _tracked_files(root):
        if path.suffix.lower() != ".zip":
            continue
        name = path.name.lower()
        if name in _RAW_BLOCKED_ARCHIVES:
            errors.append(f"{path}: raw credential-bearing legacy archive is prohibited")
            continue
        if "aura" not in name and "fractalis" not in name and "rhiannon" not in name:
            continue
        record = resource_by_name.get(name)
        if record is None:
            errors.append(f"{path}: legacy-derived ZIP is not present in the migration provenance register")
            continue
        expected = record.get("checksum_sha256")
        if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
            errors.append(f"{path}: tracked legacy ZIP has no verified SHA-256 admission record")
            continue
        actual = sha256_file(path)
        if actual != expected:
            errors.append(f"{path}: SHA-256 does not match reviewed provenance record")
            continue
        if record.get("sanitised_state") == "sanitised":
            try:
                with zipfile.ZipFile(path) as archive:
                    for info in archive.infolist():
                        parts = {part.lower() for part in Path(info.filename).parts}
                        basename = Path(info.filename).name.lower()
                        if parts & _FORBIDDEN_ARCHIVE_PARTS or basename in _FORBIDDEN_ARCHIVE_NAMES:
                            errors.append(f"{path}: sanitised archive contains prohibited member {info.filename!r}")
                            break
            except (OSError, zipfile.BadZipFile):
                errors.append(f"{path}: unreadable legacy ZIP")
    return errors


def load_register(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_repository(root: Path, register_path: Path | None = None) -> list[str]:
    path = register_path or (root / REGISTER_PATH)
    try:
        data = load_register(path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{path}: cannot load migration register: {type(exc).__name__}"]
    return validate_register(data) + validate_tracked_legacy_archives(root, data)


__all__ = [
    "REGISTER_PATH",
    "load_register",
    "sha256_file",
    "validate_register",
    "validate_repository",
    "validate_tracked_legacy_archives",
]
