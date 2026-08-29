from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aura_music_studio.aura_sec_reputation_providers import ProviderHealthEvidence
from aura_music_studio.aura_sec_reputation_status import browser_guard_cloud_status


NOW = datetime(2026, 8, 29, 7, 30, tzinfo=timezone.utc)


def _health(provider_id: str, *, age_minutes: int = 0) -> ProviderHealthEvidence:
    return ProviderHealthEvidence(
        provider_id=provider_id,
        checked_at=NOW - timedelta(minutes=age_minutes),
        service_responding=True,
        credential_verified=True,
        transport_verified=True,
    )


def _google_env() -> dict[str, str]:
    return {
        "AURA_SEC_GOOGLE_WEB_RISK_ENABLED": "true",
        "AURA_SEC_GOOGLE_WEB_RISK_COMMERCIAL_APPROVED": "true",
        "AURA_SEC_GOOGLE_WEB_RISK_CREDENTIAL": "GOOGLE-SECRET-DO-NOT-LEAK",
    }


def _openphish_env() -> dict[str, str]:
    return {
        "AURA_SEC_OPENPHISH_COMMERCIAL_ENABLED": "true",
        "AURA_SEC_OPENPHISH_COMMERCIAL_APPROVED": "true",
        "AURA_SEC_OPENPHISH_COMMERCIAL_CREDENTIAL": "OPENPHISH-SECRET-DO-NOT-LEAK",
    }


def test_default_status_is_local_only_and_truthful():
    status = browser_guard_cloud_status(env={}, health={}, now=NOW)
    assert status["manual_link_inspection"] is True
    assert status["server_fetches_submitted_url"] is False
    assert status["cloud_reputation_state"] == "inactive"
    assert status["cloud_reputation_active"] is False
    assert status["provider_count_ready"] == 0
    assert status["automatic_browser_extension_protection_released"] is False
    assert status["signed_download_available"] is False
    assert status["provider_execution_available_from_status_view"] is False


def test_one_ready_provider_reports_single_provider_without_corroboration():
    status = browser_guard_cloud_status(
        env=_google_env(),
        health={"google-web-risk": _health("google-web-risk")},
        now=NOW,
    )
    assert status["cloud_reputation_state"] == "single_provider_ready"
    assert status["cloud_reputation_active"] is True
    assert status["independent_corroboration_ready"] is False
    assert status["ready_provider_ids"] == ["google-web-risk"]
    assert status["provider_count_ready"] == 1
    assert "not automatically treated as corroborated block authority" in status["truth"]


def test_two_ready_independent_providers_report_corroboration_readiness():
    status = browser_guard_cloud_status(
        env={**_google_env(), **_openphish_env()},
        health={
            "google-web-risk": _health("google-web-risk"),
            "openphish-commercial": _health("openphish-commercial"),
        },
        now=NOW,
    )
    assert status["cloud_reputation_state"] == "corroboration_ready"
    assert status["cloud_reputation_active"] is True
    assert status["independent_corroboration_ready"] is True
    assert status["provider_count_ready"] == 2
    assert set(status["ready_provider_ids"]) == {"google-web-risk", "openphish-commercial"}


def test_stale_health_removes_provider_from_ready_state():
    status = browser_guard_cloud_status(
        env=_google_env(),
        health={"google-web-risk": _health("google-web-risk", age_minutes=16)},
        now=NOW,
    )
    assert status["cloud_reputation_active"] is False
    assert status["provider_count_ready"] == 0
    provider = next(item for item in status["providers"] if item["provider_id"] == "google-web-risk")
    assert provider["state"] == "adapter_unhealthy"
    assert provider["ready"] is False


def test_commercially_blocked_safe_browsing_never_becomes_ready():
    status = browser_guard_cloud_status(
        env={"AURA_SEC_GOOGLE_SAFE_BROWSING_ENABLED": "true"},
        health={"google-safe-browsing": _health("google-safe-browsing")},
        now=NOW,
    )
    provider = next(item for item in status["providers"] if item["provider_id"] == "google-safe-browsing")
    assert provider["state"] == "blocked_commercial_use"
    assert provider["commercial_runtime_allowed"] is False
    assert provider["ready"] is False
    assert "google-safe-browsing" not in status["ready_provider_ids"]


def test_member_projection_does_not_expose_credential_names_values_or_raw_responses():
    google_secret = "GOOGLE-SECRET-DO-NOT-LEAK"
    openphish_secret = "OPENPHISH-SECRET-DO-NOT-LEAK"
    status = browser_guard_cloud_status(
        env={**_google_env(), **_openphish_env()},
        health={
            "google-web-risk": _health("google-web-risk"),
            "openphish-commercial": _health("openphish-commercial"),
        },
        now=NOW,
    )
    rendered = repr(status)
    assert google_secret not in rendered
    assert openphish_secret not in rendered
    assert "AURA_SEC_GOOGLE_WEB_RISK_CREDENTIAL" not in rendered
    assert "AURA_SEC_OPENPHISH_COMMERCIAL_CREDENTIAL" not in rendered
    assert status["credential_names_exposed"] is False
    assert status["credential_values_exposed"] is False
    assert status["raw_provider_responses_exposed"] is False
    for provider in status["providers"]:
        assert provider["credential_names_exposed"] is False
        assert provider["credential_values_exposed"] is False
        assert provider["raw_provider_response_exposed"] is False


def test_state_counts_explain_why_providers_are_not_ready_without_leaking_config_values():
    status = browser_guard_cloud_status(env={}, health={}, now=NOW)
    assert status["provider_state_counts"]["blocked_commercial_use"] == 1
    assert status["provider_state_counts"]["disabled"] >= 1
    assert sum(status["provider_state_counts"].values()) == status["provider_count_known"]
