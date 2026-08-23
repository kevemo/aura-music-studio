from __future__ import annotations

import json
from pathlib import Path

from aura_music_studio import mailer
from aura_music_studio.self_host_setup import initialize_self_host


def _template(path: Path):
    path.write_text(
        "\n".join([
            "LSS_PUBLIC_BASE_URL=http://127.0.0.1:8000",
            "LSS_COOKIE_SECURE=false",
            "LSS_DDNS_PROVIDER=none",
            "LSS_PUBLIC_HOST=",
            "LSS_PUBLIC_SITE_ADDRESS=http://:80",
            "LSS_ADMIN_KEY=",
            "LSS_PROVENANCE_SECRET=",
            "LSS_DUCKDNS_SUBDOMAIN=",
            "LSS_DUCKDNS_TOKEN=",
            "LSS_FREEDNS_UPDATE_URL=",
            "LSS_SMTP_USERNAME=",
            "LSS_SMTP_PASSWORD=",
        ]) + "\n",
        encoding="utf-8",
    )


def test_self_host_init_generates_secrets_and_direct_mode(tmp_path):
    template = tmp_path / ".env.example"
    env = tmp_path / ".env"
    _template(template)
    result = initialize_self_host(provider="direct", env_path=env, template_path=template)
    text = env.read_text(encoding="utf-8")
    assert "LSS_DDNS_PROVIDER=direct" in text
    assert "LSS_PUBLIC_BASE_URL=auto" in text
    assert "LSS_PUBLIC_SITE_ADDRESS=http://:80" in text
    assert result.admin_key_generated is True
    assert result.admin_key
    assert "LSS_ADMIN_KEY=\n" not in text
    assert "LSS_PROVENANCE_SECRET=\n" not in text


def test_initializer_preserves_existing_owner_secret(tmp_path):
    env = tmp_path / ".env"
    template = tmp_path / ".env.example"
    _template(template)
    env.write_text(template.read_text(encoding="utf-8").replace("LSS_ADMIN_KEY=", "LSS_ADMIN_KEY=existing-owner-secret"), encoding="utf-8")
    result = initialize_self_host(provider="direct", env_path=env, template_path=template)
    assert result.admin_key_generated is False
    assert result.admin_key is None
    assert "LSS_ADMIN_KEY=existing-owner-secret" in env.read_text(encoding="utf-8")


def test_duckdns_init_enables_secure_cookie_and_never_requires_token_on_cli(tmp_path):
    template = tmp_path / ".env.example"
    env = tmp_path / ".env"
    _template(template)
    result = initialize_self_host(
        provider="duckdns",
        duckdns_subdomain="esp-live-sound-studio",
        env_path=env,
        template_path=template,
    )
    text = env.read_text(encoding="utf-8")
    assert result.hostname == "esp-live-sound-studio.duckdns.org"
    assert result.secure_cookie is True
    assert "LSS_COOKIE_SECURE=true" in text
    assert "LSS_DUCKDNS_TOKEN=" in text
    assert "LSS_DUCKDNS_TOKEN" in result.missing_private_settings


def test_mailer_auto_url_uses_aura_public_address_status(tmp_path, monkeypatch):
    status = tmp_path / "public_address_status.json"
    status.write_text(json.dumps({"recommended_url": "https://esp-live.example"}), encoding="utf-8")
    monkeypatch.setenv("LSS_PUBLIC_BASE_URL", "auto")
    monkeypatch.setenv("LSS_PUBLIC_ADDRESS_STATUS", str(status))
    assert mailer._public_url() == "https://esp-live.example"
