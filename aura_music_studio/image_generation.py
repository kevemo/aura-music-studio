from __future__ import annotations

import base64
import hashlib
import json
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
class ImageGenerationRequest:
    prompt: str
    mode: str = "image"
    aspect_ratio: str = "1:1"
    quality: str = "standard"
    provider: str = "auto"
    background: str = "opaque"
    project_id: str | None = None
    title_text: str | None = None
    subtitle_text: str | None = None
    call_to_action: str | None = None
    brand_direction: str | None = None


@dataclass
class ImageGenerationResult:
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


class ImageGenerationError(RuntimeError):
    pass


class ImageGenerationService:
    """Real image/poster generation with provider failover and no fake outputs.

    Auto order:
    1. local/self-hosted renderer (`AURA_IMAGE_RENDER_CMD`)
    2. OpenAI GPT Image 2 (`OPENAI_API_KEY`)

    Poster mode deliberately separates visual-art direction from later editable
    typography/layer composition. The model may render text requested in the
    prompt, but the Visual FX Studio remains the source of truth for editable
    titles, logos, CTAs and other production layers.
    """

    VALID_MODES = {"image", "poster", "cover_art", "social_graphic", "thumbnail"}
    VALID_RATIOS = {"1:1", "4:5", "3:2", "2:3", "16:9", "9:16"}
    VALID_QUALITIES = {"standard", "high", "professional"}
    VALID_BACKGROUNDS = {"opaque", "transparent", "auto"}

    def __init__(self, output_root: str | Path | None = None):
        self.output_root = Path(output_root or os.getenv("AURA_IMAGE_OUTPUT_DIR", "outputs/images"))
        self.output_root.mkdir(parents=True, exist_ok=True)

    def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        self._validate(request)
        errors: list[str] = []
        for provider in self._provider_order(request.provider):
            try:
                if provider == "local":
                    return self._generate_local(request)
                if provider == "openai":
                    return self._generate_openai(request)
            except Exception as exc:
                errors.append(f"{provider}: {exc}")
        raise ImageGenerationError("No image renderer succeeded. " + " | ".join(errors))

    def _validate(self, request: ImageGenerationRequest) -> None:
        if not request.prompt or not request.prompt.strip():
            raise ImageGenerationError("An image prompt is required")
        if request.mode not in self.VALID_MODES:
            raise ImageGenerationError(f"Unsupported image mode: {request.mode}")
        if request.aspect_ratio not in self.VALID_RATIOS:
            raise ImageGenerationError(f"Unsupported aspect ratio: {request.aspect_ratio}")
        if request.quality not in self.VALID_QUALITIES:
            raise ImageGenerationError(f"Unsupported image quality: {request.quality}")
        if request.background not in self.VALID_BACKGROUNDS:
            raise ImageGenerationError(f"Unsupported background mode: {request.background}")

    def _provider_order(self, requested: str) -> list[str]:
        requested = (requested or "auto").strip().lower()
        if requested != "auto":
            if requested not in {"local", "openai"}:
                raise ImageGenerationError(f"Unknown image provider: {requested}")
            return [requested]
        order: list[str] = []
        if os.getenv("AURA_IMAGE_RENDER_CMD"):
            order.append("local")
        if os.getenv("OPENAI_API_KEY"):
            order.append("openai")
        if not order:
            raise ImageGenerationError(
                "No real image provider is configured. Set AURA_IMAGE_RENDER_CMD or OPENAI_API_KEY."
            )
        return order

    @staticmethod
    def _size(ratio: str) -> str:
        # GPT Image 2 supports flexible sizes. These canonical production sizes
        # give predictable memory/cost while preserving the requested orientation.
        if ratio in {"9:16", "4:5", "2:3"}:
            return "1024x1536"
        if ratio in {"16:9", "3:2"}:
            return "1536x1024"
        return "1024x1024"

    @staticmethod
    def _quality(value: str) -> str:
        return "high" if value in {"high", "professional"} else "medium"

    @staticmethod
    def _compose_prompt(request: ImageGenerationRequest) -> str:
        parts = [request.prompt.strip()]
        if request.mode == "poster":
            parts.append(
                "Design as a premium professional poster with deliberate hierarchy, clean negative space, "
                "strong focal composition, production-ready lighting and legible typography zones."
            )
        elif request.mode == "cover_art":
            parts.append("Design as premium release-ready cover artwork with a strong central visual identity.")
        elif request.mode == "thumbnail":
            parts.append("Design for immediate small-screen readability with a strong focal subject and high visual clarity.")
        elif request.mode == "social_graphic":
            parts.append("Design as polished social media creative with clear hierarchy and mobile-first composition.")
        if request.title_text:
            parts.append(f"Primary title text: {request.title_text}")
        if request.subtitle_text:
            parts.append(f"Secondary text: {request.subtitle_text}")
        if request.call_to_action:
            parts.append(f"Call to action: {request.call_to_action}")
        if request.brand_direction:
            parts.append(f"Brand direction: {request.brand_direction}")
        return " ".join(parts)

    def _base_result(self, request: ImageGenerationRequest, provider: str, model: str) -> ImageGenerationResult:
        return ImageGenerationResult(
            id=uuid4().hex,
            provider=provider,
            model=model,
            status="submitted",
            created_at=datetime.now(timezone.utc).isoformat(),
            request_json=json.dumps(asdict(request), sort_keys=True),
        )

    def _generate_local(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        command = os.getenv("AURA_IMAGE_RENDER_CMD", "").strip()
        if not command:
            raise ImageGenerationError("AURA_IMAGE_RENDER_CMD is not configured")
        result = self._base_result(request, "local", os.getenv("AURA_IMAGE_MODEL", "local-image-renderer"))
        output = self.output_root / f"{result.id}.png"
        env = os.environ.copy()
        env.update(
            {
                "AURA_IMAGE_PROMPT": self._compose_prompt(request),
                "AURA_IMAGE_MODE": request.mode,
                "AURA_IMAGE_RATIO": request.aspect_ratio,
                "AURA_IMAGE_QUALITY": request.quality,
                "AURA_IMAGE_BACKGROUND": request.background,
                "AURA_IMAGE_OUTPUT": str(output.resolve()),
                "AURA_IMAGE_PROJECT_ID": request.project_id or "",
            }
        )
        completed = subprocess.run(
            shlex.split(command),
            env=env,
            capture_output=True,
            text=True,
            timeout=int(os.getenv("AURA_IMAGE_RENDER_TIMEOUT", "900")),
            check=False,
        )
        if completed.returncode != 0:
            raise ImageGenerationError(completed.stderr.strip() or "Local image renderer failed")
        if not output.exists() or output.stat().st_size < 1024:
            raise ImageGenerationError("Local image renderer did not produce a valid image")
        result.status = "completed"
        result.output_path = str(output)
        return result

    def _generate_openai(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ImageGenerationError("OPENAI_API_KEY is not configured")
        model = os.getenv("AURA_OPENAI_IMAGE_MODEL", "gpt-image-2")
        result = self._base_result(request, "openai", model)
        payload: dict[str, Any] = {
            "model": model,
            "prompt": self._compose_prompt(request),
            "size": self._size(request.aspect_ratio),
            "quality": self._quality(request.quality),
            "background": request.background,
            "output_format": "png",
            "n": 1,
        }
        response = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=int(os.getenv("AURA_IMAGE_API_TIMEOUT", "180")),
        )
        if response.status_code >= 300:
            raise ImageGenerationError(f"OpenAI image request failed ({response.status_code}): {response.text[:500]}")
        data = response.json()
        items = data.get("data") or []
        if not items:
            raise ImageGenerationError("OpenAI image generation returned no image")
        item = items[0]
        encoded = item.get("b64_json")
        if not encoded:
            url = item.get("url")
            if not url:
                raise ImageGenerationError("OpenAI image generation returned no downloadable output")
            remote = requests.get(url, timeout=180)
            if remote.status_code >= 300:
                raise ImageGenerationError(f"OpenAI image download failed ({remote.status_code})")
            raw = remote.content
        else:
            try:
                raw = base64.b64decode(encoded, validate=True)
            except Exception as exc:
                raise ImageGenerationError("OpenAI returned invalid base64 image data") from exc
        max_bytes = int(os.getenv("AURA_IMAGE_MAX_BYTES", str(64 * 1024 * 1024)))
        if len(raw) < 1024 or len(raw) > max_bytes:
            raise ImageGenerationError("Generated image size is outside configured limits")
        output = self.output_root / f"{result.id}.png"
        output.write_bytes(raw)
        result.status = "completed"
        result.output_path = str(output)
        return result

    @staticmethod
    def provenance_hash(result: ImageGenerationResult) -> str:
        payload = json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
