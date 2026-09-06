from __future__ import annotations

import copy
import json
import zipfile
from pathlib import Path

import aura_music_studio.legacy_migration_admission as admission


def _register() -> dict:
    path = Path(__file__).resolve().parents[1] / "docs" / "LEGACY_MIGRATION_PROVENANCE_REGISTER.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_repository_legacy_migration_register_is_structurally_valid():
    assert admission.validate_register(_register()) == []


def test_raw_complete_archive_cannot_be_marked_usable():
    data = _register()
    raw = next(row for row in data["resources"] if row["legacy_resource_id"] == "legacy-complete-raw-20260906")
    raw["sanitised_state"] = "reference_scanned"
    raw["migration_decision"] = "Port"
    errors = admission.validate_register(data)
    assert any("raw credential-bearing archive" in error for error in errors)


def test_aurasec_archive_must_keep_zero_executable_security_credit():
    data = _register()
    sec = next(row for row in data["resources"] if row["legacy_resource_id"] == "legacy-aurasec-20260906")
    sec["executable_security_credit"] = 1
    errors = admission.validate_register(data)
    assert any("AuraSec executable_security_credit must equal 0" in error for error in errors)


def test_unknown_historical_credential_state_cannot_be_closed():
    data = _register()
    data["credential_remediation"][0]["incident_closed"] = True
    errors = admission.validate_register(data)
    assert any("incident cannot close" in error for error in errors)


def test_register_rejects_fields_that_attempt_to_store_secret_values():
    data = _register()
    data["credential_remediation"][0]["secret_value"] = "synthetic-value-never-allowed"
    errors = admission.validate_register(data)
    assert any("secret values must never be stored" in error for error in errors)


def test_raw_credential_bearing_zip_is_rejected_even_when_tracked(monkeypatch, tmp_path):
    archive = tmp_path / "AuraCoreAI_Complete_Working_Build-1.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("README.md", "legacy")
    monkeypatch.setattr(admission, "_tracked_files", lambda root: [archive])
    errors = admission.validate_tracked_legacy_archives(tmp_path, _register())
    assert any("raw credential-bearing legacy archive is prohibited" in error for error in errors)


def test_sanitised_archive_requires_verified_checksum_before_tracking(monkeypatch, tmp_path):
    archive = tmp_path / "Legacy_AuraCoreAI_Complete_SANITIZED_RHIANNON_REFERENCE_2026-09-06.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("README.md", "sanitised reference")
    monkeypatch.setattr(admission, "_tracked_files", lambda root: [archive])
    errors = admission.validate_tracked_legacy_archives(tmp_path, _register())
    assert any("no verified SHA-256 admission record" in error for error in errors)


def test_sanitised_archive_rejects_env_member_even_with_matching_digest(monkeypatch, tmp_path):
    archive = tmp_path / "Legacy_AuraCoreAI_Complete_SANITIZED_RHIANNON_REFERENCE_2026-09-06.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("src/server.js", "console.log('safe')")
        handle.writestr(".env", "SYNTHETIC=not-a-real-secret")

    data = copy.deepcopy(_register())
    row = next(item for item in data["resources"] if item["legacy_resource_id"] == "legacy-complete-sanitised-20260906")
    row["checksum_sha256"] = admission.sha256_file(archive)
    row["checksum_status"] = "verified_test_fixture"
    monkeypatch.setattr(admission, "_tracked_files", lambda root: [archive])

    errors = admission.validate_tracked_legacy_archives(tmp_path, data)
    assert any("prohibited member" in error for error in errors)


def test_reference_resource_with_missing_checksum_cannot_be_promoted_to_rewrite():
    data = _register()
    row = next(item for item in data["resources"] if item["legacy_resource_id"] == "legacy-deployment-sanitised-20260906")
    row["migration_decision"] = "Rewrite"
    errors = admission.validate_register(data)
    assert any("executable migration decision requires a verified checksum" in error for error in errors)
