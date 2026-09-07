from __future__ import annotations

import json
import mimetypes
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel

RendererKind = Literal["image", "video"]
RendererQueueState = Literal["running", "pending", "not_queued"]
RendererCancellationState = Literal["cancelled_running", "cancelled_pending", "not_queued"]
_SAFE_WORKFLOW = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.json$")
_SAFE_RENDERER_INPUT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_SAFE_RENDERER_SUBFOLDER_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_SAFE_PROMPT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_IMAGE_INPUT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif"}


class RendererOutput(BaseModel):
    node_id: str
    filename: str
    subfolder: str = ""
    type: str = "output"
    channel: str = "file"


class RendererInput(BaseModel):
    name: str
    subfolder: str = ""
    type: str = "input"
    workflow_value: str


class RendererSubmission(BaseModel):
    kind: RendererKind
    provider: str = "comfyui"
    prompt_id: str
    client_id: str
    workflow_name: str


class RendererCancellation(BaseModel):
    kind: RendererKind
    provider: str = "comfyui"
    prompt_id: str
    state: RendererCancellationState


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

    Workflow JSON remains operator-owned. The Command Center injects only declared template
    variables, submits API-format workflows to `/prompt`, then reads execution history from
    `/history/{prompt_id}`. This keeps model choice replaceable while privileged filesystem
    locations and provider configuration remain server-side.
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
        self.download_timeout_seconds = max(
            30.0, float(os.getenv("AURA_COMFYUI_DOWNLOAD_TIMEOUT_SECONDS", "600"))
        )
        self.max_output_bytes = max(
            16 * 1024 * 1024,
            int(float(os.getenv("AURA_CREATIVE_MAX_OUTPUT_MB", "4096")) * 1024 * 1024),
        )
        self.max_input_bytes = max(
            1 * 1024 * 1024,
            int(float(os.getenv("AURA_CREATIVE_MAX_INPUT_MB", "64")) * 1024 * 1024),
        )

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

    @staticmethod
    def _renderer_input_value(name: str, subfolder: str) -> str:
        if not _SAFE_RENDERER_INPUT_NAME.fullmatch(name):
            raise ValueError("Renderer returned an unsafe input filename")
        normalized = str(subfolder or "").replace("\\", "/").strip("/")
        if normalized:
            parts = normalized.split("/")
            if any(
                part in {"", ".", ".."} or not _SAFE_RENDERER_SUBFOLDER_PART.fullmatch(part)
                for part in parts
            ):
                raise ValueError("Renderer returned an unsafe input subfolder")
            return "/".join([*parts, name])
        return name

    @staticmethod
    def _validate_prompt_id(prompt_id: str) -> str:
        prompt_id = str(prompt_id or "").strip()
        if not _SAFE_PROMPT_ID.fullmatch(prompt_id):
            raise ValueError("Invalid ComfyUI prompt id")
        return prompt_id

    def upload_image_input(self, source: Path) -> RendererInput:
        """Upload a server-validated local image as an opaque ComfyUI input token.

        The caller controls neither the renderer-side filename nor the subfolder. This method
        deliberately never returns the source filesystem path.
        """

        if not self.base_url:
            raise RuntimeError("ComfyUI base URL is not configured")
        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError(source)
        suffix = source.suffix.lower()
        if suffix not in _IMAGE_INPUT_EXTENSIONS:
            raise ValueError("Unsupported image format for renderer input")
        size = source.stat().st_size
        if size <= 0:
            raise ValueError("Renderer input image is empty")
        if size > self.max_input_bytes:
            raise ValueError("Renderer input image exceeds configured maximum size")

        renderer_name = f"aura_{uuid4().hex}{suffix}"
        media_type = mimetypes.guess_type(renderer_name)[0] or "application/octet-stream"
        with source.open("rb") as handle:
            with httpx.Client(timeout=self.download_timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/upload/image",
                    files={"image": (renderer_name, handle, media_type)},
                    data={"type": "input", "overwrite": "false"},
                )
                response.raise_for_status()
                value = response.json()
        if not isinstance(value, dict):
            raise RuntimeError("Unexpected ComfyUI input-upload response")
        name = str(value.get("name") or "").strip()
        subfolder = str(value.get("subfolder") or "").strip()
        folder_type = str(value.get("type") or "input").strip().lower()
        if folder_type != "input":
            raise RuntimeError("ComfyUI did not register the source image as an input")
        workflow_value = self._renderer_input_value(name, subfolder)
        return RendererInput(
            name=name,
            subfolder=subfolder.replace("\\", "/").strip("/"),
            type="input",
            workflow_value=workflow_value,
        )

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

    def queue_state(self, prompt_id: str) -> RendererQueueState:
        """Return only the queue position for one prompt without exposing workflow payloads."""

        prompt_id = self._validate_prompt_id(prompt_id)
        if not self.base_url:
            raise RuntimeError("ComfyUI base URL is not configured")
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(f"{self.base_url}/queue")
            response.raise_for_status()
            value = response.json()
        if not isinstance(value, dict):
            raise RuntimeError("Unexpected ComfyUI queue response")
        for state, key in (("running", "queue_running"), ("pending", "queue_pending")):
            rows = value.get(key, [])
            if not isinstance(rows, list):
                raise RuntimeError("Unexpected ComfyUI queue response")
            for row in rows:
                if isinstance(row, (list, tuple)) and len(row) > 1 and str(row[1]) == prompt_id:
                    return state
        return "not_queued"

    def cancel(self, prompt_id: str) -> RendererCancellation:
        """Cancel one prompt without ever issuing a global renderer interrupt.

        Pending jobs are deleted from the queue. Running jobs use the prompt-id-scoped
        interrupt supported by current ComfyUI servers. A queued job can race into execution
        between inspection and deletion, so the queue is checked again and interrupted only
        when the exact same prompt id is then observed as running.
        """

        prompt_id = self._validate_prompt_id(prompt_id)
        if not self.base_url:
            raise RuntimeError("ComfyUI base URL is not configured")
        state = self.queue_state(prompt_id)
        if state == "not_queued":
            return RendererCancellation(kind=self.kind, prompt_id=prompt_id, state="not_queued")

        if state == "pending":
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/queue",
                    json={"delete": [prompt_id]},
                )
                response.raise_for_status()
            follow_up = self.queue_state(prompt_id)
            if follow_up == "running":
                state = "running"
            elif follow_up == "pending":
                raise RuntimeError("ComfyUI did not remove the requested pending prompt")
            else:
                return RendererCancellation(
                    kind=self.kind,
                    prompt_id=prompt_id,
                    state="cancelled_pending",
                )

        # Never fall back to a body-less/global interrupt: doing so could terminate another
        # member's work on a shared renderer if the queue changes concurrently.
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/interrupt",
                json={"prompt_id": prompt_id},
            )
            response.raise_for_status()
        return RendererCancellation(
            kind=self.kind,
            prompt_id=prompt_id,
            state="cancelled_running",
        )

    def history(self, prompt_id: str) -> dict:
        prompt_id = self._validate_prompt_id(prompt_id)
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

    def download_output(self, output: RendererOutput, destination: Path) -> Path:
        """Stream one server-reported ComfyUI output into a caller-owned safe path."""
        if not self.base_url:
            raise RuntimeError("ComfyUI base URL is not configured")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".part")
        total = 0
        try:
            params = {
                "filename": output.filename,
                "subfolder": output.subfolder,
                "type": output.type,
            }
            with httpx.Client(timeout=self.download_timeout_seconds) as client:
                with client.stream("GET", f"{self.base_url}/view", params=params) as response:
                    response.raise_for_status()
                    length = response.headers.get("content-length")
                    if length and int(length) > self.max_output_bytes:
                        raise ValueError("ComfyUI output exceeds configured maximum size")
                    with temporary.open("wb") as target:
                        for chunk in response.iter_bytes(1024 * 1024):
                            total += len(chunk)
                            if total > self.max_output_bytes:
                                raise ValueError("ComfyUI output exceeds configured maximum size")
                            target.write(chunk)
            temporary.replace(destination)
            return destination
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


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


__all__ = [
    "ComfyUIRenderer",
    "RendererCancellation",
    "RendererCancellationState",
    "RendererInput",
    "RendererKind",
    "RendererOutput",
    "RendererQueueState",
    "RendererState",
    "RendererSubmission",
    "renderer_for",
    "renderer_states",
]
