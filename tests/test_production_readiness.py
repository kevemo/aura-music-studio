from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.production_readiness import build_readiness_report, router


def _production_env() -> dict[str, str]:
    return {
        "AURA_DEPLOYMENT_ENV": "production",
        "LSS_PAYMENT_PROVIDER": "paypal",
        "LSS_PAYMENT_MODE": "verified_paypal_webhook",
        "LSS_PAYPAL_ENVIRONMENT": "live",
        "LSS_PAYPAL_CLIENT_ID": "paypal-client-0123456789",
        "LSS_PAYPAL_CLIENT_SECRET": "paypal-secret-0123456789",
        "LSS_PAYPAL_WEBHOOK_ID": "paypal-webhook-0123456789",
        "AURA_PRODUCTION_REQUIRED_PROVIDER_SECRETS": "ELEVENLABS_API_KEY,MUREKA_API_KEY",
        "ELEVENLABS_API_KEY": "eleven-provider-0123456789",
        "MUREKA_API_KEY": "mureka-provider-0123456789",
        "AURA_GPU_REQUIRED": "true",
        "AURA_REQUIRE_LIVE_RENDERER": "true",
        "AURA_ACESTEP_API_URL": "http://ace-step:8001",
        "ACESTEP_API_KEY": "ace-step-private-0123456789",
        "AURA_GPU_MIN_VRAM_GB": "12",
        "AURA_MONITORING_ENABLED": "true",
        "AURA_MONITORING_TOKEN": "monitoring-private-0123456789",
        "LSS_AUTO_BACKUP_ENABLED": "true",
        "LSS_AUTO_BACKUP_INTERVAL_HOURS": "24",
        "LSS_AUTO_BACKUP_KEEP": "7",
        "LSS_BACKUP_AGE_RECIPIENT": "age1exampleproductionrecipient",
        "LSS_PUBLIC_BASE_URL": "https://studio.example.test",
        "LSS_COOKIE_SECURE": "true",
        "LSS_PROVENANCE_SECRET": "provenance-private-0123456789",
        "LSS_ADMIN_KEY": "owner-private-0123456789",
        "AURA_WEB_ALLOW_HTTP": "false",
        "LSS_DB_PATH": "/srv/aura/data/studio.sqlite3",
        "AURA_PROJECTS_ROOT": "/srv/aura/projects",
        "LSS_BACKUP_DIR": "/srv/aura/backups",
    }


def _stripe_production_env() -> dict[str, str]:
    env = _production_env()
    env.update(
        {
            "LSS_PAYMENT_PROVIDER": "stripe",
            "STRIPE_SECRET_KEY": "sk_live_testfixture_0123456789",
            "STRIPE_WEBHOOK_SECRET": "whsec_testfixture_0123456789",
            "STRIPE_BASE_PRICE_ID": "price_tier2_testfixture_0123456789",
            "STRIPE_PRO_PRICE_ID": "price_pro_testfixture_0123456789",
            "STRIPE_PRO_ANNUAL_PRICE_ID": "price_pro_annual_testfixture_0123456789",
        }
    )
    return env


def test_complete_production_configuration_passes_but_does_not_fake_runtime_or_restore_proof():
    env = _production_env()
    report = build_readiness_report(env)
    assert report["ok"] is True
    assert report["configuration_ready"] is True
    assert report["production_ready"] is False
    assert report["blocking_categories"] == []
    assert report["runtime_probes_performed"] is False
    assert report["network_probes_performed"] is False
    assert set(report["release_blocking_categories"]) == {"runtime_dependencies", "restore_evidence"}
    assert report["categories"]["backups"]["details"]["configuration_is_restore_proof"] is False
    assert report["secret_values_exposed"] is False

    serialized = json.dumps(report)
    for name in (
        "LSS_PAYPAL_CLIENT_SECRET",
        "ELEVENLABS_API_KEY",
        "MUREKA_API_KEY",
        "ACESTEP_API_KEY",
        "AURA_MONITORING_TOKEN",
        "LSS_PROVENANCE_SECRET",
        "LSS_ADMIN_KEY",
    ):
        assert env[name] not in serialized


def test_complete_stripe_production_configuration_passes_without_exposing_secrets():
    env = _stripe_production_env()
    report = build_readiness_report(env)
    payment = report["categories"]["payments"]
    assert report["ok"] is True
    assert report["configuration_ready"] is True
    assert report["production_ready"] is False
    assert payment["ok"] is True
    assert payment["details"]["provider"] == "stripe"
    assert payment["details"]["mode"] == "signed_stripe_webhook"
    assert payment["details"]["stripe_environment"] == "live"
    assert payment["details"]["browser_return_is_payment_proof"] is False
    assert payment["details"]["verification_credentials_configured"] is True
    assert payment["details"]["subscription_price_ids_configured"] is True
    serialized = json.dumps(report)
    assert env["STRIPE_SECRET_KEY"] not in serialized
    assert env["STRIPE_WEBHOOK_SECRET"] not in serialized


def test_stripe_production_fails_closed_on_missing_or_test_credentials():
    env = _stripe_production_env()
    env["STRIPE_SECRET_KEY"] = "sk_test_testfixture_0123456789"
    env.pop("STRIPE_WEBHOOK_SECRET")
    report = build_readiness_report(env)
    payment = report["categories"]["payments"]
    assert report["ok"] is False
    assert "payments" in report["blocking_categories"]
    assert payment["ok"] is False
    assert "STRIPE_WEBHOOK_SECRET" in payment["details"]["missing_credential_names"]
    assert any("live secret key" in message for message in payment["messages"])


