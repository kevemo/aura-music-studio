from __future__ import annotations

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
class VideoGenerationRequest:
    prompt: str
    mode: str = "text_to_video"
    aspect_ratio: str = "9:16"
    duration_seconds: int = 8
    provider: str = "auto"
    quality: str = "standard"
    reference_url: str | None = None
    negative_prompt: str | None = None
    project_id: str | None = None
    target_platform: str | None = None


@dataclass
class VideoGenerationResult:
    id: str
    provider: str
    model: str
    status: str
    provider_job_id: str | None = None
    output_url: str | None = None
    output_path: str | None = None
    error: str | None = None
    created_at: str = ""
    request_json: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VideoGenerationError(RuntimeError):
    pass


class VideoGenerationService:
    """Model-agnostic video generator used by the Live Sound Studio.

    Provider order in auto mode:
    1. local/self-hosted command (`AURA_VIDEO_RENDER_CMD`)
    2. OpenAI video generation (`OPENAI_API_KEY`)
    3. Runway (`RUNWAYML_API_SECRET`)

    The service never fabricates a successful video. A request that cannot be
    submitted to a real configured renderer returns a clear failure.
    """

    VALID_MODES = {"text_to_video", "image_to_video", "video_to_video"}
    VALID_RATIOS = {"9:16", "16:9", "1:1"}

    def __init__(self, output_root: str | Path | None = None):
        self.output_root = Path(output_root or os.getenv("AURA_VIDEO_OUTPUT_DIR", "outputs/video"))
        self.output_root.mkdir(parents=True, exist_ok=True)

    def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        self._validate(request)
        providers = self._provider_order(request.provider)
        errors: list[str] = []
        for provider in providers:
            try:
                if provider == "local":
                    return self._generate_local(request)
                if provider == "openai":
                    return self._generate_openai(request)
                if provider == "runway":
                    return self._generate_runway(request)
            except Exception as exc:  # provider failover is deliberate
                errors.append(f"{provider}: {exc}")
        raise VideoGenerationError("No video renderer succeeded. " + " | ".join(errors))

    def _validate(self, request: VideoGenerationRequest) -> None:
        if not request.prompt or not request.prompt.strip():
            raise VideoGenerationError("A video prompt is required")
        if request.mode not in self.VALID_MODES:
            raise VideoGenerationError(f"Unsupported video mode: {request.mode}")
        if request.aspect_ratio not in self.VALID_RATIOS:
            raise VideoGenerationError(f"Unsupported aspect ratio: {request.aspect_ratio}")
        if request.mode in {"image_to_video", "video_to_video"} and not request.reference_url:
            raise VideoGenerationError(f"{request.mode} requires a reference URL or uploaded asset")
        if request.duration_seconds < 1 or request.duration_seconds > 60:
            raise VideoGenerationError("Video duration must be between 1 and 60 seconds")

    def _provider_order(self, requested: str) -> list[str]:
        requested = (requested or "auto").strip().lower()
        if requested != "auto":
            if requested not in {"local", "openai", "runway"}:
                raise VideoGenerationError(f"Unknown video provider: {requested}")
            return [requested]
        order: list[str] = []
        if os.getenv("AURA_VIDEO_RENDER_CMD"):
            order.append("local")
        if os.getenv("OPENAI_API_KEY"):
            order.append("openai")
        if os.getenv("RUNWAYML_API_SECRET"):
            order.append("runway")
        if not order:
            raise VideoGenerationError(
                "No real video provider is configured. Set AURA_VIDEO_RENDER_CMD, OPENAI_API_KEY, or RUNWAYML_API_SECRET."
            )
        return order

    def _base_result(self, request: VideoGenerationRequest, provider: str, model: str) -> VideoGenerationResult:
        return VideoGenerationResult(
            id=uuid4().hex,
            provider=provider,
            model=model,
            status="submitted",
            created_at=datetime.now(timezone.utc).isoformat(),
            request_json=json.dumps(asdict(request), sort_keys=True),
        )

    def _generate_local(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        command = os.getenv("AURA_VIDEO_RENDER_CMD", "").strip()
        if not command:
            raise VideoGenerationError("AURA_VIDEO_RENDER_CMD is not configured")
        result = self._base_result(request, "local", os.getenv("AURA_VIDEO_MODEL", "local-video-renderer"))
        output = self.output_root / f"{result.id}.mp4"
        env = os.environ.copy()
        env.update(
            {
                "AURA_VIDEO_PROMPT": request.prompt,
                "AURA_VIDEO_MODE": request.mode,
                "AURA_VIDEO_RATIO": request.aspect_ratio,
                "AURA_VIDEO_DURATION": str(request.duration_seconds),
                "AURA_VIDEO_OUTPUT": str(output.resolve()),
                "AURA_VIDEO_REFERENCE": request.reference_url or "",
                "AURA_VIDEO_NEGATIVE_PROMPT": request.negative_prompt or "",
                "AURA_VIDEO_PROJECT_ID": request.project_id or "",
            }
        )
        completed = subprocess.run(
            shlex.split(command),
            env=env,
            capture_output=True,
            text=True,
            timeout=int(os.getenv("AURA_VIDEO_RENDER_TIMEOUT", "1800")),
            check=False,
        )
        if completed.returncode != 0:
            raise VideoGenerationError(completed.stderr.strip() or "Local video renderer failed")
        if not output.exists() or output.stat().st_size < 1024:
            raise VideoGenerationError("Local video renderer did not produce a valid MP4 output")
        result.status = "completed"
        result.output_path = str(output)
        return result

    @staticmethod
    def _openai_size(ratio: str) -> str:
        return {"9:16": "720x1280", "16:9": "1280x720", "1:1": "1280x720"}[ratio]

    def _generate_openai(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        if request.mode == "video_to_video":
            raise VideoGenerationError("OpenAI adapter currently supports text/image reference generation, not video-to-video")
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise VideoGenerationError("OPENAI_API_KEY is not configured")
        model = os.getenv("AURA_OPENAI_VIDEO_MODEL", "sora-2")
        result = self._base_result(request, "openai", model)
        payload: dict[str, Any] = {
            "model": model,
            "prompt": request.prompt,
            "seconds": min((4, 8, 12), key=lambda x: abs(x - request.duration_seconds)),
            "size": self._openai_size(request.aspect_ratio),
        }
        if request.reference_url:
            payload["input_reference"] = {"image_url": request.reference_url}
        response = requests.post(
            "https://api.openai.com/v1/videos",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        if response.status_code >= 300:
            raise VideoGenerationError(f"OpenAI video request failed ({response.status_code}): {response.text[:500]}")
        data = response.json()
        result.provider_job_id = data.get("id")
        result.status = data.get("status") or "submitted"
        return result

    @staticmethod
    def _runway_ratio(ratio: str) -> str:
        return {"9:16": "720:1280", "16:9": "1280:720", "1:1": "1280:720"}[ratio]

    def _generate_runway(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        api_key = os.getenv("RUNWAYML_API_SECRET")
        if not api_key:
            raise VideoGenerationError("RUNWAYML_API_SECRET is not configured")
        model = os.getenv("AURA_RUNWAY_VIDEO_MODEL", "gen4.5")
        result = self._base_result(request, "runway", model)
        endpoint = "text_to_video"
        payload: dict[str, Any] = {
            "model": model,
            "promptText": request.prompt,
            "ratio": self._runway_ratio(request.aspect_ratio),
            "duration": request.duration_seconds,
        }
        if request.negative_prompt:
            payload["negativePrompt"] = request.negative_prompt
        if request.mode == "image_to_video":
            endpoint = "image_to_video"
            payload["promptImage"] = request.reference_url
        elif request.mode == "video_to_video":
            endpoint = "video_to_video"
            payload["videoUri"] = request.reference_url
        response = requests.post(
            f"https://api.dev.runwayml.com/v1/{endpoint}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Runway-Version": "2024-11-06",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        if response.status_code >= 300:
            raise VideoGenerationError(f"Runway video request failed ({response.status_code}): {response.text[:500]}")
        data = response.json()
        result.provider_job_id = data.get("id")
        result.status = "submitted"
        return result

    @staticmethod
    def provenance_hash(result: VideoGenerationResult) -> str:
        payload = json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
