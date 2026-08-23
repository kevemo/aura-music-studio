from __future__ import annotations

import json

import aura_music_studio.public_address as pa
from aura_music_studio.public_address import PublicAddressManager


def test_duckdns_hostname_is_derived_without_exposing_token(tmp_path, monkeypatch):
    monkeypatch.setenv("LSS_DDNS_PROVIDER", "duckdns")
    monkeypatch.setenv("LSS_DUCKDNS_SUBDOMAIN", "esp-live-sound-studio")
    monkeypatch.setenv("LSS_DUCKDNS_TOKEN", "top-secret-token")
    seen = {}

    def fake_http(url: str, **kwargs):
        seen["url"] = url
        return "OK\n8.8.8.8\nUPDATED"

    monkeypatch.setattr(pa, "_http_text", fake_http)
    manager = PublicAddressManager(tmp_path / "status.json")
    ok, message = manager.update_ddns("8.8.8.8")
    assert ok is True
    assert manager.hostname == "esp-live-sound-studio.duckdns.org"
    assert "top-secret-token" in seen["url"]
    assert "top-secret-token" not in message


def test_freedns_rejects_non_official_update_host(tmp_path, monkeypatch):
    monkeypatch.setenv("LSS_DDNS_PROVIDER", "freedns")
    monkeypatch.setenv("LSS_FREEDNS_UPDATE_URL", "https://example.com/steal-token")
    manager = PublicAddressManager(tmp_path / "status.json")
    ok, message = manager.update_ddns("8.8.8.8")
    assert ok is False
    assert "official HTTPS afraid.org" in message


def test_direct_mode_reports_public_ip_without_ddns(tmp_path, monkeypatch):
    monkeypatch.setenv("LSS_DDNS_PROVIDER", "direct")
    monkeypatch.setenv("LSS_UPNP_DISCOVERY", "true")
    monkeypatch.setenv("LSS_UPNP_PORT_FORWARD", "false")
    monkeypatch.setattr(pa, "_local_ipv4", lambda: "192.168.1.22")
    monkeypatch.setattr(pa, "_upnp_external_ipv4", lambda: ("8.8.8.8", None))
    monkeypatch.setattr(pa, "_global_ipv6_candidates", lambda: [])
    manager = PublicAddressManager(tmp_path / "status.json")
    status = manager.check(update_ddns=False)
    assert status.public_ipv4 == "8.8.8.8"
    assert status.recommended_url == "http://8.8.8.8"
    assert status.likely_cgnat is False
    assert status.diagnostics["cloud_required"] is False


def test_cgnat_signal_detects_router_address_behind_upstream_nat(tmp_path, monkeypatch):
    monkeypatch.setenv("LSS_DDNS_PROVIDER", "direct")
    monkeypatch.setenv("LSS_UPNP_DISCOVERY", "true")
    monkeypatch.setenv("LSS_PUBLIC_IP_DISCOVERY", "true")
    monkeypatch.setenv("LSS_UPNP_PORT_FORWARD", "false")
    monkeypatch.setattr(pa, "_local_ipv4", lambda: "192.168.1.22")
    monkeypatch.setattr(pa, "_upnp_external_ipv4", lambda: ("100.64.12.4", None))
    monkeypatch.setattr(pa, "_http_public_ipv4", lambda: ("8.8.8.8", None))
    monkeypatch.setattr(pa, "_global_ipv6_candidates", lambda: [])
    manager = PublicAddressManager(tmp_path / "status.json")
    status = manager.check(update_ddns=False)
    assert status.likely_cgnat is True
    assert any("CGNAT" in warning for warning in status.warnings)


def test_status_file_contains_no_ddns_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("LSS_DDNS_PROVIDER", "duckdns")
    monkeypatch.setenv("LSS_DUCKDNS_SUBDOMAIN", "esp")
    monkeypatch.setenv("LSS_DUCKDNS_TOKEN", "never-write-this")
    monkeypatch.setattr(pa, "_local_ipv4", lambda: "192.168.1.2")
    monkeypatch.setattr(pa, "_upnp_external_ipv4", lambda: ("8.8.8.8", None))
    monkeypatch.setattr(pa, "_global_ipv6_candidates", lambda: [])
    monkeypatch.setattr(pa, "_resolved_addresses", lambda hostname: ["8.8.8.8"])
    monkeypatch.setattr(PublicAddressManager, "update_ddns", lambda self, ipv4, ipv6=None: (True, "accepted"))
    manager = PublicAddressManager(tmp_path / "status.json")
    manager.check(update_ddns=True)
    raw = (tmp_path / "status.json").read_text(encoding="utf-8")
    assert "never-write-this" not in raw
    parsed = json.loads(raw)
    assert parsed["diagnostics"]["ddns_secret_exposed"] is False
