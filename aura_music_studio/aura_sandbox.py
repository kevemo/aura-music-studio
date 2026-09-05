from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

import requests
from fastapi import APIRouter, HTTPException, Request

from . import aura_agent_core as core
from . import aura_agent_tools as tools
from .aura_artifacts import artifact_store, _select_artifact
from .aura_runtime_context import current_turn

router = APIRouter(tags=["Aura Sandbox"])
_INSTALLED = False

_DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024
_MIN_MAX_RESPONSE_BYTES = 4096
_MAX_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = (os.getenv(name) or str(default)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    return max(minimum, min(value, maximum))


def _valid_sandbox_base_url(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    if parsed.query or parsed.fragment:
        return False
    return True


def _read_bounded_json(response, *, max_bytes: int) -> dict:
    content_length = (response.headers or {}).get("Content-Length") if hasattr(response, "headers") else None
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise RuntimeError("Aura sandbox response exceeded the configured transport limit")
        except ValueError:
            pass

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise RuntimeError("Aura sandbox response exceeded the configured transport limit")
        chunks.append(chunk)
    try:
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Aura sandbox returned an invalid JSON response") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Aura sandbox returned an invalid response object")
    return payload


class AuraSandboxClient:
    """Adapter for a separately isolated code-execution service.

    Pulsar-Frequency House never executes arbitrary member code inside the FastAPI process.
    The operator must connect an external/container sandbox service implementing the bounded
    JSON contract documented in docs/AURA_SANDBOX.md.
    """

    def __init__(self):
        self.base_url = (os.getenv("AURA_SANDBOX_URL") or "").strip().rstrip("/")
        self.token = (os.getenv("AURA_SANDBOX_TOKEN") or "").strip()
        self.timeout = _bounded_int("AURA_SANDBOX_TIMEOUT_SECONDS", 60, 5, 300)
        self.max_code_chars = _bounded_int("AURA_SANDBOX_MAX_CODE_CHARS", 100000, 1000, 500000)
        self.max_output_chars = _bounded_int("AURA_SANDBOX_MAX_OUTPUT_CHARS", 100000, 1000, 500000)
        self.max_response_bytes = _bounded_int(
            "AURA_SANDBOX_MAX_RESPONSE_BYTES",
            _DEFAULT_MAX_RESPONSE_BYTES,
            _MIN_MAX_RESPONSE_BYTES,
            _MAX_MAX_RESPONSE_BYTES,
        )

    @property
    def configured(self) -> bool:
        return _valid_sandbox_base_url(self.base_url)

    def diagnostics(self) -> dict:
        return {
            "configured": self.configured,
            "execution_location": "isolated_external_service" if self.configured else None,
            "host_execution": False,
            "timeout_seconds": self.timeout,
            "max_code_chars": self.max_code_chars,
            "max_output_chars": self.max_output_chars,
            "max_response_bytes": self.max_response_bytes,
            "network_requested": False,
            "redirects_allowed": False,
        }

    def run(self, *, code: str, language: str) -> dict:
        if not self.configured:
            raise RuntimeError(
                "Aura code execution is not configured. Connect a valid isolated AURA_SANDBOX_URL; code will not run on the web host."
            )
        clean_code = str(code or "")
        if not clean_code.strip():
            raise ValueError("The selected code Artifact is empty")
        if len(clean_code) > self.max_code_chars:
            raise ValueError("Code Artifact exceeds the configured sandbox input limit")
        lang = (language or "text").strip().lower()[:80]
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        response = None
        try:
            response = requests.post(
                f"{self.base_url}/v1/execute",
                headers=headers,
                json={
                    "language": lang,
                    "code": clean_code,
                    "timeout_seconds": self.timeout,
                    "network": False,
                    "filesystem": "ephemeral",
                },
                timeout=(5, self.timeout + 10),
                allow_redirects=False,
                stream=True,
            )
            status_code = int(getattr(response, "status_code", 0) or 0)
            if 300 <= status_code < 400:
                raise RuntimeError("Aura sandbox redirect refused")
            response.raise_for_status()
            data = _read_bounded_json(response, max_bytes=self.max_response_bytes)
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    close()

        stdout_raw = str(data.get("stdout") or "")
        stderr_raw = str(data.get("stderr") or "")
        stdout = stdout_raw[: self.max_output_chars]
        stderr = stderr_raw[: self.max_output_chars]
        return {
            "completed": bool(data.get("completed", True)),
            "exit_code": data.get("exit_code"),
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": bool(data.get("timed_out", False)),
            "language": lang,
            "host_execution": False,
            "network_requested": False,
            "filesystem": "ephemeral",
            "output_truncated": len(stdout_raw) > len(stdout) or len(stderr_raw) > len(stderr),
        }


sandbox = AuraSandboxClient()

SANDBOX_SPECS = [
    tools.ToolSpec(
        name="sandbox_status",
        description="Report whether Aura's separately isolated code-execution sandbox is configured. Never claims host execution.",
        arguments={},
    ),
    tools.ToolSpec(
        name="run_code_artifact",
        description="Execute one private code Artifact in the configured isolated sandbox. The web/FastAPI host never runs the member code.",
        arguments={"artifact": "Code Artifact id or unambiguous title."},
        write=True,
    ),
]


def _explicit_execute(text: str) -> bool:
    lower = (text or "").lower()
    action = any(word in lower for word in ("run", "execute", "test", "try", "compile"))
    target = any(word in lower for word in ("code", "script", "artifact", "program"))
    return action and target


def install_aura_sandbox_tools() -> None:
    global _INSTALLED, sandbox
    if _INSTALLED:
        return
    # Re-read deployment environment at application startup rather than module-import time.
    sandbox = AuraSandboxClient()
    for spec in SANDBOX_SPECS:
        if spec.name not in {item.name for item in tools.TOOL_SPECS}:
            tools.TOOL_SPECS.append(spec)
            tools._SPEC_BY_NAME[spec.name] = spec
    original_execute = tools.AuraToolRegistry.execute
    original_needs = core._needs_model_tool_router

    def execute(self, call: tools.ToolCall, *, latest_user_message: str):
        if call.name not in {"sandbox_status", "run_code_artifact"}:
            return original_execute(self, call, latest_user_message=latest_user_message)
        if not self.tools_enabled:
            raise PermissionError("Aura tools are disabled for this conversation")
        if call.name == "sandbox_status":
            return sandbox.diagnostics()
        if not _explicit_execute(latest_user_message):
            raise PermissionError("Running code requires an explicit execute/run/test instruction in the member's latest message")
        turn = current_turn()
        if turn is None or turn.user_id != self.member.user_id:
            raise RuntimeError("Current Aura conversation context is unavailable")
        rows = artifact_store.list(self.member.user_id, turn.thread_id)
        selected = _select_artifact(rows, str((call.arguments or {}).get("artifact") or ""))
        item = artifact_store.get(self.member.user_id, turn.thread_id, selected["id"])
        if not item or item.get("kind") != "code":
            raise ValueError("Only an Aura Artifact with kind=code can be executed")
        result = sandbox.run(code=item["content"], language=item.get("language") or "text")
        return {"artifact": {"id": item["id"], "title": item["title"], "version": item["current_version"]}, "sandbox": result}

    def needs_model_tool_router(text: str, pinned_project: str | None, tools_enabled: bool, web_enabled: bool) -> bool:
        lower = (text or "").lower()
        if tools_enabled and ("sandbox" in lower or _explicit_execute(text)):
            return True
        return original_needs(text, pinned_project, tools_enabled, web_enabled)

    tools.AuraToolRegistry.execute = execute
    core._needs_model_tool_router = needs_model_tool_router
    _INSTALLED = True


@router.get("/aura-intelligence/api/sandbox/status")
def sandbox_status(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return sandbox.diagnostics()


__all__ = ["router", "AuraSandboxClient", "sandbox", "install_aura_sandbox_tools"]
