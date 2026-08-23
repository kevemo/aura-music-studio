from __future__ import annotations

import json

from aura_music_studio import discovery
from aura_music_studio.doctor import _public_address_status


def test_robots_blocks_private_studio_routes(monkeypatch):
    monkeypatch.setattr(discovery, "_base", lambda: "https://studio.example/")
    response = discovery.robots()
    text = response.body.decode("utf-8")
    for private in ("/owner", "/dashboard", "/studio", "/production-suite", "/projects/", "/membership/"):
        assert f"Disallow: {private}" in text
    assert "Sitemap: https://studio.example/sitemap.xml" in text


def test_sitemap_contains_only_declared_public_pages(monkeypatch):
    monkeypatch.setattr(discovery, "_base", lambda: "https://studio.example/")
    response = discovery.sitemap()
    text = response.body.decode("utf-8")
    assert "https://studio.example/ai-song-generator" in text
    assert "https://studio.example/backing-track-maker" in text
    assert "/owner" not in text
    assert "/dashboard" not in text
    assert "/projects/" not in text


def test_service_worker_never_caches_private_routes():
    response = discovery.service_worker()
    script = response.body.decode("utf-8")
    for private in ("/studio", "/dashboard", "/owner", "/projects", "/membership", "/production-suite"):
        assert private not in script
    assert "/brand/esp-logo.webp" in script


def test_manifest_uses_esp_brand_asset():
    response = discovery.manifest()
    payload = json.loads(response.body)
    assert payload["short_name"] == "ESP Live Sound Studio"
    assert payload["display"] == "standalone"
    assert payload["icons"][0]["src"] == "/brand/esp-logo.webp"


def test_member_safe_doctor_redacts_network_topology(tmp_path, monkeypatch):
    status_file = tmp_path / "status.json"
    status_file.write_text(json.dumps({
        "checked_at": "2026-08-23T06:00:00+00:00",
        "provider": "duckdns",
        "hostname": "esp.duckdns.org",
        "recommended_url": "https://esp.duckdns.org",
        "lan_ipv4": "192.168.1.10",
        "router_external_ipv4": "100.64.1.5",
        "public_ipv4": "8.8.8.8",
        "ddns_updated": True,
        "likely_cgnat": True,
        "caddy_https_ready": False,
        "warnings": ["CGNAT warning"],
    }), encoding="utf-8")
    monkeypatch.setenv("LSS_PUBLIC_ADDRESS_STATUS", str(status_file))
    report = _public_address_status()
    dumped = json.dumps(report)
    assert "192.168.1.10" not in dumped
    assert "100.64.1.5" not in dumped
    assert "8.8.8.8" not in dumped
    assert report["internal_network_addresses_exposed"] is False
