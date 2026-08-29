from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aura_music_studio.aura_sec_threat_intel import (
    IntelSource,
    ThreatIntelEvidence,
    evaluate_intel_freshness,
    evidence_summary,
    payload_digest,
)


NOW = datetime(2026, 8, 27, 4, 30, tzinfo=timezone.utc)


def evidence(source: IntelSource, *, age: timedelta, version="test-version") -> ThreatIntelEvidence:
    return ThreatIntelEvidence(
        source=source,
        source_record_id="CVE-2026-12345",
        retrieved_at=NOW - age,
        published_at=NOW - age - timedelta(minutes=10),
        model_or_schema_version=version,
        confidence=0.95,
        licence_class="public-source-test-fixture",
        redistribution_allowed=False,
        raw_payload_sha256="a" * 64,
        evidence_url="https://example.test/evidence/CVE-2026-12345",
    )


def test_nvd_freshness_expires_in_hours_not_forever():
    fresh = evaluate_intel_freshness(evidence(IntelSource.NVD, age=timedelta(hours=1)), now=NOW)
    stale = evaluate_intel_freshness(evidence(IntelSource.NVD, age=timedelta(hours=8)), now=NOW)
    assert fresh["usable"] is True
    assert fresh["state"] == "fresh"
    assert stale["usable"] is False
    assert stale["state"] == "stale"


def test_epss_daily_data_has_longer_but_bounded_freshness_window():
    fresh = evaluate_intel_freshness(
        evidence(IntelSource.FIRST_EPSS, age=timedelta(hours=20), version="EPSS-v5"),
        now=NOW,
    )
    stale = evaluate_intel_freshness(
        evidence(IntelSource.FIRST_EPSS, age=timedelta(hours=48), version="EPSS-v5"),
        now=NOW,
    )
    assert fresh["usable"] is True
    assert stale["usable"] is False


def test_future_dated_feed_record_fails_closed():
    item = ThreatIntelEvidence(
        source=IntelSource.CISA_KEV,
        source_record_id="CVE-2026-99999",
        retrieved_at=NOW + timedelta(hours=1),
        published_at=NOW,
        model_or_schema_version="kev-test",
        confidence=1.0,
        licence_class="public-source-test-fixture",
        redistribution_allowed=False,
        raw_payload_sha256="b" * 64,
    )
    result = evaluate_intel_freshness(item, now=NOW)
    assert result["usable"] is False
    assert result["state"] == "invalid_future_timestamp"


def test_summary_preserves_source_model_licence_and_hash_provenance():
    item = evidence(IntelSource.FIRST_EPSS, age=timedelta(hours=2), version="EPSS-v5")
    summary = evidence_summary(item, now=NOW)
    assert summary["source"] == "first_epss"
    assert summary["model_or_schema_version"] == "EPSS-v5"
    assert summary["redistribution_allowed"] is False
    assert summary["raw_payload_sha256"] == "a" * 64
    assert summary["freshness"]["usable"] is True


def test_raw_provider_payload_can_be_bound_to_a_digest_without_storing_it_in_member_output():
    assert payload_digest(b"provider response fixture") != payload_digest(b"different response")
    assert len(payload_digest(b"provider response fixture")) == 64
