from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.aura_sec_reputation_providers import (
    PROVIDERS,
    ProviderHealthEvidence,
    cloud_reputation_readiness,
    provider_readiness,
    provider_registry_snapshot,
    provider_spec,
)


NOW = datetime(2026, 8, 28, 16, 0, tzinfo=timezone.utc)


def _configured_env(provider_id: str) -> dict[str, str]:
    spec = provider_spec(provider_id)
    env = {spec.enable_env_name: "true"}
    assert spec.commercial_approval_env_name is not None
    env[spec.commercial_approval_env_name] = "true"
    for name in spec.credential_env_names:
        env[name] = "configured-placeholder"
    return env


def _healthy(provider_id: str, *, checked_at: datetime = NOW) -> ProviderHealthEvidence:
    return ProviderHealthEvidence(
        provider_id=provider_id,
        checked_at=checked_at,
        service_responding=True,
        credential_verified=True,
        transport_verified=True,
    )


def test_commercial_providers_are_disabled_by_default_and_safe_browsing_is_blocked():
    snapshot = {item.provider_id: item for item in provider_registry_snapshot(env={}, now=NOW)}
    assert snapshot["google-web-risk"].state == "disabled"
    assert snapshot["openphish-commercial"].state == "disabled"
    assert snapshot["spamhaus-intelligence"].state == "disabled"
    assert snapshot["abusech-commercial"].state == "disabled"
    safe_browsing = snapshot["google-safe-browsing"]
    assert safe_browsing.state == "blocked_commercial_use"
    assert safe_browsing.commercial_runtime_allowed is False
    assert safe_browsing.enabled is False


def test_enable_flag_does_not_bypass_explicit_commercial_approval():
    spec = provider_spec("google-web-risk")
    readiness = provider_readiness(
        spec.provider_id,
        env={spec.enable_env_name: "true"},
        now=NOW,
    )
    assert readiness.state == "commercial_approval_required"
    assert readiness.ready is False


def test_commercial_approval_without_credentials_fails_closed():
    spec = provider_spec("openphish-commercial")
    readiness = provider_readiness(
        spec.provider_id,
        env={
            spec.enable_env_name: "true",
            spec.commercial_approval_env_name: "true",
        },
        now=NOW,
    )
    assert readiness.state == "credentials_missing"
    assert readiness.credentials_configured is False


def test_credential_presence_is_configuration_not_provider_readiness():
    provider_id = "spamhaus-intelligence"
    readiness = provider_readiness(provider_id, env=_configured_env(provider_id), now=NOW)
    assert readiness.credentials_configured is True
    assert readiness.adapter_health_verified is False
    assert readiness.state == "configured_not_verified"
    assert readiness.ready is False


def test_ready_requires_fresh_adapter_health_and_credential_verification():
    provider_id = "google-web-risk"
    readiness = provider_readiness(
        provider_id,
        env=_configured_env(provider_id),
        health={provider_id: _healthy(provider_id)},
        now=NOW,
    )
    assert readiness.state == "ready"
    assert readiness.ready is True
    assert readiness.adapter_health_verified is True


def test_stale_future_or_unhealthy_adapter_evidence_never_makes_provider_ready():
    provider_id = "google-web-risk"
    env = _configured_env(provider_id)

    stale = provider_readiness(
        provider_id,
        env=env,
        health={provider_id: _healthy(provider_id, checked_at=NOW - timedelta(minutes=16))},
        now=NOW,
    )
    assert stale.state == "adapter_unhealthy"

    future = provider_readiness(
        provider_id,
        env=env,
        health={provider_id: _healthy(provider_id, checked_at=NOW + timedelta(minutes=3))},
        now=NOW,
    )
    assert future.state == "adapter_unhealthy"

    unhealthy = ProviderHealthEvidence(
        provider_id=provider_id,
        checked_at=NOW,
        service_responding=True,
        credential_verified=False,
        transport_verified=True,
    )
    failed = provider_readiness(
        provider_id,
        env=env,
        health={provider_id: unhealthy},
        now=NOW,
    )
    assert failed.state == "adapter_unhealthy"


def test_public_readiness_exposes_credential_names_and_booleans_never_values():
    provider_id = "openphish-commercial"
    env = _configured_env(provider_id)
    readiness = provider_readiness(
        provider_id,
        env=env,
        health={provider_id: _healthy(provider_id)},
        now=NOW,
    )
    payload = readiness.public_dict()
    assert payload["credential_env_names"] == list(PROVIDERS[provider_id].credential_env_names)
    assert payload["credentials_configured"] is True
    assert payload["credential_values_exposed"] is False
    serialized = repr(payload)
    assert "configured-placeholder" not in serialized


def test_google_safe_browsing_cannot_be_enabled_for_commercial_runtime_by_env_flags():
    spec = provider_spec("google-safe-browsing")
    readiness = provider_readiness(
        spec.provider_id,
        env={spec.enable_env_name: "true"},
        health={spec.provider_id: _healthy(spec.provider_id)},
        now=NOW,
    )
    assert readiness.state == "blocked_commercial_use"
    assert readiness.commercial_runtime_allowed is False
    assert readiness.credentials_configured is False
    assert readiness.ready is False


def test_unknown_provider_fails_closed():
    with pytest.raises(PermissionError, match="Unknown Aura Sec reputation provider"):
        provider_spec("browser-supplied-provider")
    with pytest.raises(PermissionError, match="Unknown Aura Sec reputation provider"):
        provider_readiness("browser-supplied-provider", env={}, now=NOW)


def test_one_ready_provider_activates_cloud_reputation_but_not_corroboration_or_extension():
    provider_id = "google-web-risk"
    status = cloud_reputation_readiness(
        env=_configured_env(provider_id),
        health={provider_id: _healthy(provider_id)},
        now=NOW,
    )
    assert status["cloud_reputation_active"] is True
    assert status["ready_provider_ids"] == [provider_id]
    assert status["independent_corroboration_ready"] is False
    assert status["automatic_browser_extension_protection_released"] is False
    assert status["credential_values_exposed"] is False


def test_two_independent_ready_providers_report_corroboration_readiness_only():
    first = "google-web-risk"
    second = "openphish-commercial"
    env = {**_configured_env(first), **_configured_env(second)}
    status = cloud_reputation_readiness(
        env=env,
        health={first: _healthy(first), second: _healthy(second)},
        now=NOW,
    )
    assert status["cloud_reputation_active"] is True
    assert status["independent_corroboration_ready"] is True
    assert status["provider_count_ready"] == 2
    # Provider readiness never makes the not-yet-released Browser Guard extension real.
    assert status["automatic_browser_extension_protection_released"] is False


def test_registry_has_no_runtime_credentials_for_commercially_prohibited_safe_browsing():
    spec = PROVIDERS["google-safe-browsing"]
    assert spec.commercial_status == "commercial_use_prohibited"
    assert spec.credential_env_names == ()
    assert spec.commercial_approval_env_name is None
    assert spec.network_mode == "blocked_from_commercial_runtime"
