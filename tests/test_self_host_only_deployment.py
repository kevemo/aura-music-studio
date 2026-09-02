from __future__ import annotations

import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_release_surface_is_self_host_only_without_vercel_runtime():
    assert not (ROOT / "vercel.json").exists()
    assert not (ROOT / "vercel_bootstrap.py").exists()
    assert not (ROOT / "docs" / "VERCEL_DEPLOYMENT.md").exists()

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "vercel" not in config.get("tool", {})


def test_required_self_host_release_assets_exist():
    required = [
        ROOT / "Dockerfile",
        ROOT / "docker-compose.yml",
        ROOT / "docker-compose.gpu.yml",
        ROOT / "deploy" / "Caddyfile",
        ROOT / "deploy" / "production" / "docker-compose.production.yml",
        ROOT / "deploy" / "production" / "production.env.example",
        ROOT / "deploy" / "selfhost" / "README.md",
        ROOT / "deploy" / "selfhost" / "build-release.sh",
        ROOT / "deploy" / "selfhost" / "run-production.sh",
        ROOT / "deploy" / "selfhost" / "compose.release.yml",
        ROOT / "deploy" / "selfhost" / "compose.aura-inference.yml",
    ]
    missing = [path.relative_to(ROOT).as_posix() for path in required if not path.exists()]
    assert missing == []


def test_production_compose_requires_real_renderer_secret_and_fail_closed_mode():
    production = (ROOT / "deploy" / "production" / "docker-compose.production.yml").read_text(encoding="utf-8")
    assert "AURA_DEPLOYMENT_ENV: production" in production
    assert 'AURA_REQUIRE_LIVE_RENDERER: "true"' in production
    assert 'AURA_MONITORING_ENABLED: "true"' in production
    assert 'LSS_COOKIE_SECURE: "true"' in production
    assert 'AURA_WEB_ALLOW_HTTP: "false"' in production
    assert "${ACESTEP_API_KEY:?ACESTEP_API_KEY is required in production}" in production


def test_public_ingress_is_caddy_and_fastapi_is_loopback_bound():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:8000:8000"' in compose
    assert 'profiles: ["public"]' in compose
    assert '"80:80"' in compose
    assert '"443:443"' in compose
    assert "./deploy/Caddyfile:/etc/caddy/Caddyfile:ro" in compose


def test_production_environment_template_matches_launcher_required_contract():
    env_text = (ROOT / "deploy" / "production" / "production.env.example").read_text(encoding="utf-8")
    required = {
        "LSS_PUBLIC_BASE_URL",
        "LSS_PUBLIC_SITE_ADDRESS",
        "LSS_ADMIN_KEY",
        "LSS_PROVENANCE_SECRET",
        "ACESTEP_API_KEY",
        "AURA_MONITORING_TOKEN",
        "LSS_BACKUP_AGE_RECIPIENT",
        "COSIGN_VERIFY_KEY",
        "AURA_SELFHOST_LLM_MODEL_DIR",
        "AURA_LLM_INTERNAL_API_KEY",
        "AURA_ACESTEP_CUDA_VISIBLE_DEVICES",
        "AURA_LLM_CUDA_VISIBLE_DEVICES",
    }
    defined = {
        line.split("=", 1)[0].strip()
        for line in env_text.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }
    assert required <= defined


def test_release_manifest_and_compose_pin_command_center_and_ace_step_images():
    manifest = json.loads(
        (ROOT / "deploy" / "selfhost" / "release-manifest.example.json").read_text(encoding="utf-8")
    )
    assert manifest["approved"] is False
    assert "@sha256:" in manifest["command_center_image"]
    assert "@sha256:" in manifest["ace_step_image"]
    assert len(manifest["ace_step_upstream_commit"]) == 40

    release_compose = (ROOT / "deploy" / "selfhost" / "compose.release.yml").read_text(encoding="utf-8")
    assert "ESP_COMMAND_CENTER_IMAGE" in release_compose
    assert "AURA_ACESTEP_IMAGE" in release_compose
    assert "pull_policy: always" in release_compose


def test_production_launcher_activates_public_ingress_and_verifies_every_runtime_image():
    launcher = (ROOT / "deploy" / "selfhost" / "run-production.sh").read_text(encoding="utf-8")
    assert '--profile public' in launcher
    assert 'AURA_SOCIAL_PUBLISH_WORKER_ENABLED' in launcher
    assert 'export AURA_ACESTEP_IMAGE=' in launcher
    assert 'component=ace-step' in launcher
    assert 'trivy image --exit-code 1 --severity HIGH,CRITICAL --scanners vuln "$AURA_ACESTEP_IMAGE"' in launcher
    assert 'PULL_SERVICES+=' in launcher
    assert 'ace-step aura-llm searxng caddy' in launcher
    assert 'up -d --no-build --remove-orphans' in launcher


def test_self_host_runbook_requires_env_release_and_inference_manifests():
    runbook = (ROOT / "deploy" / "selfhost" / "README.md").read_text(encoding="utf-8")
    assert "--env /secure/path/production.env" in runbook
    assert "--release /secure/path/release.json" in runbook
    assert "--inference /secure/path/aura-inference.json" in runbook
