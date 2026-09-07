from __future__ import annotations

from copy import deepcopy

from aura_music_studio.final_release_admission import evaluate_final_release_admission

SHA = "a" * 40


def _row(**extra):
    return {"status": "passed", "head_sha": SHA, "evidence_ref": "test://evidence", **extra}


def _valid_evidence():
    return {
        "schema_version": 1,
        "candidate": {"git_sha": SHA, "integration_branch": "development/full-site-build"},
        "workflows": {
            "Command Center CI": {"conclusion": "success", "head_sha": SHA, "evidence_ref": "test://ci"},
            "Security Gates": {"conclusion": "success", "head_sha": SHA, "evidence_ref": "test://security-gates"},
            "Self-Host Smoke": {"conclusion": "success", "head_sha": SHA, "evidence_ref": "test://self-host"},
        },
        "gates": {
            "source_route_integrity": _row(),
            "database_migrations": _row(rollback_documented=True),
            "security_review": _row(critical_findings_open=0, secret_scan_passed=True),
            "privacy_review": _row(
                deletion_verified=True,
                export_verified=True,
                retention_verified=True,
                breach_response_verified=True,
            ),
            "restore_drill": _row(encrypted_backup_restored=True, restore_integrity_verified=True),
            "observability": _row(),
            "commercial_acceptance": _row(
                server_authoritative_pricing_verified=True,
                provider_payment_verified=True,
                webhook_authenticity_verified=True,
                entitlement_projection_verified=True,
                refund_reversal_verified=True,
                receipt_history_verified=True,
            ),
            "standalone_acceptance": _row(),
            "branding_acceptance": _row(),
            "effects_systems_acceptance": _row(
                metadata_required_coverage_met=True,
                executable_required_coverage_met=True,
                metadata_only_counted_as_executable=False,
            ),
            "self_host_acceptance": _row(
                claimed_capabilities_smoke_verified=True,
                unverified_capabilities_claimed_ready=False,
            ),
        },
        "device_accessibility": {
            "desktop": _row(),
            "tablet": _row(),
            "mobile": _row(),
            "keyboard_only": _row(),
            "reduced_motion": _row(),
            "screen_reader_semantics": _row(),
        },
        "internal_gaps": {"open_count": 0},
        "duplicate_authority": {
            "consequential_duplicates_open": 0,
            "audit_head_sha": SHA,
            "evidence_ref": "test://authority-audit",
        },
        "external_blockers": {"blocking": [], "non_blocking": []},
        "release_controller": {
            "recommendation": "approve",
            "candidate_sha": SHA,
            "evidence_ref": "test://release-controller",
        },
    }


def test_complete_exact_sha_package_is_admissible():
    result = evaluate_final_release_admission(_valid_evidence(), candidate_sha=SHA)
    assert result.admissible is True
    assert result.blockers == ()
    assert result.as_dict()["secret_values_exposed"] is False


def test_missing_evidence_fails_closed():
    result = evaluate_final_release_admission({}, candidate_sha=SHA)
    assert result.admissible is False
    assert result.blockers


def test_stale_workflow_head_is_rejected():
    evidence = _valid_evidence()
    evidence["workflows"]["Security Gates"]["head_sha"] = "b" * 40
    result = evaluate_final_release_admission(evidence, candidate_sha=SHA)
    assert result.admissible is False
    assert any("Security Gates" in blocker and "exact candidate SHA" in blocker for blocker in result.blockers)


def test_restore_drill_must_prove_real_restore_integrity():
    evidence = _valid_evidence()
    evidence["gates"]["restore_drill"]["encrypted_backup_restored"] = False
    evidence["gates"]["restore_drill"]["restore_integrity_verified"] = False
    result = evaluate_final_release_admission(evidence, candidate_sha=SHA)
    assert result.admissible is False
    assert any("encrypted backup" in blocker for blocker in result.blockers)
    assert any("restored-data integrity" in blocker for blocker in result.blockers)


def test_commercial_browser_style_declaration_cannot_substitute_for_verification():
    evidence = _valid_evidence()
    evidence["gates"]["commercial_acceptance"]["provider_payment_verified"] = False
    evidence["gates"]["commercial_acceptance"]["webhook_authenticity_verified"] = False
    result = evaluate_final_release_admission(evidence, candidate_sha=SHA)
    assert result.admissible is False
    assert any("provider_payment_verified" in blocker for blocker in result.blockers)
    assert any("webhook_authenticity_verified" in blocker for blocker in result.blockers)


def test_metadata_only_effects_are_never_executable_evidence():
    evidence = _valid_evidence()
    evidence["gates"]["effects_systems_acceptance"]["metadata_only_counted_as_executable"] = True
    result = evaluate_final_release_admission(evidence, candidate_sha=SHA)
    assert result.admissible is False
    assert any("metadata-only" in blocker for blocker in result.blockers)


def test_open_internal_gap_or_duplicate_authority_blocks_release():
    evidence = _valid_evidence()
    evidence["internal_gaps"]["open_count"] = 1
    evidence["duplicate_authority"]["consequential_duplicates_open"] = 1
    result = evaluate_final_release_admission(evidence, candidate_sha=SHA)
    assert result.admissible is False
    assert any("internal gaps" in blocker for blocker in result.blockers)
    assert any("duplicate authority" in blocker for blocker in result.blockers)


def test_blocking_external_dependency_blocks_but_nonblocking_is_warning_only():
    evidence = _valid_evidence()
    evidence["external_blockers"] = {"blocking": ["provider approval pending"], "non_blocking": ["optional analytics"]}
    result = evaluate_final_release_admission(evidence, candidate_sha=SHA)
    assert result.admissible is False
    assert any("blocking external" in blocker for blocker in result.blockers)
    assert result.warnings

    evidence = deepcopy(_valid_evidence())
    evidence["external_blockers"] = {"blocking": [], "non_blocking": ["optional analytics"]}
    result = evaluate_final_release_admission(evidence, candidate_sha=SHA)
    assert result.admissible is True
    assert result.warnings


def test_bad_candidate_sha_is_never_admitted():
    result = evaluate_final_release_admission(_valid_evidence(), candidate_sha="short")
    assert result.admissible is False
    assert any("40-character" in blocker for blocker in result.blockers)
