from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_uvicorn_does_not_apply_forwarded_headers_before_application_authentication():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert '"--no-proxy-headers"' in dockerfile
    assert '"--proxy-headers"' not in dockerfile


def test_caddy_overwrites_internal_proxy_auth_header_before_upstream_routing():
    caddy = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    assert "header_up X-ESP-Proxy-Auth" in caddy
    assert "{$LSS_TRUSTED_PROXY_TOKEN:__proxy_token_missing__}" in caddy
    assert "health_uri /health/ready" in caddy


def test_production_compose_requires_same_proxy_secret_for_app_and_caddy():
    compose = (ROOT / "deploy" / "production" / "docker-compose.production.yml").read_text(encoding="utf-8")
    required = "${LSS_TRUSTED_PROXY_TOKEN:?LSS_TRUSTED_PROXY_TOKEN is required in production}"
    assert compose.count(required) == 2


def test_production_env_template_never_invents_proxy_secret():
    template = (ROOT / "deploy" / "production" / "production.env.example").read_text(encoding="utf-8")
    assert "LSS_TRUSTED_PROXY_TOKEN=\n" in template
    assert "Generate at least 32 random bytes" in template
    assert "LSS_AUTH_RATE_LIMIT=12" in template
    assert "LSS_AUTH_RATE_WINDOW_SECONDS=900" in template
