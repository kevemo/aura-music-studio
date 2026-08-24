from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import shlex
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests


@dataclass
class ImageEditRequest:
    prompt: str
    quality: str = "standard"
    provider: str = "auto"
    aspect_ratio: str = "1:1"
    project_id: str | None = None
    source_job_id: str | None = None
    preserve_subject: bool = True
    edit_strength: float = 0.65


@dataclass
class ImageEditResult:
    id: str
    provider: str
    model: str
    status: str
    output_path: str | None = None
    error: str | None = None
    created_at: str = ""
    request_json: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ImageEditError(RuntimeError):
    pass


class ImageEditingService:
    """Non-destructive real image editing with provider failover.

    Every successful edit creates a new image file. The source is never overwritten.
    Providers are intentionally abstracted because 4Infinity Creative Studios must remain
    editable even when one model/provider is changed or removed later.
    """

    VALID_RATIOS = {"1:1", "4:5", "3:2", "2:3", "16:9", "9:16"}
    VALID_QUALITIES = {"standard", "high", "professional"}
    VALID_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

    def __init__(self, output_root: str | Path | None = None):
        self.output_root = Path(output_root or os.getenv("AURA_IMAGE_OUTPUT_DIR", "outputs/images"))
        self.output_root.mkdir(parents=True, exist_ok=True)

    def edit(self, source: str | Path, request: ImageEditRequest) -> ImageEditResult:
        source_path = Path(source).resolve()
        self._validate_source(source_path)
        self._validate_request(request)
        errors: list[str] = []
        for provider in self._provider_order(request.provider):
            try:
                if provider == "local":
                    return self._edit_local(source_path, request)
                if provider == "openai":
                    return self._edit_openai(source_path, request)
            except Exception as exc:
                errors.append(f"{provider}: {type(exc).__name__}: {exc}")
        raise ImageEditError("No image editor succeeded. " + " | ".join(errors))

    def _validate_source(self, source: Path) -> None:
        if not source.is_file():
            raise ImageEditError("Source image is unavailable")
        if source.suffix.lower() not in self.VALID_SUFFIXES:
            raise ImageEditError("Source must be PNG, JPEG or WebP")
        max_bytes = int(os.getenv("AURA_IMAGE_MAX_BYTES", str(64 * 1024 * 1024)))
        size = source.stat().st_size
        if size < 256 or size > max_bytes:
            raise ImageEditError("Source image size is outside configured limits")

    def _validate_request(self, request: ImageEditRequest) -> None:
        if not (request.prompt or "").strip():
            raise ImageEditError("An edit instruction is required")
        if request.quality not in self.VALID_QUALITIES:
            raise ImageEditError(f"Unsupported image quality: {request.quality}")
        if request.aspect_ratio not in self.VALID_RATIOS:
            raise ImageEditError(f"Unsupported aspect ratio: {request.aspect_ratio}")
        if not 0.0 <= float(request.edit_strength) <= 1.0:
            raise ImageEditError("edit_strength must be between 0 and 1")

    def _provider_order(self, requested: str) -> list[str]:
        requested = (requested or "auto").strip().lower()
        if requested != "auto":
            if requested not in {"local", "openai"}:
                raise ImageEditError(f"Unknown image edit provider: {requested}")
            return [requested]
        result: list[str] = []
        if os.getenv("AURA_IMAGE_EDIT_CMD"):
            result.append("local")
        if os.getenv("OPENAI_API_KEY"):
            result.append("openai")
        if not result:
            raise ImageEditError("No real image editing provider is configured")
        return result

    @staticmethod
    def _size(ratio: str) -> str:
        if ratio in {"9:16", "4:5", "2:3"}:
            return "1024x1536"
        if ratio in {"16:9", "3:2"}:
            return "1536x1024"
        return "1024x1024"

    @staticmethod
    def _quality(value: str) -> str:
        return "high" if value in {"high", "professional"} else "medium"

    @staticmethod
    def _compose_prompt(request: ImageEditRequest) -> str:
        direction = (request.prompt or "").strip()
        preservation = (
            "Preserve the main subject identity, composition and unaffected details unless the requested edit requires a change."
            if request.preserve_subject
            else "You may reinterpret composition and subject treatment where useful to satisfy the edit."
        )
        strength = float(request.edit_strength)
        if strength <= 0.3:
            change = "Make a subtle, localized revision and keep the original image extremely close."
        elif strength >= 0.8:
            change = "Apply the requested revision strongly while retaining only details that remain compatible with the instruction."
        else:
            change = "Make a clear but controlled revision, preserving unrelated image details."
        return f"Edit instruction: {direction} {preservation} {change}"

    def _base_result(self, request: ImageEditRequest, provider: str, model: str) -> ImageEditResult:
        return ImageEditResult(
            id=uuid4().hex,
            provider=provider,
            model=model,
            status="submitted",
            created_at=datetime.now(timezone.utc).isoformat(),
            request_json=json.dumps(asdict(request), sort_keys=True),
        )

    def _edit_local(self, source: Path, request: ImageEditRequest) -> ImageEditResult:
        command = (os.getenv("AURA_IMAGE_EDIT_CMD") or "").strip()
        if not command:
            raise ImageEditError("AURA_IMAGE_EDIT_CMD is not configured")
        result = self._base_result(request, "local", os.getenv("AURA_IMAGE_EDIT_MODEL", "local-image-editor"))
        output = self.output_root / f"{result.id}.png"
        env = os.environ.copy()
        env.update(
            {
                "AURA_IMAGE_EDIT_SOURCE": str(source),
                "AURA_IMAGE_EDIT_OUTPUT": str(output.resolve()),
                "AURA_IMAGE_EDIT_PROMPT": self._compose_prompt(request),
                "AURA_IMAGE_EDIT_RATIO": request.aspect_ratio,
                "AURA_IMAGE_EDIT_QUALITY": request.quality,
                "AURA_IMAGE_EDIT_STRENGTH": str(request.edit_strength),
                "AURA_IMAGE_EDIT_PRESERVE_SUBJECT": "true" if request.preserve_subject else "false",
                "AURA_IMAGE_EDIT_PROJECT_ID": request.project_id or "",
            }
        )
        completed = subprocess.run(
            shlex.split(command),
            env=env,
            capture_output=True,
            text=True,
            timeout=int(os.getenv("AURA_IMAGE_EDIT_TIMEOUT", "900")),
            check=False,
        )
        if completed.returncode != 0:
            raise ImageEditError(completed.stderr.strip() or "Local image editor failed")
        if not output.is_file() or output.stat().st_size < 256:
            raise ImageEditError("Local image editor did not produce a valid image")
        result.status = "completed"
        result.output_path = str(output)
        return result

    def _edit_openai(self, source: Path, request: ImageEditRequest) -> ImageEditResult:
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise ImageEditError("OPENAI_API_KEY is not configured")
        model = os.getenv("AURA_OPENAI_IMAGE_MODEL", "gpt-image-2")
        result = self._base_result(request, "openai", model)
        mime = mimetypes.guess_type(source.name)[0] or "image/png"
        data = {
            "model": model,
            "prompt": self._compose_prompt(request),
            "size": self._size(request.aspect_ratio),
            "quality": self._quality(request.quality),
            "output_format": "png",
            "n": "1",
        }
        with source.open("rb") as handle:
            response = requests.post(
                "https://api.openai.com/v1/images/edits",
                headers={"Authorization": f"Bearer {api_key}"},
                data=data,
                files={"image": (source.name, handle, mime)},
                timeout=int(os.getenv("AURA_IMAGE_API_TIMEOUT", "180")),
            )
        if response.status_code >= 300:
            raise ImageEditError(f"OpenAI image edit failed ({response.status_code}): {response.text[:500]}")
        payload = response.json()
        items = payload.get("data") or []
        if not items:
            raise ImageEditError("OpenAI image edit returned no image")
        item = items[0]
        encoded = item.get("b64_json")
        if encoded:
            try:
                raw = base64.b64decode(encoded, validate=True)
            except Exception as exc:
                raise ImageEditError("OpenAI returned invalid base64 image data") from exc
        else:
            url = item.get("url")
            if not url:
                raise ImageEditError("OpenAI image edit returned no downloadable output")
            remote = requests.get(url, timeout=180)
            if remote.status_code >= 300:
                raise ImageEditError(f"OpenAI image edit download failed ({remote.status_code})")
            raw = remote.content
        max_bytes = int(os.getenv("AURA_IMAGE_MAX_BYTES", str(64 * 1024 * 1024)))
        if len(raw) < 256 or len(raw) > max_bytes:
            raise ImageEditError("Edited image size is outside configured limits")
        output = self.output_root / f"{result.id}.png"
        output.write_bytes(raw)
        result.status = "completed"
        result.output_path = str(output)
        return result

    @staticmethod
    def provenance_hash(result: ImageEditResult, *, source_sha256: str) -> str:
        payload = {
            "source_sha256": source_sha256,
            "edit": result.to_dict(),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
