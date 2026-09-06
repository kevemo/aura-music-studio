from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path

import pytest

from aura_music_studio.archive_admission import (
    UNTRUSTED_PUBLICATION_ZIP_POLICY,
    inspect_zip_archive,
    quarantine_zip_archive,
    require_safe_zip,
    structural_zip_policy,
)
from aura_music_studio.node_transfer import extract_project_bundle


def _codes(report) -> set[str]:
    return {finding.code for finding in report.findings}


def test_publication_policy_rejects_secret_git_script_and_protected_identity_assets(tmp_path: Path):
    archive = tmp_path / "creator-package.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr(".env.production", "DO_NOT_LOG_THIS_SECRET")
        zipped.writestr(".git/config", "[remote]")
        zipped.writestr("tools/install.py", "print('no')")
        zipped.writestr("Rhiannon_Legacy_Aura_Voice_Preview_REFERENCE.mp3", b"reference")
    report = inspect_zip_archive(archive, policy=UNTRUSTED_PUBLICATION_ZIP_POLICY)
    assert report.allowed is False
    assert {"secret_or_git_material", "executable_or_script", "protected_platform_asset"} <= _codes(report)
    assert "DO_NOT_LOG_THIS_SECRET" not in json.dumps(report.as_dict())


def test_publication_policy_rejects_raw_legacy_archive_name_but_not_sanitized_reference_name(tmp_path: Path):
    raw = tmp_path / "AuraCoreAI_Deployment (2)-1.zip"
    sanitized = tmp_path / "Legacy_AuraCoreAI_Deployment_SANITIZED_RHIANNON_REFERENCE_2026-09-06.zip"
    for archive in (raw, sanitized):
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("README.txt", "reference")
    raw_report = inspect_zip_archive(raw, policy=UNTRUSTED_PUBLICATION_ZIP_POLICY)
    sanitized_report = inspect_zip_archive(sanitized, policy=UNTRUSTED_PUBLICATION_ZIP_POLICY)
    assert "restricted_legacy_source_archive" in _codes(raw_report)
    assert sanitized_report.allowed is True


def test_structural_policy_rejects_symlink_and_portable_duplicate_member(tmp_path: Path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("project/File.txt", "one")
        zipped.writestr("project/file.txt", "two")
        link = zipfile.ZipInfo("project/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zipped.writestr(link, "target.txt")
    report = inspect_zip_archive(archive)
    assert {"duplicate_member", "forbidden_symlink"} <= _codes(report)
    with pytest.raises(ValueError, match="Archive admission rejected"):
        require_safe_zip(archive)


def test_structural_policy_rejects_traversal_and_suspicious_expansion_ratio(tmp_path: Path):
    archive = tmp_path / "bombish.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("../escape.txt", "bad")
        zipped.writestr("payload/zeros.bin", b"\0" * (2 * 1024 * 1024))
    policy = structural_zip_policy(max_compression_ratio=5.0)
    report = inspect_zip_archive(archive, policy=policy)
    assert {"unsafe_archive_path", "suspicious_compression_ratio"} <= _codes(report)


def test_quarantine_is_hash_addressed_and_never_extracts_archive(tmp_path: Path):
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("safe/readme.txt", "hello")
    quarantine = tmp_path / "quarantine"
    result = quarantine_zip_archive(archive, quarantine)
    assert result["allowed"] is True
    assert result["extracted"] is False
    quarantined = Path(result["quarantined_path"])
    assert quarantined.is_file()
    assert quarantined.name == f"{result['sha256']}.zip"
    assert (quarantine / f"{result['sha256']}.json").is_file()
    assert not (quarantine / "safe" / "readme.txt").exists()


def test_compute_node_intake_uses_structural_gate_before_extraction(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LSS_NODE_MAX_BUNDLE_BYTES", str(64 * 1024 * 1024))
    archive = tmp_path / "node.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(
            "node_job.json",
            json.dumps(
                {
                    "format": "esp-node-job-v1",
                    "job": {"id": "j", "job_type": "produce", "project_name": "p", "payload": {}},
                    "files": [],
                    "uncompressed_bytes": 0,
                }
            ),
        )
        link = zipfile.ZipInfo("project/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zipped.writestr(link, "target")
    destination = tmp_path / "out"
    with pytest.raises(ValueError, match="forbidden_symlink"):
        extract_project_bundle(archive, destination)
    assert not destination.exists()
