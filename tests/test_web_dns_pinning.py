from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

import aura_music_studio.web_access as web_access
import aura_music_studio.web_transport as web_transport
from aura_music_studio.web_access import AuraWebGateway


class _RawResponse:
    status = 200
    headers = {"Content-Type": "text/plain"}

    def close(self):
        return None


def _response(url: str, status: int = 200, **headers) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.url = url
    response.headers.update(headers)
    return response


def test_pinned_https_connects_to_validated_ip_but_keeps_original_tls_and_host(monkeypatch):
    captured = {}

    class FakeHTTPSConnection:
        def __init__(self, host, port, **kwargs):
            captured["connect_host"] = host
            captured["connect_port"] = port
            captured["connection_kwargs"] = kwargs

        def request(self, method, target, **kwargs):
            captured["method"] = method
            captured["target"] = target
            captured["request_kwargs"] = kwargs

        def getresponse(self):
            return _RawResponse()

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(web_transport, "HTTPSConnection", FakeHTTPSConnection)
    monkeypatch.setattr(web_transport.ssl, "create_default_context", lambda **_kwargs: object())

    response = web_transport.pinned_get(
        "https://example.com/research?q=aura",
        addresses=("93.184.216.34",),
        headers={"User-Agent": "test"},
        timeout=5,
    )

    assert response.status_code == 200
    assert captured["connect_host"] == "93.184.216.34"
    assert captured["connect_port"] == 443
    assert captured["connection_kwargs"]["server_hostname"] == "example.com"
    assert captured["connection_kwargs"]["assert_hostname"] == "example.com"
    assert captured["method"] == "GET"
    assert captured["target"] == "/research?q=aura"
    assert captured["request_kwargs"]["headers"]["Host"] == "example.com"
    assert "shell" not in captured["request_kwargs"]


def test_gateway_passes_exact_validated_address_set_to_direct_transport(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_WEB_ENABLED", "true")
    monkeypatch.setenv("AURA_WEB_ALLOW_HTTP", "false")
    monkeypatch.delenv("AURA_WEB_EGRESS_PROXY", raising=False)
    monkeypatch.delenv("AURA_WEB_TRUST_EGRESS_PROXY_DNS", raising=False)
    gateway = AuraWebGateway(cache_dir=tmp_path)
    resolutions = []
    captured = {}

    def fake_resolve(host: str, port: int):
        resolutions.append((host, port))
        return ("93.184.216.34", "1.1.1.1")

    def fake_pinned_get(url, *, addresses, headers, timeout):
        captured.update(url=url, addresses=addresses, headers=headers, timeout=timeout)
        return _response(url)

    monkeypatch.setattr(gateway, "_resolve_public_addresses", fake_resolve)
    monkeypatch.setattr(web_access, "pinned_get", fake_pinned_get)

    response = gateway._request_with_safe_redirects("https://example.com/path")

    assert response.status_code == 200
    assert resolutions == [("example.com", 443)]
    assert captured["addresses"] == ("93.184.216.34", "1.1.1.1")
    assert captured["url"] == "https://example.com/path"


def test_mixed_public_and_private_dns_answer_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_WEB_ENABLED", "true")
    gateway = AuraWebGateway(cache_dir=tmp_path)

    monkeypatch.setattr(
        web_access.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ],
    )

    with pytest.raises(PermissionError, match="private/local"):
        gateway._validate_url("https://example.com/private")


def test_embedded_url_credentials_are_rejected_before_dns(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_WEB_ENABLED", "true")
    gateway = AuraWebGateway(cache_dir=tmp_path)
    called = False

    def should_not_resolve(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("DNS must not be reached")

    monkeypatch.setattr(web_access.socket, "getaddrinfo", should_not_resolve)
    with pytest.raises(ValueError, match="embedded credentials"):
        gateway._validate_url("https://user:password@example.com/private")
    assert called is False


def test_explicit_proxy_fails_closed_until_dns_enforcement_is_delegated(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_WEB_ENABLED", "true")
    monkeypatch.setenv("AURA_WEB_EGRESS_PROXY", "http://proxy.internal:3128")
    monkeypatch.setenv("AURA_WEB_TRUST_EGRESS_PROXY_DNS", "false")
    gateway = AuraWebGateway(cache_dir=tmp_path)

    with pytest.raises(PermissionError, match="target-DNS enforcement"):
        gateway._public_get("https://example.com/", ("93.184.216.34",))

    diagnostics = gateway.diagnostics()
    assert diagnostics["dns_rebinding_protection"] == "proxy_untrusted_fail_closed"
    assert diagnostics["explicit_egress_proxy_configured"] is True
    assert diagnostics["explicit_egress_proxy_dns_trust_enabled"] is False


def test_explicit_proxy_mode_is_opt_in_and_ambient_proxy_environment_is_not_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_WEB_ENABLED", "true")
    monkeypatch.setenv("HTTP_PROXY", "http://ambient.invalid:9999")
    monkeypatch.setenv("HTTPS_PROXY", "http://ambient.invalid:9999")
    monkeypatch.delenv("AURA_WEB_EGRESS_PROXY", raising=False)
    gateway = AuraWebGateway(cache_dir=tmp_path)

    diagnostics = gateway.diagnostics()
    assert diagnostics["ambient_proxy_environment_ignored"] is True
    assert diagnostics["explicit_egress_proxy_configured"] is False
    assert diagnostics["dns_rebinding_protection"] == "direct_validated_ip_pinning"
    assert web_transport.no_env_session().trust_env is False


def test_trusted_explicit_proxy_uses_only_configured_proxy(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_WEB_ENABLED", "true")
    monkeypatch.setenv("AURA_WEB_EGRESS_PROXY", "https://proxy.example:8443")
    monkeypatch.setenv("AURA_WEB_TRUST_EGRESS_PROXY_DNS", "true")
    gateway = AuraWebGateway(cache_dir=tmp_path)
    captured = {}

    def fake_proxy_get(url, *, proxy_url, headers, timeout):
        captured.update(url=url, proxy_url=proxy_url, headers=headers, timeout=timeout)
        return _response(url)

    monkeypatch.setattr(web_access, "explicit_proxy_get", fake_proxy_get)
    response = gateway._public_get("https://example.com/a", ("93.184.216.34",))

    assert response.status_code == 200
    assert captured["proxy_url"] == "https://proxy.example:8443"
    assert gateway.diagnostics()["dns_rebinding_protection"] == "delegated_to_explicit_egress_proxy"


@pytest.mark.parametrize(
    "value",
    [
        "socks5://proxy.example:1080",
        "https://proxy.example/path",
        "https://proxy.example?q=1",
        "https://proxy.example#fragment",
    ],
)
def test_proxy_configuration_rejects_unsupported_or_ambiguous_urls(value: str):
    with pytest.raises(ValueError, match="AURA_WEB_EGRESS_PROXY"):
        web_transport.validate_proxy_url(value)
