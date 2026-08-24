from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

RendererKind = Literal["image", "video"]
_SAFE_WORKFLOW = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.json$")


class RendererOutput(BaseModel):
    node_id: str
    filename: str
    subfolder: str = ""
    type: str = "output"
    channel: str = "file"


class RendererSubmission(BaseModel):
    kind: RendererKind
    provider: str = "comfyui"
    prompt_id: str
    client_id: str
    workflow_name: str


class RendererState(BaseModel):
    kind: RendererKind
    provider: str = "comfyui"
    configured: bool
    connected: bool = False
    base_url: str | None = None
    workflow_name: str | None = None
    detail: str = ""


class ComfyUIRenderer:
    """Small provider-neutral bridge to a self-hosted ComfyUI server.

    Workflow JSON remains operator-owned. Pulsar-Frequency House injects only declared
    template variables, submits API-format workflows to `/prompt`, then reads execution
    history from `/history/{prompt_id}`. This keeps model choice replaceable: an operator
    can use an image workflow, LTX/Wan/Hunyuan video workflow or another compatible graph
    without changing the public creative-project schema.
    """

    def __init__(self, kind: RendererKind):
        self.kind = kind
        self.base_url = (os.getenv("AURA_COMFYUI_URL") or "").strip().rstrip("/")
        self.workflow_dir = Path(
            os.getenv("AURA_COMFYUI_WORKFLOW_DIR", "config/comfyui")
        ).resolve()
        env_name = f"AURA_COMFYUI_{kind.upper()}_WORKFLOW"
        self.workflow_name = (os.getenv(env_name) or "").strip()
        self.timeout_seconds = max(3.0, float(os.getenv("AURA_COMFYUI_TIMEOUT_SECONDS", "30")))

    @property
    def configured(self) -> bool:
        if not self.base_url or not self.workflow_name:
            return False
        try:
            return self.workflow_path().is_file()
        except ValueError:
            return False

    def workflow_path(self) -> Path:
        name = self.workflow_name
        if not _SAFE_WORKFLOW.fullmatch(name):
            raise ValueError("ComfyUI workflow must be a safe .json filename")
        target = (self.workflow_dir / name).resolve()
        if self.workflow_dir not in target.parents:
            raise ValueError("ComfyUI workflow resolves outside the configured workflow directory")
        return target

    def load_workflow(self) -> dict:
        path = self.workflow_path()
        if not path.is_file():
            raise FileNotFoundError(path)
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not value:
            raise ValueError("ComfyUI API workflow must be a non-empty JSON object")
        return value

    @staticmethod
    def _render_value(value: Any, variables: dict[str, Any]) -> Any:
        if isinstance(value, dict):
            return {key: ComfyUIRenderer._render_value(item, variables) for key, item in value.items()}
        if isinstance(value, list):
            return [ComfyUIRenderer._render_value(item, variables) for item in value]
        if not isinstance(value, str):
            return value

        exact = re.fullmatch(r"\{\{([A-Za-z0-9_]+)\}\}", value)
        if exact:
            return deepcopy(variables.get(exact.group(1), value))

        rendered = value
        for key, item in variables.items():
            token = "{{" + key + "}}"
            if token in rendered:
                rendered = rendered.replace(token, str(item))
        return rendered

    def prepare_workflow(self, variables: dict[str, Any]) -> dict:
        workflow = self.load_workflow()
        return self._render_value(workflow, variables)

    def probe(self) -> RendererState:
        if not self.configured:
            return RendererState(
                kind=self.kind,
                configured=False,
                connected=False,
                base_url=self.base_url or None,
                workflow_name=self.workflow_name or None,
                detail="Set AURA_COMFYUI_URL and a valid workflow filename for this media kind.",
            )
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.get(f"{self.base_url}/queue")
                response.raise_for_status()
            return RendererState(
                kind=self.kind,
                configured=True,
                connected=True,
                base_url=self.base_url,
                workflow_name=self.workflow_name,
                detail="ComfyUI server reachable and workflow configured.",
            )
        except Exception as exc:
            return RendererState(
                kind=self.kind,
                configured=True,
                connected=False,
                base_url=self.base_url,
                workflow_name=self.workflow_name,
                detail=f"ComfyUI configured but unavailable: {type(exc).__name__}: {exc}",
            )

    def submit(self, variables: dict[str, Any]) -> RendererSubmission:
        if not self.configured:
            raise RuntimeError(f"ComfyUI {self.kind} renderer is not configured")
        client_id = str(uuid4())
        workflow = self.prepare_workflow(variables)
        payload = {"prompt": workflow, "client_id": client_id}
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(f"{self.base_url}/prompt", json=payload)
            response.raise_for_status()
            result = response.json()
        prompt_id = str(result.get("prompt_id") or "").strip()
        if not prompt_id:
            raise RuntimeError("ComfyUI accepted no prompt_id")
        return RendererSubmission(
            kind=self.kind,
            prompt_id=prompt_id,
            client_id=client_id,
            workflow_name=self.workflow_name,
        )

    def history(self, prompt_id: str) -> dict:
        if not prompt_id or len(prompt_id) > 200:
            raise ValueError("Invalid ComfyUI prompt id")
        if not self.base_url:
            raise RuntimeError("ComfyUI base URL is not configured")
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(f"{self.base_url}/history/{prompt_id}")
            response.raise_for_status()
            value = response.json()
        if not isinstance(value, dict):
            raise RuntimeError("Unexpected ComfyUI history response")
        return value

    @staticmethod
    def collect_outputs(history: dict, prompt_id: str) -> list[RendererOutput]:
        entry = history.get(prompt_id)
        if not isinstance(entry, dict):
            return []
        outputs = entry.get("outputs")
        if not isinstance(outputs, dict):
            return []
        rows: list[RendererOutput] = []
        seen: set[tuple[str, str, str]] = set()
        for node_id, node_output in outputs.items():
            if not isinstance(node_output, dict):
                continue
            for channel, items in node_output.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict) or not item.get("filename"):
                        continue
                    filename = str(item["filename"])
                    subfolder = str(item.get("subfolder") or "")
                    folder_type = str(item.get("type") or "output")
                    key = (filename, subfolder, folder_type)
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(
                        RendererOutput(
                            node_id=str(node_id),
                            filename=filename,
                            subfolder=subfolder,
                            type=folder_type,
                            channel=str(channel),
                        )
                    )
        return rows


def renderer_for(kind: RendererKind) -> ComfyUIRenderer:
    return ComfyUIRenderer(kind)


def renderer_states(*, probe: bool = False) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for kind in ("image", "video"):
        renderer = renderer_for(kind)
        if probe:
            state = renderer.probe()
        else:
            state = RendererState(
                kind=kind,
                configured=renderer.configured,
                connected=False,
                base_url=renderer.base_url or None,
                workflow_name=renderer.workflow_name or None,
                detail=(
                    "Configured; call the probe endpoint to check connectivity."
                    if renderer.configured
                    else "Renderer integration slot is available but not configured."
                ),
            )
        rows[kind] = state.model_dump(mode="json")
    return rows
