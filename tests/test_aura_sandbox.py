from __future__ import annotations

import json

import pytest

import aura_music_studio.aura_sandbox as sandbox_module
from aura_music_studio.aura_sandbox import AuraSandboxClient, _explicit_execute


class Response:
    def __init__(self, payload: dict | list, *, status_code: int = 200, headers: dict | None = None):
        self._body = json.dumps(payload).encode("utf-8")
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self._body), chunk_size):
            yield self._body[offset : offset + chunk_size]

    def close(self):
        self.closed = True


def test_sandbox_unconfigured_never_executes(monkeypatch):
    monkeypatch.delenv("AURA_SANDBOX_URL", raising=False)
    client = AuraSandboxClient()
    assert client.configured is False
    assert client.diagnostics()["host_execution"] is False
    with pytest.raises(RuntimeError, match="not configured"):
        client.run(code="print('hello')", language="python")


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/sandbox",
        "https://user:pass@sandbox.example",
        "https://sandbox.example?token=secret",
        "https://sandbox.example#fragment",
        "not-a-url",
    ],
)
def test_sandbox_rejects_unsafe_or_ambiguous_base_urls(monkeypatch, url):
    monkeypatch.setenv("AURA_SANDBOX_URL", url)
    assert AuraSandboxClient().configured is False


def test_sandbox_execution_contract_disables_network_redirects_and_host_execution(monkeypatch):
    monkeypatch.setenv("AURA_SANDBOX_URL", "http://aura-sandbox:9000")
    monkeypatch.setenv("AURA_SANDBOX_TOKEN", "test-token")
    captured = {}
    response = Response({"completed": True, "exit_code": 0, "stdout": "hello\n", "stderr": "", "timed_out": False})

    def fake_post(url, *, headers, json, timeout, allow_redirects, stream):
        captured.update(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
                "allow_redirects": allow_redirects,
                "stream": stream,
            }
        )
        return response

    monkeypatch.setattr(sandbox_module.requests, "post", fake_post)
    client = AuraSandboxClient()
    result = client.run(code="print('hello')", language="python")
    assert captured["url"] == "http://aura-sandbox:9000/v1/execute"
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    assert captured["json"]["network"] is False
    assert captured["json"]["filesystem"] == "ephemeral"
    assert captured["allow_redirects"] is False
    assert captured["stream"] is True
    assert captured["timeout"] == (5, 70)
    assert result["host_execution"] is False
    assert result["network_requested"] is False
    assert result["stdout"] == "hello\n"
    assert response.closed is True


def test_sandbox_refuses_redirect_response(monkeypatch):
    monkeypatch.setenv("AURA_SANDBOX_URL", "http://sandbox:9000")
    response = Response({}, status_code=302, headers={"Location": "http://other-service/internal"})
    monkeypatch.setattr(sandbox_module.requests, "post", lambda *args, **kwargs: response)
    with pytest.raises(RuntimeError, match="redirect refused"):
        AuraSandboxClient().run(code="print('x')", language="python")
    assert response.closed is True


def test_sandbox_transport_response_is_bounded_before_json_parse(monkeypatch):
    monkeypatch.setenv("AURA_SANDBOX_URL", "http://sandbox:9000")
    monkeypatch.setenv("AURA_SANDBOX_MAX_RESPONSE_BYTES", "4096")
    response = Response({"completed": True, "stdout": "x" * 10000})
    monkeypatch.setattr(sandbox_module.requests, "post", lambda *args, **kwargs: response)
    with pytest.raises(RuntimeError, match="transport limit"):
        AuraSandboxClient().run(code="print('x')", language="python")
    assert response.closed is True


def test_sandbox_content_length_over_limit_is_refused(monkeypatch):
    monkeypatch.setenv("AURA_SANDBOX_URL", "http://sandbox:9000")
    monkeypatch.setenv("AURA_SANDBOX_MAX_RESPONSE_BYTES", "4096")
    response = Response({"completed": True}, headers={"Content-Length": "5000"})
    monkeypatch.setattr(sandbox_module.requests, "post", lambda *args, **kwargs: response)
    with pytest.raises(RuntimeError, match="transport limit"):
        AuraSandboxClient().run(code="print('x')", language="python")


def test_sandbox_requires_json_object(monkeypatch):
    monkeypatch.setenv("AURA_SANDBOX_URL", "http://sandbox:9000")
    response = Response(["not", "an", "object"])
    monkeypatch.setattr(sandbox_module.requests, "post", lambda *args, **kwargs: response)
    with pytest.raises(RuntimeError, match="response object"):
        AuraSandboxClient().run(code="print('x')", language="python")


def test_sandbox_output_is_bounded(monkeypatch):
    monkeypatch.setenv("AURA_SANDBOX_URL", "http://sandbox:9000")
    monkeypatch.setenv("AURA_SANDBOX_MAX_OUTPUT_CHARS", "1000")
    response = Response({"completed": True, "exit_code": 0, "stdout": "x" * 5000, "stderr": "", "timed_out": False})
    monkeypatch.setattr(sandbox_module.requests, "post", lambda *args, **kwargs: response)
    result = AuraSandboxClient().run(code="print('x')", language="python")
    assert len(result["stdout"]) == 1000
    assert result["output_truncated"] is True


def test_sandbox_diagnostics_are_truthful_and_secret_free(monkeypatch):
    monkeypatch.setenv("AURA_SANDBOX_URL", "http://sandbox:9000")
    monkeypatch.setenv("AURA_SANDBOX_TOKEN", "never-expose-me")
    diagnostics = AuraSandboxClient().diagnostics()
    assert diagnostics["configured"] is True
    assert diagnostics["host_execution"] is False
    assert diagnostics["network_requested"] is False
    assert diagnostics["redirects_allowed"] is False
    assert diagnostics["max_response_bytes"] == 1024 * 1024
    assert "token" not in diagnostics
    assert "never-expose-me" not in str(diagnostics)


def test_code_run_requires_explicit_member_wording():
    assert _explicit_execute("Run this code artifact") is True
    assert _explicit_execute("Test the Python script") is True
    assert _explicit_execute("Explain this code artifact") is False
