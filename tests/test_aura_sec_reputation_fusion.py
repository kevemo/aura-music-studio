from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.aura_sec_browser_guard import VerifiedReputationEvidence, evaluate_url
from aura_music_studio.aura_sec_reputation_fusion import (
    ReputationProviderPolicy,
    build_reputation_fusion_verifier,
)


NOW = datetime(2026, 8, 28, 15, 0, tzinfo=timezone.utc)


def _adapter(
    provider_id: str,
    *,
    verdict: str,
    confidence: float,
    observed_delta: timedelta = timedelta(minutes=-5),
    expires_delta: timedelta = timedelta(minutes=30),
    digest_override: str | None = None,
):
    def verify(response, context):
        if response != {"proof": f"verified:{provider_id}"}:
            return None
        observed = NOW + observed_delta
        expires = NOW + expires_delta
        digest = hashlib.sha256(
            context.evidence_payload(
                indicator_type="host",
                indicator=context.host,
                verdict=verdict,
                confidence=confidence,
                provider_id=provider_id,
                observed_at=observed,
                expires_at=expires,
            )
        ).hexdigest()
        return VerifiedReputationEvidence(
            indicator_type="host",
            indicator=context.host,
            verdict=verdict,
            confidence=confidence,
            provider_id=provider_id,
            observed_at=observed,
            expires_at=expires,
            evidence_digest=digest_override or digest,
        )

    return verify


def _fusion(*, policies, adapters):
    return build_reputation_fusion_verifier(
        provider_policies=policies,
        provider_verifiers=adapters,
        clock=lambda: NOW,
    )


def _payload(*provider_ids: str):
    return {
        "provider_results": [
            {"provider_id": provider_id, "response": {"proof": f"verified:{provider_id}"}}
            for provider_id in provider_ids
        ]
    }


def test_two_independent_high_confidence_threat_feeds_can_corroborate_a_block():
    policies = {
        "feed-a": ReputationProviderPolicy("feed-a"),
        "feed-b": ReputationProviderPolicy("feed-b"),
    }
    verifier = _fusion(
        policies=policies,
        adapters={
            "feed-a": _adapter("feed-a", verdict="phishing", confidence=0.96),
            "feed-b": _adapter("feed-b", verdict="malicious", confidence=0.94),
        },
    )
    decision = evaluate_url(
        "https://signin.example/account",
        reputation_payload=_payload("feed-a", "feed-b"),
        reputation_verifier=verifier,
        now=NOW,
    )
    assert decision.verdict == "block"
    assert decision.hard_block_reason == "verified_reputation"
    assert decision.reputation["provider_id"] == "aura-sec-reputation-fusion-v1"
    assert decision.reputation["verdict"] == "malicious"


def test_ordinary_single_source_threat_hit_warns_instead_of_hard_blocking():
    policies = {"feed-a": ReputationProviderPolicy("feed-a")}
    verifier = _fusion(
        policies=policies,
        adapters={"feed-a": _adapter("feed-a", verdict="phishing", confidence=0.99)},
    )
    decision = evaluate_url(
        "https://signin.example/account",
        reputation_payload=_payload("feed-a"),
        reputation_verifier=verifier,
        now=NOW,
    )
    assert decision.verdict == "warn"
    assert decision.hard_block_reason is None
    assert decision.reputation["verdict"] == "suspicious"
    assert decision.reputation["confidence"] < 0.70


def test_server_authorized_single_source_can_block_only_at_its_high_threshold():
    policy = ReputationProviderPolicy(
        "authoritative-feed",
        can_single_source_block=True,
        single_source_block_confidence=0.98,
    )
    verifier = _fusion(
        policies={"authoritative-feed": policy},
        adapters={
            "authoritative-feed": _adapter(
                "authoritative-feed", verdict="phishing", confidence=0.99
            )
        },
    )
    decision = evaluate_url(
        "https://signin.example/account",
        reputation_payload=_payload("authoritative-feed"),
        reputation_verifier=verifier,
        now=NOW,
    )
    assert decision.verdict == "block"
    assert decision.hard_block_reason == "verified_reputation"


def test_high_confidence_benign_conflict_vetoes_hard_block_and_surfaces_caution():
    policies = {
        "bad-feed": ReputationProviderPolicy("bad-feed", can_single_source_block=True),
        "benign-feed": ReputationProviderPolicy("benign-feed"),
    }
    verifier = _fusion(
        policies=policies,
        adapters={
            "bad-feed": _adapter("bad-feed", verdict="malicious", confidence=0.99),
            "benign-feed": _adapter("benign-feed", verdict="benign", confidence=0.98),
        },
    )
    decision = evaluate_url(
        "https://example.com/",
        reputation_payload=_payload("bad-feed", "benign-feed"),
        reputation_verifier=verifier,
        now=NOW,
    )
    assert decision.verdict == "warn"
    assert decision.hard_block_reason is None
    assert decision.reputation["verdict"] == "suspicious"
    assert 0.40 <= decision.reputation["confidence"] < 0.70


