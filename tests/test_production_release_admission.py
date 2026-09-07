from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.production_release_admission import (
    REQUIRED_PRODUCTION_RELEASE_GATES,
    ProductionReleaseEvidence,
    assess_production_release,
    normalize_release_evidence,
)

SHA = "a" * 40
NOW = datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)


def evidence(gate: str, **overrides) -> ProductionReleaseEvidence:
    values = {
        "gate": gate,
        "release_sha": SHA,
        "environment": "production",
        "outcome": "passed",
        "evidence_digest": "b" * 64,
        "evidence_ref": f"urn:evidence:{gate}:20260901",
        "verifier": "release-operations",
        "observed_at": NOW - timedelta(minutes=10),
        "expires_at": NOW + timedelta(hours=2),
    }
    values.update(overrides)
    return ProductionReleaseEvidence(**values)


def all_evidence() -> list[ProductionReleaseEvidence]:
    return [evidence(gate) for gate in sorted(REQUIRED_PRODUCTION_RELEASE_GATES)]


def test_all_required_exact_sha_fresh_production_evidence_admits() -> None:
    result = assess_production_release(SHA, all_evidence(), evaluated_at=NOW)
    assert result.admitted is True
    assert result.missing_gates == ()
    assert result.rejected_gates == {}
    assert set(result.accepted_gates) == REQUIRED_PRODUCTION_RELEASE_GATES


def test_missing_gate_fails_closed() -> None:
    rows = [row for row in all_evidence() if row.gate != "backup_restore_drill"]
    result = assess_production_release(SHA, rows, evaluated_at=NOW)
    assert result.admitted is False
    assert result.missing_gates == ("backup_restore_drill",)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"release_sha": "c" * 40}, "release_sha_mismatch"),
        ({"environment": "staging"}, "non_production_environment"),
        ({"outcome": "warning"}, "outcome_not_passed"),
        ({"observed_at": NOW + timedelta(seconds=1)}, "observation_in_future"),
        ({"expires_at": NOW}, "evidence_expired"),
    ],
)
def test_invalid_gate_evidence_is_rejected(overrides: dict, reason: str) -> None:
    rows = all_evidence()
    gate = "domain_tls"
    rows = [row for row in rows if row.gate != gate]
    rows.append(evidence(gate, **overrides))
    result = assess_production_release(SHA, rows, evaluated_at=NOW)
    assert result.admitted is False
    assert reason in result.rejected_gates[gate]


def test_duplicate_evidence_for_gate_fails_closed() -> None:
    rows = all_evidence()
    rows.append(evidence("monitoring_alerting", evidence_digest="d" * 64))
    result = assess_production_release(SHA, rows, evaluated_at=NOW)
    assert result.admitted is False
    assert result.rejected_gates["monitoring_alerting"] == ("duplicate_evidence",)


def test_evidence_for_unknown_gate_cannot_substitute_for_required_gate() -> None:
    rows = [row for row in all_evidence() if row.gate != "privacy_security_review"]
    rows.append(evidence("some_other_review"))
    result = assess_production_release(SHA, rows, evaluated_at=NOW)
    assert result.admitted is False
    assert result.missing_gates == ("privacy_security_review",)


def test_custom_required_gate_set_still_fails_closed() -> None:
    result = assess_production_release(
        SHA,
        [evidence("domain_tls")],
        evaluated_at=NOW,
        required_gates={"domain_tls", "rollback_proof"},
    )
    assert result.admitted is False
    assert result.missing_gates == ("rollback_proof",)


def test_empty_required_gate_set_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one"):
        assess_production_release(SHA, [], evaluated_at=NOW, required_gates=[])


def test_naive_timestamps_are_rejected() -> None:
    item = evidence("domain_tls", observed_at=datetime(2026, 9, 1, 8, 0))
    with pytest.raises(ValueError, match="timezone-aware"):
        normalize_release_evidence(item)


def test_evidence_expiry_must_follow_observation() -> None:
    item = evidence(
        "domain_tls",
        observed_at=NOW,
        expires_at=NOW - timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="expiry"):
        normalize_release_evidence(item)


@pytest.mark.parametrize("evidence_ref", ["http://example.com/report", "file:///tmp/report", "report-123"])
def test_untrusted_evidence_reference_schemes_are_rejected(evidence_ref: str) -> None:
    with pytest.raises(ValueError, match="HTTPS or urn:evidence"):
        normalize_release_evidence(evidence("domain_tls", evidence_ref=evidence_ref))


def test_https_evidence_reference_is_accepted() -> None:
    item = normalize_release_evidence(
        evidence("domain_tls", evidence_ref="https://evidence.example.test/releases/domain-tls.json")
    )
    assert item.evidence_ref.startswith("https://")


def test_short_sha_and_non_sha256_digest_are_rejected() -> None:
    with pytest.raises(ValueError, match="release_sha"):
        assess_production_release("abc123", [], evaluated_at=NOW)
    with pytest.raises(ValueError, match="SHA-256"):
        normalize_release_evidence(evidence("domain_tls", evidence_digest="not-a-digest"))
