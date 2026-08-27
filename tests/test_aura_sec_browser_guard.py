from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.aura_sec_browser_guard import (
    BrowserLocalPolicy,
    VerifiedReputationEvidence,
    evaluate_url,
    strip_tracking_parameters,
)


NOW = datetime(2026, 8, 27, 22, 0, tzinfo=timezone.utc)


def _reputation_verifier(
    *,
    verdict="phishing",
    confidence=0.97,
    indicator_type="host",
    indicator_override=None,
    expires_delta=timedelta(hours=2),
    digest_override=None,
):
    def verify(payload, context):
        if payload.get("provider_response") != "verified-fixture":
            return None
        indicator = indicator_override or (context.host if indicator_type == "host" else context.normalized_url)
        observed = NOW - timedelta(minutes=5)
        expires = NOW + expires_delta
        evidence_digest = hashlib.sha256(
            context.evidence_payload(
                indicator_type=indicator_type,
                indicator=indicator,
                verdict=verdict,
                confidence=confidence,
                provider_id="test-browser-reputation",
                observed_at=observed,
                expires_at=expires,
            )
        ).hexdigest()
        return VerifiedReputationEvidence(
            indicator_type=indicator_type,
            indicator=indicator,
            verdict=verdict,
            confidence=confidence,
            provider_id="test-browser-reputation",
            observed_at=observed,
            expires_at=expires,
            evidence_digest=digest_override or evidence_digest,
        )

    return verify


def test_normal_https_navigation_is_allowed_and_canonicalized():
    decision = evaluate_url("HTTPS://Example.COM:443/path?q=1", now=NOW)
    assert decision.verdict == "allow"
    assert decision.host == "example.com"
    assert decision.normalized_url == "https://example.com/path?q=1"
    assert decision.credentials_redacted is False


def test_executable_browser_schemes_are_hard_blocked_without_reputation():
    decision = evaluate_url("javascript:alert(1)", source="link", now=NOW)
    assert decision.verdict == "block"
    assert decision.hard_block_reason == "dangerous_scheme"
    assert decision.risk_score == 100
    assert decision.normalized_url is None


def test_embedded_credentials_are_redacted_and_warned():
    decision = evaluate_url("https://user:secret@example.com/account/login", now=NOW)
    assert decision.verdict == "warn"
    assert decision.credentials_redacted is True
    assert decision.normalized_url == "https://example.com/account/login"
    assert "secret" not in decision.normalized_url
    assert any(signal.code == "embedded_credentials" for signal in decision.signals)


def test_heuristics_warn_but_do_not_claim_verified_maliciousness():
    decision = evaluate_url(
        "http://203.0.113.40:8088/verify/account/payment?login=true",
        source="email",
        now=NOW,
    )
    assert decision.verdict == "warn"
    assert decision.hard_block_reason is None
    assert decision.reputation is None
    assert decision.risk_score >= 20


def test_qr_decoded_link_uses_same_policy_engine():
    decision = evaluate_url("https://example.com/verify-wallet", source="qr", now=NOW)
    assert decision.source == "qr"
    assert decision.verdict in {"allow", "warn"}


def test_unicode_hostname_is_normalized_for_comparison():
    decision = evaluate_url("https://bücher.example/", now=NOW)
    assert decision.host == "xn--bcher-kva.example"
    assert decision.normalized_url == "https://xn--bcher-kva.example/"
    assert any(signal.code == "internationalized_hostname" for signal in decision.signals)
    assert any(signal.code == "punycode_hostname" for signal in decision.signals)


def test_exact_local_block_rule_blocks_but_trust_rule_is_not_a_threat_bypass():
    blocked = BrowserLocalPolicy.build(blocked_hosts={"blocked.example"})
    decision = evaluate_url("https://blocked.example/path", local_policy=blocked, now=NOW)
    assert decision.verdict == "block"
    assert decision.hard_block_reason == "local_policy"

    trusted = BrowserLocalPolicy.build(trusted_hosts={"trusted.example"})
    verified_bad = evaluate_url(
        "https://trusted.example/login",
        local_policy=trusted,
        reputation_payload={"provider_response": "verified-fixture"},
        reputation_verifier=_reputation_verifier(),
        now=NOW,
    )
    assert verified_bad.verdict == "block"
    assert verified_bad.hard_block_reason == "verified_reputation"


