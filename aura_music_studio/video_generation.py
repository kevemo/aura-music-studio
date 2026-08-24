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
    """Model-agnostic real video generation for The Live Sound Studio.

    Auto-provider order:
    1. configured self-hosted/open model renderer
    2. OpenAI video generation
    3. Runway

    Local renderer contracts support a general adapter plus explicit LTX-2, Wan 2.2,
    HunyuanVideo and CogVideoX commands. The service never fabricates successful output,
    never silently changes an aspect ratio, and stores remote results locally after completion.
    """

    VALID_MODES = {"text_to_video", "image_to_video", "video_to_video"}
    VALID_RATIOS = {"9:16", "16:9", "1:1"}
    LOCAL_RENDERERS = (
        ("custom", "AURA_VIDEO_RENDER_CMD", "AURA_VIDEO_MODEL", "local-video-renderer"),
        ("ltx2", "AURA_LTX2_CMD", "AURA_LTX2_MODEL", "LTX-2.3"),
        ("wan22", "AURA_WAN22_CMD", "AURA_WAN22_MODEL", "Wan2.2"),
        ("hunyuanvideo", "AURA_HUNYUANVIDEO_CMD", "AURA_HUNYUANVIDEO_MODEL", "HunyuanVideo"),
        ("cogvideox", "AURA_COGVIDEOX_CMD", "AURA_COGVIDEOX_MODEL", "CogVideoX"),
    )

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
            except Exception as exc:
                errors.append(f"{provider}: {exc}")
        raise VideoGenerationError("No video renderer succeeded. " + " | ".join(errors))

    def refresh(self, *, result_id: str, provider: str, provider_job_id: str | None) -> dict[str, Any]:
        provider = (provider or "").strip().lower()
        if provider == "local":
            output = self.output_root / f"{result_id}.mp4"
            return {
                "status": "completed" if output.exists() else "failed",
                "output_path": str(output) if output.exists() else None,
                "error": None if output.exists() else "Local render output was not found",
            }
        if not provider_job_id:
            raise VideoGenerationError("This video job has no provider task ID")
        if provider == "openai":
            return self._refresh_openai(result_id, provider_job_id)
        if provider == "runway":
            return self._refresh_runway(result_id, provider_job_id)
        raise VideoGenerationError(f"Unknown video provider: {provider}")

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

    def _configured_local_renderer(self) -> tuple[str, str, str] | None:
        for name, command_env, model_env, default_model in self.LOCAL_RENDERERS:
            command = (os.getenv(command_env) or "").strip()
            if command:
                return name, command, os.getenv(model_env, default_model)
        return None

    def _provider_order(self, requested: str) -> list[str]:
        requested = (requested or "auto").strip().lower()
        if requested != "auto":
            if requested not in {"local", "openai", "runway"}:
                raise VideoGenerationError(f"Unknown video provider: {requested}")
            return [requested]
        order: list[str] = []
        if self._configured_local_renderer():
            order.append("local")
        if os.getenv("OPENAI_API_KEY"):
            order.append("openai")
        if os.getenv("RUNWAYML_API_SECRET"):
            order.append("runway")
        if not order:
            raise VideoGenerationError(
                "No real video provider is configured. Configure a local video renderer, OPENAI_API_KEY, or RUNWAYML_API_SECRET."
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
        configured = self._configured_local_renderer()
        if not configured:
            raise VideoGenerationError("No self-hosted video renderer command is configured")
        engine, command, model = configured
        result = self._base_result(request, "local", model)
        output = self.output_root / f"{result.id}.mp4"
        env = os.environ.copy()
        env.update(
            {
                "AURA_VIDEO_ENGINE": engine,
                "AURA_VIDEO_MODEL": model,
                "AURA_VIDEO_PROMPT": request.prompt,
                "AURA_VIDEO_MODE": request.mode,
                "AURA_VIDEO_RATIO": request.aspect_ratio,
                "AURA_VIDEO_DURATION": str(request.duration_seconds),
                "AURA_VIDEO_OUTPUT": str(output.resolve()),
                "AURA_VIDEO_REFERENCE": request.reference_url or "",
                "AURA_VIDEO_NEGATIVE_PROMPT": request.negative_prompt or "",
                "AURA_VIDEO_PROJECT_ID": request.project_id or "",
                "AURA_VIDEO_QUALITY": request.quality,
                "AURA_VIDEO_TARGET_PLATFORM": request.target_platform or "",
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
            raise VideoGenerationError(completed.stderr.strip() or f"{engine} video renderer failed")
        if not output.exists() or output.stat().st_size < 1024:
            raise VideoGenerationError(f"{engine} video renderer did not produce a valid MP4 output")
        result.status = "completed"
        result.output_path = str(output)
        return result

    @staticmethod
    def _openai_size(ratio: str, quality: str) -> str:
        if ratio == "1:1":
            raise VideoGenerationError("OpenAI video currently exposes portrait and landscape sizes, not native square output")
        high = quality.strip().lower() in {"high", "professional", "pro"}
        if ratio == "9:16":
            return "1024x1792" if high else "720x1280"
        return "1792x1024" if high else "1280x720"

    def _generate_openai(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        if request.mode == "video_to_video":
            raise VideoGenerationError("OpenAI video-to-video is not enabled in this adapter; use Runway or a configured local renderer")
        if request.duration_seconds > 12:
            raise VideoGenerationError("OpenAI single-shot video generation adapter supports up to 12 seconds")
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise VideoGenerationError("OPENAI_API_KEY is not configured")
        model = os.getenv(
            "AURA_OPENAI_VIDEO_PRO_MODEL" if request.quality.strip().lower() != "standard" else "AURA_OPENAI_VIDEO_MODEL",
            "sora-2-pro" if request.quality.strip().lower() != "standard" else "sora-2",
        )
        result = self._base_result(request, "openai", model)
        payload: dict[str, Any] = {
            "model": model,
            "prompt": request.prompt + (f"\nAvoid: {request.negative_prompt}" if request.negative_prompt else ""),
            "seconds": min((4, 8, 12), key=lambda x: abs(x - request.duration_seconds)),
            "size": self._openai_size(request.aspect_ratio, request.quality),
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
        if not result.provider_job_id:
            raise VideoGenerationError("OpenAI video request returned no job id")
        result.status = data.get("status") or "submitted"
        return result

    def _refresh_openai(self, result_id: str, provider_job_id: str) -> dict[str, Any]:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise VideoGenerationError("OPENAI_API_KEY is not configured")
        headers = {"Authorization": f"Bearer {api_key}"}
        response = requests.get(f"https://api.openai.com/v1/videos/{provider_job_id}", headers=headers, timeout=60)
        if response.status_code >= 300:
            raise VideoGenerationError(f"OpenAI video status failed ({response.status_code}): {response.text[:500]}")
        data = response.json()
        status = data.get("status") or "in_progress"
        if status != "completed":
            error = data.get("error")
            return {"status": status, "error": error.get("message") if isinstance(error, dict) else error}
        content = requests.get(
            f"https://api.openai.com/v1/videos/{provider_job_id}/content",
            headers=headers,
            stream=True,
            timeout=300,
        )
        if content.status_code >= 300:
            raise VideoGenerationError(f"OpenAI video download failed ({content.status_code})")
        output = self.output_root / f"{result_id}.mp4"
        self._write_stream(content, output)
        return {"status": "completed", "output_path": str(output), "output_url": None, "error": None}

    @staticmethod
    def _runway_ratio(request: VideoGenerationRequest) -> str | None:
        if request.mode == "video_to_video":
            # Aleph 2.0 preserves source resolution; omitting ratio avoids falsely promising a crop.
            return None
        if request.mode == "text_to_video":
            if request.aspect_ratio == "1:1":
                raise VideoGenerationError("Runway Gen-4.5 text-to-video does not expose native square output")
            return {"9:16": "720:1280", "16:9": "1280:720"}[request.aspect_ratio]
        return {"9:16": "720:1280", "16:9": "1280:720", "1:1": "960:960"}[request.aspect_ratio]

    def _generate_runway(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        api_key = os.getenv("RUNWAYML_API_SECRET")
        if not api_key:
            raise VideoGenerationError("RUNWAYML_API_SECRET is not configured")
        if request.mode == "video_to_video":
            model = os.getenv("AURA_RUNWAY_VIDEO_TO_VIDEO_MODEL", "aleph2")
            if request.duration_seconds < 2 or request.duration_seconds > 30:
                raise VideoGenerationError("Runway Aleph 2.0 video-to-video expects a 2–30 second source clip")
        else:
            model = os.getenv("AURA_RUNWAY_VIDEO_MODEL", "gen4.5")
        result = self._base_result(request, "runway", model)
        endpoint = "text_to_video"
        prompt = request.prompt
        if request.negative_prompt:
            prompt += f"\nAvoid these visual elements: {request.negative_prompt}"
        payload: dict[str, Any] = {"model": model, "promptText": prompt[:1000]}
        ratio = self._runway_ratio(request)
        if ratio:
            payload["ratio"] = ratio

        if request.mode == "image_to_video":
            endpoint = "image_to_video"
            payload["promptImage"] = request.reference_url
            payload["duration"] = request.duration_seconds
        elif request.mode == "video_to_video":
            endpoint = "video_to_video"
            payload["videoUri"] = request.reference_url
        else:
            payload["duration"] = request.duration_seconds

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
        if not result.provider_job_id:
            raise VideoGenerationError("Runway video request returned no task id")
        result.status = "submitted"
        return result

    def _refresh_runway(self, result_id: str, provider_job_id: str) -> dict[str, Any]:
        api_key = os.getenv("RUNWAYML_API_SECRET")
        if not api_key:
            raise VideoGenerationError("RUNWAYML_API_SECRET is not configured")
        response = requests.get(
            f"https://api.dev.runwayml.com/v1/tasks/{provider_job_id}",
            headers={"Authorization": f"Bearer {api_key}", "X-Runway-Version": "2024-11-06"},
            timeout=60,
        )
        if response.status_code >= 300:
            raise VideoGenerationError(f"Runway task status failed ({response.status_code}): {response.text[:500]}")
        data = response.json()
        raw = (data.get("status") or "PENDING").upper()
        status_map = {
            "PENDING": "queued",
            "THROTTLED": "queued",
            "RUNNING": "in_progress",
            "SUCCEEDED": "completed",
            "FAILED": "failed",
            "CANCELLED": "failed",
        }
        status = status_map.get(raw, raw.lower())
        if status != "completed":
            return {"status": status, "error": data.get("failure") or data.get("failureCode")}
        outputs = data.get("output") or []
        if not outputs:
            raise VideoGenerationError("Runway completed without an output URL")
        remote_url = outputs[0]
        download = requests.get(remote_url, stream=True, timeout=300)
        if download.status_code >= 300:
            raise VideoGenerationError(f"Runway output download failed ({download.status_code})")
        output = self.output_root / f"{result_id}.mp4"
        self._write_stream(download, output)
        return {"status": "completed", "output_path": str(output), "output_url": None, "error": None}

    @staticmethod
    def _write_stream(response: requests.Response, output: Path) -> None:
        max_bytes = int(os.getenv("AURA_VIDEO_MAX_DOWNLOAD_BYTES", str(1024 * 1024 * 1024)))
        total = 0
        with output.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    handle.close()
                    output.unlink(missing_ok=True)
                    raise VideoGenerationError("Generated video exceeded the configured download size limit")
                handle.write(chunk)
        if not output.exists() or output.stat().st_size < 1024:
            output.unlink(missing_ok=True)
            raise VideoGenerationError("Downloaded video output was empty or invalid")

    @staticmethod
    def provenance_hash(result: VideoGenerationResult) -> str:
        payload = json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