def test_benign_reputation_never_erases_local_browser_guard_risk():
    policies = {
        "feed-a": ReputationProviderPolicy("feed-a"),
        "feed-b": ReputationProviderPolicy("feed-b"),
    }
    verifier = _fusion(
        policies=policies,
        adapters={
            "feed-a": _adapter("feed-a", verdict="benign", confidence=0.97),
            "feed-b": _adapter("feed-b", verdict="benign", confidence=0.95),
        },
    )
    decision = evaluate_url(
        "http://example.com:8080/login/payment",
        reputation_payload=_payload("feed-a", "feed-b"),
        reputation_verifier=verifier,
        now=NOW,
    )
    assert decision.reputation["verdict"] == "benign"
    assert decision.verdict == "warn"
    assert decision.risk_score >= 20


def test_unknown_duplicate_or_policy_injected_provider_results_fail_closed():
    verifier = _fusion(
        policies={"feed-a": ReputationProviderPolicy("feed-a")},
        adapters={"feed-a": _adapter("feed-a", verdict="benign", confidence=0.95)},
    )
    with pytest.raises(PermissionError, match="unconfigured provider"):
        evaluate_url(
            "https://example.com/",
            reputation_payload=_payload("unknown-feed"),
            reputation_verifier=verifier,
            now=NOW,
        )
    with pytest.raises(PermissionError, match="duplicate provider"):
        evaluate_url(
            "https://example.com/",
            reputation_payload=_payload("feed-a", "feed-a"),
            reputation_verifier=verifier,
            now=NOW,
        )
    with pytest.raises(PermissionError, match="invalid shape"):
        evaluate_url(
            "https://example.com/",
            reputation_payload={
                "provider_results": [
                    {
                        "provider_id": "feed-a",
                        "response": {"proof": "verified:feed-a"},
                        "can_single_source_block": True,
                    }
                ]
            },
            reputation_verifier=verifier,
            now=NOW,
        )


def test_tampered_or_expired_provider_evidence_never_becomes_fused_authority():
    policies = {"feed-a": ReputationProviderPolicy("feed-a")}
    tampered = _fusion(
        policies=policies,
        adapters={
            "feed-a": _adapter(
                "feed-a", verdict="phishing", confidence=0.99, digest_override="a" * 64
            )
        },
    )
    with pytest.raises(PermissionError, match="was not verified"):
        evaluate_url(
            "https://example.com/",
            reputation_payload=_payload("feed-a"),
            reputation_verifier=tampered,
            now=NOW,
        )

    expired = _fusion(
        policies=policies,
        adapters={
            "feed-a": _adapter(
                "feed-a",
                verdict="phishing",
                confidence=0.99,
                observed_delta=timedelta(minutes=-40),
                expires_delta=timedelta(minutes=-1),
            )
        },
    )
    with pytest.raises(PermissionError, match="was not verified"):
        evaluate_url(
            "https://example.com/",
            reputation_payload=_payload("feed-a"),
            reputation_verifier=expired,
            now=NOW,
        )


def test_raw_payload_verdict_cannot_bypass_provider_adapter_verification():
    verifier = _fusion(
        policies={"feed-a": ReputationProviderPolicy("feed-a", can_single_source_block=True)},
        adapters={"feed-a": _adapter("feed-a", verdict="malicious", confidence=0.99)},
    )
    with pytest.raises(PermissionError, match="was not verified"):
        evaluate_url(
            "https://example.com/",
            reputation_payload={
                "provider_results": [
                    {
                        "provider_id": "feed-a",
                        "response": {
                            "verdict": "malicious",
                            "confidence": 1.0,
                            "proof": "browser-claimed",
                        },
                    }
                ]
            },
            reputation_verifier=verifier,
            now=NOW,
        )


def test_provider_evidence_count_is_bounded():
    policies = {
        f"feed-{index}": ReputationProviderPolicy(f"feed-{index}") for index in range(9)
    }
    adapters = {
        provider_id: _adapter(provider_id, verdict="benign", confidence=0.95)
        for provider_id in policies
    }
    verifier = _fusion(policies=policies, adapters=adapters)
    with pytest.raises(PermissionError, match="provider evidence limit"):
        evaluate_url(
            "https://example.com/",
            reputation_payload=_payload(*policies.keys()),
            reputation_verifier=verifier,
            now=NOW,
        )


def test_configuration_requires_exact_policy_verifier_pairing():
    with pytest.raises(ValueError, match="missing"):
        build_reputation_fusion_verifier(
            provider_policies={"feed-a": ReputationProviderPolicy("feed-a")},
            provider_verifiers={},
        )
    with pytest.raises(ValueError, match="without a server-owned"):
        build_reputation_fusion_verifier(
            provider_policies={},
            provider_verifiers={"feed-a": _adapter("feed-a", verdict="benign", confidence=0.95)},
        )
