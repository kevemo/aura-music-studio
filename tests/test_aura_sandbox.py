from __future__ import annotations

import pytest

import aura_music_studio.aura_sandbox as sandbox_module
from aura_music_studio.aura_sandbox import AuraSandboxClient, _explicit_execute


def test_sandbox_unconfigured_never_executes(monkeypatch):
    monkeypatch.delenv("AURA_SANDBOX_URL", raising=False)
    client = AuraSandboxClient()
    assert client.configured is False
    assert client.diagnostics()["host_execution"] is False
    with pytest.raises(RuntimeError, match="not configured"):
        client.run(code="print('hello')", language="python")


def test_sandbox_execution_contract_disables_network_and_host_execution(monkeypatch):
    monkeypatch.setenv("AURA_SANDBOX_URL", "http://aura-sandbox:9000")
    monkeypatch.setenv("AURA_SANDBOX_TOKEN", "test-token")
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"completed": True, "exit_code": 0, "stdout": "hello\n", "stderr": "", "timed_out": False}

    def fake_post(url, *, headers, json, timeout):
        captured.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return Response()

    monkeypatch.setattr(sandbox_module.requests, "post", fake_post)
    client = AuraSandboxClient()
    result = client.run(code="print('hello')", language="python")
    assert captured["url"] == "http://aura-sandbox:9000/v1/execute"
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    assert captured["json"]["network"] is False
    assert captured["json"]["filesystem"] == "ephemeral"
    assert result["host_execution"] is False
    assert result["network_requested"] is False
    assert result["stdout"] == "hello\n"


def test_sandbox_output_is_bounded(monkeypatch):
    monkeypatch.setenv("AURA_SANDBOX_URL", "http://sandbox:9000")
    monkeypatch.setenv("AURA_SANDBOX_MAX_OUTPUT_CHARS", "1000")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"completed": True, "exit_code": 0, "stdout": "x" * 5000, "stderr": "", "timed_out": False}

    monkeypatch.setattr(sandbox_module.requests, "post", lambda *args, **kwargs: Response())
    result = AuraSandboxClient().run(code="print('x')", language="python")
    assert len(result["stdout"]) == 1000
    assert result["output_truncated"] is True


def test_code_run_requires_explicit_member_wording():
    assert _explicit_execute("Run this code artifact") is True
    assert _explicit_execute("Test the Python script") is True
    assert _explicit_execute("Explain this code artifact") is False
