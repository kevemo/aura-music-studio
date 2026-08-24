from __future__ import annotations

import os
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, HTTPException, Request

from . import aura_agent_core as core
from . import aura_agent_tools as tools
from .aura_artifacts import artifact_store, _select_artifact
from .aura_runtime_context import current_turn

router = APIRouter(tags=["Aura Sandbox"])
_INSTALLED = False


class AuraSandboxClient:
    """Adapter for a separately isolated code-execution service.

    Pulsar-Frequency House never executes arbitrary member code inside the FastAPI process.
    The operator must connect an external/container sandbox service implementing the bounded
    JSON contract documented in docs/AURA_SANDBOX.md.
    """

    def __init__(self):
        self.base_url = (os.getenv("AURA_SANDBOX_URL") or "").strip().rstrip("/")
        self.token = (os.getenv("AURA_SANDBOX_TOKEN") or "").strip()
        self.timeout = max(5, min(int(os.getenv("AURA_SANDBOX_TIMEOUT_SECONDS", "60")), 300))
        self.max_code_chars = max(1000, min(int(os.getenv("AURA_SANDBOX_MAX_CODE_CHARS", "100000")), 500000))
        self.max_output_chars = max(1000, min(int(os.getenv("AURA_SANDBOX_MAX_OUTPUT_CHARS", "100000")), 500000))

    @property
    def configured(self) -> bool:
        if not self.base_url:
            return False
        parsed = urlparse(self.base_url)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname)

    def diagnostics(self) -> dict:
        return {
            "configured": self.configured,
            "execution_location": "isolated_external_service" if self.configured else None,
            "host_execution": False,
            "timeout_seconds": self.timeout,
            "max_code_chars": self.max_code_chars,
            "max_output_chars": self.max_output_chars,
        }

    def run(self, *, code: str, language: str) -> dict:
        if not self.configured:
            raise RuntimeError("Aura code execution is not configured. Connect an isolated AURA_SANDBOX_URL; code will not run on the web host.")
        clean_code = str(code or "")
        if not clean_code.strip():
            raise ValueError("The selected code Artifact is empty")
        if len(clean_code) > self.max_code_chars:
            raise ValueError("Code Artifact exceeds the configured sandbox input limit")
        lang = (language or "text").strip().lower()[:80]
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
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
            timeout=self.timeout + 10,
        )
        response.raise_for_status()
        data = response.json()
        stdout = str(data.get("stdout") or "")[: self.max_output_chars]
        stderr = str(data.get("stderr") or "")[: self.max_output_chars]
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
            "output_truncated": len(str(data.get("stdout") or "")) > len(stdout) or len(str(data.get("stderr") or "")) > len(stderr),
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
