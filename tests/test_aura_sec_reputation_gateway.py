from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.aura_sec_browser_guard import (
    ReputationVerificationContext,
    VerifiedReputationEvidence,
)
from aura_music_studio.aura_sec_reputation_fusion import ReputationProviderPolicy
from aura_music_studio.aura_sec_reputation_gateway import (
    ReputationRuntimeAdapter,
    run_reputation_gateway,
)
from aura_music_studio.aura_sec_reputation_providers import ProviderHealthEvidence


NOW = datetime(2026, 8, 29, 7, 0, tzinfo=timezone.utc)
CONTEXT = ReputationVerificationContext(
    normalized_url="https://example.com/account/login?session=redacted",
    host="example.com",
    source="link",
)


def _google_env() -> dict[str, str]:
    return {
        "AURA_SEC_GOOGLE_WEB_RISK_ENABLED": "true",
        "AURA_SEC_GOOGLE_WEB_RISK_COMMERCIAL_APPROVED": "true",
        "AURA_SEC_GOOGLE_WEB_RISK_CREDENTIAL": "fixture-value",
    }


def _openphish_env() -> dict[str, str]:
    return {
        "AURA_SEC_OPENPHISH_COMMERCIAL_ENABLED": "true",
        "AURA_SEC_OPENPHISH_COMMERCIAL_APPROVED": "true",
        "AURA_SEC_OPENPHISH_COMMERCIAL_CREDENTIAL": "fixture-value",
    }


def _health(provider_id: str, *, checked_at: datetime = NOW) -> ProviderHealthEvidence:
    return ProviderHealthEvidence(
        provider_id=provider_id,
        checked_at=checked_at,
        service_responding=True,
        credential_verified=True,
        transport_verified=True,
    )


def _verifier(provider_id: str, *, verdict: str, confidence: float):
    def verify(response, context):
        if response != {"fixture": provider_id}:
            return None
        observed = NOW - timedelta(minutes=3)
        expires = NOW + timedelta(minutes=30)
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
            evidence_digest=digest,
        )

    return verify


def _runtime(
    provider_id: str,
    *,
    verdict: str,
    confidence: float,
    lookup,
    indicator_type: str = "host",
    policy: ReputationProviderPolicy | None = None,
):
    return ReputationRuntimeAdapter(
        provider_id=provider_id,
        indicator_type=indicator_type,
        lookup=lookup,
        verifier=_verifier(provider_id, verdict=verdict, confidence=confidence),
        policy=policy or ReputationProviderPolicy(provider_id),
    )


def test_disabled_provider_is_never_called():
    calls = []

    def lookup(request):
        calls.append(request)
        return {"fixture": "google-web-risk"}

    result = run_reputation_gateway(
        context=CONTEXT,
        runtimes={
            "google-web-risk": _runtime(
                "google-web-risk", verdict="phishing", confidence=0.99, lookup=lookup
            )
        },
        env={},
        health={"google-web-risk": _health("google-web-risk")},
        now=NOW,
    )
    assert calls == []
    assert result.evidence is None
    assert result.provider_states[0].readiness_state == "disabled"
    assert result.provider_states[0].queried is False


def test_provider_without_fresh_health_proof_is_never_called():
    calls = []

    def lookup(request):
        calls.append(request)
        return {"fixture": "google-web-risk"}

    result = run_reputation_gateway(
        context=CONTEXT,
        runtimes={
            "google-web-risk": _runtime(
                "google-web-risk", verdict="malicious", confidence=0.99, lookup=lookup
            )
        },
        env=_google_env(),
        health={
            "google-web-risk": _health(
                "google-web-risk", checked_at=NOW - timedelta(minutes=16)
            )
        },
        now=NOW,
    )
    assert calls == []
    assert result.evidence is None
    assert result.provider_states[0].readiness_state == "adapter_unhealthy"


def test_host_only_provider_receives_no_url_path_query_or_credentials():
    captured = []

    def lookup(request):
        captured.append(request)
        return {"fixture": "google-web-risk"}

    result = run_reputation_gateway(
        context=CONTEXT,
        runtimes={
            "google-web-risk": _runtime(
                "google-web-risk", verdict="phishing", confidence=0.99, lookup=lookup
            )
        },
        env=_google_env(),
        health={"google-web-risk": _health("google-web-risk")},
        now=NOW,
    )
    assert len(captured) == 1
    request = captured[0]
    assert request.indicator_type == "host"
    assert request.indicator == "example.com"
    assert "account" not in request.indicator
    assert "session" not in request.indicator
    assert not hasattr(request, "credential")
    assert result.evidence is not None
    # A normal single-source harmful result becomes caution rather than hard-block authority.
    assert result.evidence.verdict == "suspicious"
    assert result.evidence.confidence < 0.70


def test_url_scoped_provider_receives_only_normalized_url_indicator():
    captured = []

    def lookup(request):
        captured.append(request)
        return {"fixture": "google-web-risk"}

    run_reputation_gateway(
        context=CONTEXT,
        runtimes={
            "google-web-risk": _runtime(
                "google-web-risk",
                verdict="benign",
                confidence=0.96,
                lookup=lookup,
                indicator_type="url",
            )
        },
        env=_google_env(),
        health={"google-web-risk": _health("google-web-risk")},
        now=NOW,
    )
    assert captured[0].indicator_type == "url"
    assert captured[0].indicator == CONTEXT.normalized_url


