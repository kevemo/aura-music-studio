from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VideoEngineSpec:
    id: str
    name: str
    env_command: str
    role: str
    license_note: str


VIDEO_ENGINES = (
    VideoEngineSpec(
        "wan22",
        "Wan 2.2",
        "AURA_WAN_VIDEO_CMD",
        "Primary optional self-hosted text/image/audio-driven cinematic scene generator.",
        "Wan 2.2 code/models are Apache-2.0 upstream; track the exact downloaded checkpoint in ESP's model ledger.",
    ),
    VideoEngineSpec(
        "mova",
        "MOVA",
        "AURA_MOVA_VIDEO_CMD",
        "Optional synchronized video+audio and lip-sync research renderer.",
        "Review the exact OpenMOSS MOVA code/model licence before enabling for paid member outputs.",
    ),
    VideoEngineSpec(
        "ltx2",
        "LTX-2.x",
        "AURA_LTX_VIDEO_CMD",
        "Optional cinematic text/image-to-video renderer.",
        "LTX-2.x uses a community licence with commercial conditions; deployment approval must be explicit.",
    ),
)


def _command_ready(env_name: str) -> bool:
    raw = (os.getenv(env_name) or "").strip()
    if not raw:
        return False
    try:
        parts = shlex.split(raw)
    except ValueError:
        return False
    return bool(parts and (shutil.which(parts[0]) or Path(parts[0]).is_file()))


def public_video_engine_status() -> list[dict]:
    """Member-safe engine readiness. Commands/paths are never returned."""
    return [
        {
            "id": spec.id,
            "name": spec.name,
            "configured": _command_ready(spec.env_command),
            "role": spec.role,
            "license_review_required": True,
            "command_exposed": False,
        }
        for spec in VIDEO_ENGINES
    ]


def _validate_video(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size < 4096:
        raise RuntimeError("Video renderer did not create a usable output file")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"valid": True, "ffprobe": False}
    import json

    proc = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-show_entries", "stream=codec_type,width,height", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    payload = json.loads(proc.stdout)
    streams = payload.get("streams") or []
    video = next((x for x in streams if x.get("codec_type") == "video"), None)
    if not video:
        raise RuntimeError("Neural renderer output contains no video stream")
    duration = float((payload.get("format") or {}).get("duration") or 0.0)
    if duration <= 0:
        raise RuntimeError("Neural renderer output has zero duration")
    return {
        "valid": True,
        "ffprobe": True,
        "duration_seconds": duration,
        "width": video.get("width"),
        "height": video.get("height"),
    }


def render_neural_scene(
    engine_id: str,
    *,
    prompt: str,
    output: str | Path,
    duration_seconds: float,
    width: int,
    height: int,
    fps: int,
    image: str | Path | None = None,
    audio: str | Path | None = None,
    negative_prompt: str = "",
    seed: int = -1,
) -> tuple[Path, dict]:
    """Invoke an owner-approved local video engine through a stable ESP environment contract."""
    spec = next((item for item in VIDEO_ENGINES if item.id == engine_id), None)
    if not spec:
        raise ValueError(f"Unknown video engine: {engine_id}")
    raw = (os.getenv(spec.env_command) or "").strip()
    if not raw or not _command_ready(spec.env_command):
        raise RuntimeError(f"{spec.name} is not installed/configured on this ESP node")

    target = Path(output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "AURA_VIDEO_PROMPT": prompt[:8000],
        "AURA_VIDEO_NEGATIVE_PROMPT": negative_prompt[:4000],
        "AURA_VIDEO_IMAGE": str(Path(image).resolve()) if image else "",
        "AURA_VIDEO_AUDIO": str(Path(audio).resolve()) if audio else "",
        "AURA_VIDEO_OUTPUT": str(target),
        "AURA_VIDEO_DURATION": str(float(duration_seconds)),
        "AURA_VIDEO_WIDTH": str(int(width)),
        "AURA_VIDEO_HEIGHT": str(int(height)),
        "AURA_VIDEO_FPS": str(int(fps)),
        "AURA_VIDEO_SEED": str(int(seed)),
        "AURA_VIDEO_ENGINE": spec.id,
    })
    subprocess.run(
        shlex.split(raw),
        env=env,
        check=True,
        timeout=int(os.getenv("AURA_VIDEO_RENDER_TIMEOUT", "7200")),
    )
    report = _validate_video(target)
    report.update({"engine": spec.id, "engine_name": spec.name})
    return target, report
