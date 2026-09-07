from __future__ import annotations

from pathlib import Path

from aura_music_studio.production_readiness import build_readiness_report


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_COMPOSE = ROOT / "deploy" / "production" / "docker-compose.production.yml"
STAGING_COMPOSE = ROOT / "deploy" / "staging" / "docker-compose.staging.yml"
PRODUCTION_ENV = ROOT / "deploy" / "production" / "production.env.example"
STAGING_ENV = ROOT / "deploy" / "staging" / "staging.env.example"


def test_production_overlay_is_readiness_gated_and_requires_private_gpu_key():
    text = PRODUCTION_COMPOSE.read_text(encoding="utf-8")
    assert "name: aura-command-center-production" in text
    assert "AURA_DEPLOYMENT_ENV: production" in text
    assert "/health/ready" in text
    assert "AURA_GPU_REQUIRED: \"true\"" in text
    assert "AURA_REQUIRE_LIVE_RENDERER: \"true\"" in text
    assert "ACESTEP_API_KEY: ${ACESTEP_API_KEY:?" in text
    assert "no-new-privileges:true" in text
    assert "cap_drop:" in text
    assert "max-size:" in text and "max-file:" in text


def test_staging_overlay_is_a_separate_sandbox_identity_and_social_publish_is_off():
    text = STAGING_COMPOSE.read_text(encoding="utf-8")
    assert "name: aura-command-center-staging" in text
    assert "AURA_DEPLOYMENT_ENV: staging" in text
    assert "LSS_PAYPAL_ENVIRONMENT: sandbox" in text
    assert "LSS_PAYPAL_ENVIRONMENT: live" not in text
    assert "AURA_SOCIAL_PUBLISH_WORKER_ENABLED: \"false\"" in text
    assert "/health/ready" in text
    # Optional GPU staging must compose without defining a half-configured ace-step service.
    assert "\n  ace-step:" not in text


def test_deployment_examples_never_embed_provider_or_owner_secret_values():
    production = PRODUCTION_ENV.read_text(encoding="utf-8")
    staging = STAGING_ENV.read_text(encoding="utf-8")
    secret_names = (
        "LSS_ADMIN_KEY",
        "LSS_PROVENANCE_SECRET",
        "LSS_PAYPAL_CLIENT_ID",
        "LSS_PAYPAL_CLIENT_SECRET",
        "LSS_PAYPAL_WEBHOOK_ID",
        "ELEVENLABS_API_KEY",
        "MUREKA_API_KEY",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "AURA_CONNECTOR_MASTER_KEY",
        "ACESTEP_API_KEY",
        "AURA_MONITORING_TOKEN",
        "LSS_BACKUP_AGE_RECIPIENT",
    )
    for text in (production, staging):
        for name in secret_names:
            if f"{name}=" in text:
                line = next(row for row in text.splitlines() if row.startswith(f"{name}="))
                assert line == f"{name}="


def test_production_and_staging_use_different_compose_project_names():
    production = PRODUCTION_COMPOSE.read_text(encoding="utf-8")
    staging = STAGING_COMPOSE.read_text(encoding="utf-8")
    assert "name: aura-command-center-production" in production
    assert "name: aura-command-center-staging" in staging
    assert production.splitlines()[0] != staging.splitlines()[0]


def test_verified_staging_paypal_fails_closed_when_sandbox_credentials_are_missing():
    report = build_readiness_report(
        {
            "AURA_DEPLOYMENT_ENV": "staging",
            "LSS_PAYMENT_PROVIDER": "paypal",
            "LSS_PAYMENT_MODE": "verified_paypal_invoice",
            "LSS_PAYPAL_ENVIRONMENT": "sandbox",
            "LSS_DB_PATH": "/staging/data.sqlite3",
            "AURA_PROJECTS_ROOT": "/staging/projects",
            "LSS_BACKUP_DIR": "/staging/backups",
        }
    )
    assert report["ok"] is False
    assert "payments" in report["blocking_categories"]
    assert report["categories"]["payments"]["details"]["verification_credentials_configured"] is False