def test_staging_rejects_live_stripe_credentials():
    env = _stripe_production_env()
    env["AURA_DEPLOYMENT_ENV"] = "staging"
    report = build_readiness_report(env)
    assert report["ok"] is False
    assert "payments" in report["blocking_categories"]
    assert report["categories"]["deployment"]["details"]["staging_uses_live_stripe"] is True


def test_staging_accepts_stripe_test_credentials():
    env = _stripe_production_env()
    env["AURA_DEPLOYMENT_ENV"] = "staging"
    env["STRIPE_SECRET_KEY"] = "sk_test_testfixture_0123456789"
    env["LSS_PAYPAL_ENVIRONMENT"] = "live"
    report = build_readiness_report(env)
    assert report["categories"]["payments"]["ok"] is True
    assert report["categories"]["deployment"]["details"]["staging_uses_live_paypal"] is False


def test_unsupported_payment_provider_fails_closed():
    env = _production_env()
    env["LSS_PAYMENT_PROVIDER"] = "unknown-provider"
    report = build_readiness_report(env)
    assert report["ok"] is False
    assert "payments" in report["blocking_categories"]
    assert report["categories"]["payments"]["ok"] is False


def test_production_fails_closed_on_manual_payment_or_placeholder_secrets():
    env = _production_env()
    env["LSS_PAYMENT_MODE"] = "manual_invoice_link"
    env["ACESTEP_API_KEY"] = "replace-me-placeholder"
    env["AURA_MONITORING_TOKEN"] = "changeme-placeholder"
    report = build_readiness_report(env)
    assert report["ok"] is False
    assert {"payments", "gpu", "monitoring"}.issubset(set(report["blocking_categories"]))
    assert report["categories"]["payments"]["details"]["automatic_activation"] is False
    assert report["categories"]["payments"]["details"]["browser_return_is_payment_proof"] is False


def test_staging_rejects_live_paypal_environment():
    env = _production_env()
    env["AURA_DEPLOYMENT_ENV"] = "staging"
    env["LSS_PAYPAL_ENVIRONMENT"] = "live"
    report = build_readiness_report(env)
    assert report["ok"] is False
    assert "payments" in report["blocking_categories"]
    assert report["categories"]["deployment"]["details"]["staging_uses_live_paypal"] is True


def test_test_and_ci_environments_are_supported_and_default_to_paypal_sandbox():
    for name in ("local", "test", "ci", "integration"):
        report = build_readiness_report({"AURA_DEPLOYMENT_ENV": name})
        assert report["environment"] == name
        assert report["categories"]["deployment"]["ok"] is True
        assert report["categories"]["payments"]["details"]["paypal_environment"] == "sandbox"


def test_nonproduction_environment_blocks_live_payment_configuration():
    env = _stripe_production_env()
    env["AURA_DEPLOYMENT_ENV"] = "ci"
    report = build_readiness_report(env)
    assert report["ok"] is False
    assert report["categories"]["deployment"]["details"]["nonproduction_uses_live_stripe"] is True
    assert report["categories"]["payments"]["ok"] is False


def test_required_provider_secret_names_are_reported_but_values_never_are():
    env = _production_env()
    env["AURA_PRODUCTION_REQUIRED_PROVIDER_SECRETS"] = "ELEVENLABS_API_KEY,MISSING_PROVIDER_KEY"
    env.pop("MUREKA_API_KEY")
    report = build_readiness_report(env)
    provider = report["categories"]["provider_credentials"]
    assert provider["ok"] is False
    assert provider["details"]["missing_secret_names"] == ["MISSING_PROVIDER_KEY"]
    assert provider["details"]["secret_values_exposed"] is False
    assert env["ELEVENLABS_API_KEY"] not in json.dumps(report)


def _client(monkeypatch, env: dict[str, str]) -> TestClient:
    for key in list({**_production_env(), **_stripe_production_env()}):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_monitoring_disabled_is_service_unavailable(monkeypatch):
    env = _production_env()
    env["AURA_MONITORING_ENABLED"] = "false"
    client = _client(monkeypatch, env)
    response = client.get("/internal/metrics", headers={"X-Aura-Monitoring-Token": env["AURA_MONITORING_TOKEN"]})
    assert response.status_code == 503
    assert response.text == "monitoring unavailable\n"
    assert response.headers["cache-control"] == "no-store"


def test_monitoring_wrong_or_missing_token_is_forbidden(monkeypatch):
    env = _production_env()
    client = _client(monkeypatch, env)
    assert client.get("/internal/metrics").status_code == 403
    assert client.get("/internal/metrics", headers={"X-Aura-Monitoring-Token": "definitely-wrong-token"}).status_code == 403


def test_monitoring_correct_token_exposes_truthful_operational_metrics(monkeypatch):
    env = _production_env()
    client = _client(monkeypatch, env)
    response = client.get("/internal/metrics", headers={"X-Aura-Monitoring-Token": env["AURA_MONITORING_TOKEN"]})
    assert response.status_code == 200
    assert "aura_process_up 1" in response.text
    assert "aura_configuration_ready 1" in response.text
    assert "aura_serving_ready 0" in response.text
    assert "aura_restore_evidence_verified 0" in response.text
    assert "aura_production_ready 0" in response.text
    assert env["AURA_MONITORING_TOKEN"] not in response.text
    assert env["LSS_PAYPAL_CLIENT_SECRET"] not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_readiness_endpoint_returns_503_when_production_is_unsafe(monkeypatch):
    env = _production_env()
    env["LSS_PUBLIC_BASE_URL"] = "http://insecure.example.test"
    client = _client(monkeypatch, env)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert "security" in response.json()["blocking_categories"]


def test_liveness_is_independent_of_provider_configuration(monkeypatch):
    client = _client(monkeypatch, {"AURA_DEPLOYMENT_ENV": "production"})
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["kind"] == "liveness"
