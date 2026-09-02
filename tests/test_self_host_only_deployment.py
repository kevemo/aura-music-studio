from __future__ import annotations

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