def test_two_ready_independent_providers_can_produce_corroborated_threat_evidence():
    env = {**_google_env(), **_openphish_env()}
    health = {
        "google-web-risk": _health("google-web-risk"),
        "openphish-commercial": _health("openphish-commercial"),
    }
    runtimes = {
        "google-web-risk": _runtime(
            "google-web-risk",
            verdict="phishing",
            confidence=0.96,
            lookup=lambda _request: {"fixture": "google-web-risk"},
        ),
        "openphish-commercial": _runtime(
            "openphish-commercial",
            verdict="malicious",
            confidence=0.94,
            lookup=lambda _request: {"fixture": "openphish-commercial"},
        ),
    }
    result = run_reputation_gateway(
        context=CONTEXT,
        runtimes=runtimes,
        env=env,
        health=health,
        now=NOW,
    )
    assert result.evidence is not None
    assert result.evidence.verdict == "malicious"
    assert result.evidence.confidence >= 0.70
    assert result.evidence.provider_id == "aura-sec-reputation-fusion-v1"
    assert result.independent_corroboration_attempted is True
    assert result.fusion_failed is False


def test_lookup_failure_is_isolated_from_other_provider_evidence():
    def broken_lookup(_request):
        raise RuntimeError("provider unavailable")

    result = run_reputation_gateway(
        context=CONTEXT,
        runtimes={
            "google-web-risk": _runtime(
                "google-web-risk",
                verdict="phishing",
                confidence=0.97,
                lookup=lambda _request: {"fixture": "google-web-risk"},
            ),
            "openphish-commercial": _runtime(
                "openphish-commercial",
                verdict="malicious",
                confidence=0.99,
                lookup=broken_lookup,
            ),
        },
        env={**_google_env(), **_openphish_env()},
        health={
            "google-web-risk": _health("google-web-risk"),
            "openphish-commercial": _health("openphish-commercial"),
        },
        now=NOW,
    )
    assert result.evidence is not None
    assert result.evidence.verdict == "suspicious"
    openphish = next(
        item for item in result.provider_states if item.provider_id == "openphish-commercial"
    )
    assert openphish.queried is True
    assert openphish.lookup_failed is True
    assert openphish.returned_data is False


def test_no_result_is_not_misreported_as_provider_failure():
    result = run_reputation_gateway(
        context=CONTEXT,
        runtimes={
            "google-web-risk": _runtime(
                "google-web-risk", verdict="benign", confidence=0.95, lookup=lambda _request: None
            )
        },
        env=_google_env(),
        health={"google-web-risk": _health("google-web-risk")},
        now=NOW,
    )
    assert result.evidence is None
    state = result.provider_states[0]
    assert state.queried is True
    assert state.returned_data is False
    assert state.lookup_failed is False


def test_commercially_blocked_safe_browsing_runtime_is_never_called():
    calls = []

    runtime = _runtime(
        "google-safe-browsing",
        verdict="malicious",
        confidence=0.99,
        lookup=lambda request: calls.append(request) or {"fixture": "google-safe-browsing"},
    )
    result = run_reputation_gateway(
        context=CONTEXT,
        runtimes={"google-safe-browsing": runtime},
        env={"AURA_SEC_GOOGLE_SAFE_BROWSING_ENABLED": "true"},
        health={"google-safe-browsing": _health("google-safe-browsing")},
        now=NOW,
    )
    assert calls == []
    assert result.evidence is None
    assert result.provider_states[0].readiness_state == "blocked_commercial_use"


def test_runtime_mapping_cannot_alias_or_inject_provider_identity():
    runtime = _runtime(
        "google-web-risk",
        verdict="benign",
        confidence=0.95,
        lookup=lambda _request: {"fixture": "google-web-risk"},
    )
    with pytest.raises(ValueError, match="key must match provider_id"):
        run_reputation_gateway(
            context=CONTEXT,
            runtimes={"openphish-commercial": runtime},
            env=_google_env(),
            health={"google-web-risk": _health("google-web-risk")},
            now=NOW,
        )


def test_public_summary_never_contains_raw_provider_responses_or_credential_values():
    secret_sentinel = "SHOULD-NOT-LEAK-7719"

    def lookup(_request):
        return {"fixture": "google-web-risk", "opaque": secret_sentinel}

    result = run_reputation_gateway(
        context=CONTEXT,
        runtimes={
            "google-web-risk": _runtime(
                "google-web-risk", verdict="benign", confidence=0.95, lookup=lookup
            )
        },
        env={
            **_google_env(),
            "AURA_SEC_GOOGLE_WEB_RISK_CREDENTIAL": secret_sentinel,
        },
        health={"google-web-risk": _health("google-web-risk")},
        now=NOW,
    )
    summary = result.public_summary()
    rendered = repr(summary)
    assert secret_sentinel not in rendered
    assert summary["raw_provider_responses_exposed"] is False
    assert summary["credential_values_exposed"] is False
    assert "response" not in summary["provider_states"][0]


def test_invalid_context_or_unknown_runtime_fails_before_lookup():
    with pytest.raises(TypeError, match="verification context"):
        run_reputation_gateway(context=object(), runtimes={}, now=NOW)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="unknown provider"):
        ReputationRuntimeAdapter(
            provider_id="browser-supplied-provider",
            indicator_type="host",
            lookup=lambda _request: None,
            verifier=lambda _response, _context: None,
            policy=ReputationProviderPolicy("browser-supplied-provider"),
        )