def test_verified_fresh_phishing_reputation_can_hard_block():
    decision = evaluate_url(
        "https://signin.example/account",
        reputation_payload={"provider_response": "verified-fixture"},
        reputation_verifier=_reputation_verifier(verdict="phishing", confidence=0.98),
        now=NOW,
    )
    assert decision.verdict == "block"
    assert decision.hard_block_reason == "verified_reputation"
    assert decision.reputation["provider_id"] == "test-browser-reputation"
    assert decision.reputation["verdict"] == "phishing"


def test_reputation_requires_real_verifier_and_exact_destination_binding():
    with pytest.raises(PermissionError, match="trusted Aura Sec Browser Guard reputation verifier"):
        evaluate_url(
            "https://example.com/",
            reputation_payload={"provider_response": "verified-fixture"},
            reputation_verifier=None,
            now=NOW,
        )

    with pytest.raises(PermissionError, match="indicator does not match"):
        evaluate_url(
            "https://example.com/",
            reputation_payload={"provider_response": "verified-fixture"},
            reputation_verifier=_reputation_verifier(indicator_override="other.example"),
            now=NOW,
        )


def test_reputation_rejects_expired_and_tampered_evidence():
    with pytest.raises(PermissionError, match="expired"):
        evaluate_url(
            "https://example.com/",
            reputation_payload={"provider_response": "verified-fixture"},
            reputation_verifier=_reputation_verifier(expires_delta=timedelta(minutes=-1)),
            now=NOW,
        )

    with pytest.raises(PermissionError, match="digest"):
        evaluate_url(
            "https://example.com/",
            reputation_payload={"provider_response": "verified-fixture"},
            reputation_verifier=_reputation_verifier(digest_override="a" * 64),
            now=NOW,
        )


def test_verified_benign_reputation_does_not_erase_local_warning_signals():
    decision = evaluate_url(
        "http://example.com:8080/login/payment",
        reputation_payload={"provider_response": "verified-fixture"},
        reputation_verifier=_reputation_verifier(verdict="benign", confidence=0.99),
        now=NOW,
    )
    assert decision.reputation["verdict"] == "benign"
    assert decision.verdict == "warn"
    assert decision.risk_score >= 20


def test_tracking_cleanup_is_conservative_and_preserves_application_parameters():
    cleaned, removed = strip_tracking_parameters(
        "https://example.com/watch?id=42&utm_source=test&fbclid=abc&mode=full&id=43#chapter"
    )
    assert cleaned == "https://example.com/watch?id=42&mode=full&id=43#chapter"
    assert removed == ("utm_source", "fbclid")

    decision = evaluate_url(
        "https://example.com/watch?id=42&utm_campaign=launch&ttclid=xyz",
        now=NOW,
    )
    assert decision.privacy_clean_url == "https://example.com/watch?id=42"
    assert decision.tracking_parameters_removed == ("utm_campaign", "ttclid")


def test_invalid_or_ambiguous_remote_urls_fail_closed():
    with pytest.raises(ValueError, match="explicit URL scheme"):
        evaluate_url("example.com/login", now=NOW)
    with pytest.raises(ValueError, match="Backslashes"):
        evaluate_url("https://example.com\\@attacker.test/", now=NOW)
    with pytest.raises(ValueError, match="control characters or whitespace"):
        evaluate_url("https://example.com/a b", now=NOW)
    with pytest.raises(ValueError, match="Unsupported Browser Guard URL scheme"):
        evaluate_url("file:///tmp/example", now=NOW)


def test_policy_rules_are_exact_host_only_and_conflicts_are_rejected():
    policy = BrowserLocalPolicy.build(trusted_hosts={"example.com"})
    child = evaluate_url("https://sub.example.com/", local_policy=policy, now=NOW)
    assert not any(signal.code == "local_trust_rule" for signal in child.signals)

    with pytest.raises(ValueError, match="both trusted and blocked"):
        BrowserLocalPolicy.build(trusted_hosts={"example.com"}, blocked_hosts={"EXAMPLE.COM."})
