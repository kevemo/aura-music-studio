from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .aura_chat_store import AuraChatStore
from .speech import AuraSpeechService

router = APIRouter(tags=["Aura Multimodal"])
store = AuraChatStore()


class AttachmentAnalysisRequest(BaseModel):
    instruction: str = Field(default="Describe and analyse this attachment for use in the current conversation.", max_length=4000)


class AuraVisionService:
    """Local-first vision adapter for Aura chat attachments.

    Vision data stays on the configured local model host. The default adapter uses Ollama's
    chat API with message images. If no vision model is configured, Aura fails truthfully
    instead of pretending it can see the attachment.
    """

    def __init__(self):
        self.base_url = (os.getenv("OLLAMA_BASE_URL") or "").strip().rstrip("/")
        self.model = (os.getenv("AURA_VISION_MODEL") or "").strip()
        self.timeout = max(30, int(os.getenv("AURA_VISION_TIMEOUT", "180")))
        self.max_image_bytes = max(1, min(50, int(os.getenv("AURA_VISION_MAX_IMAGE_MB", "12")))) * 1024 * 1024

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    def analyze_images(self, images: list[Path], instruction: str) -> str:
        if not self.configured:
            raise RuntimeError("Aura vision is not configured. Set AURA_VISION_MODEL on the local Ollama host.")
        encoded: list[str] = []
        for path in images[:8]:
            if not path.is_file():
                continue
            if path.stat().st_size > self.max_image_bytes:
                raise ValueError(f"Vision image {path.name} exceeds the configured maximum size")
            encoded.append(base64.b64encode(path.read_bytes()).decode("ascii"))
        if not encoded:
            raise ValueError("No readable image frames were supplied to Aura vision")
        response = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are Aura's visual perception module. Describe only what is supportable from the supplied image(s). "
                            "Be precise about text, layout, objects, visual defects, composition and creative-edit opportunities when relevant. "
                            "Do not infer a real person's identity from appearance."
                        ),
                    },
                    {"role": "user", "content": instruction, "images": encoded},
                ],
                "options": {"temperature": 0.15},
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        text = str((response.json().get("message") or {}).get("content") or "").strip()
        if not text:
            raise RuntimeError("Aura vision model returned an empty analysis")
        return text[:80000]

    def diagnostics(self) -> dict:
        return {
            "configured": self.configured,
            "provider": "ollama_local",
            "model": self.model or None,
            "max_image_bytes": self.max_image_bytes,
        }


def _safe_attachment(member_id: str, thread_id: str, attachment_id: str) -> tuple[dict, Path]:
    item = store.attachment(member_id, thread_id, attachment_id)
    if not item:
        raise HTTPException(404, "Aura attachment not found")
    path = Path(str(item.get("stored_path") or "")).resolve()
    root = Path(os.getenv("AURA_CHAT_ATTACHMENT_DIR", "data/aura/attachments")).resolve()
    expected = (root / member_id / thread_id).resolve()
    if root not in expected.parents or (path != expected and expected not in path.parents):
        raise HTTPException(400, "Invalid Aura attachment path")
    if not path.is_file():
        raise HTTPException(404, "Aura attachment file is missing")
    return item, path


def _update_analysis(user_id: str, thread_id: str, attachment_id: str, *, text: str | None, metadata: dict) -> dict:
    with store._connect() as con:
        row = con.execute(
            "SELECT metadata_json FROM aura_chat_attachments WHERE id=? AND user_id=? AND thread_id=?",
            (attachment_id, user_id, thread_id),
        ).fetchone()
        if not row:
            raise KeyError(attachment_id)
        try:
            existing = json.loads(row["metadata_json"] or "{}")
        except Exception:
            existing = {}
        existing.update(metadata)
        con.execute(
            "UPDATE aura_chat_attachments SET extracted_text=COALESCE(?,extracted_text),metadata_json=? WHERE id=? AND user_id=? AND thread_id=?",
            (text, json.dumps(existing, ensure_ascii=False, default=str), attachment_id, user_id, thread_id),
        )
    return store.attachment(user_id, thread_id, attachment_id) or {}


def _sample_video(path: Path, work: Path) -> list[Path]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("ffmpeg/ffprobe are required for Aura video perception")
    probe = subprocess.run(
        [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    payload = json.loads(probe.stdout or "{}")
    duration = float((payload.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        times = [0.0]
    else:
        times = sorted({max(0.0, duration * ratio) for ratio in (0.08, 0.28, 0.5, 0.72, 0.92)})
    frames = []
    for index, second in enumerate(times, 1):
        target = work / f"frame_{index:02d}.jpg"
        subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{second:.3f}",
                "-i", str(path), "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", "-q:v", "3", str(target),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        if target.is_file():
            frames.append(target)
    return frames


def _audio_transcript(path: Path) -> str:
    return AuraSpeechService().transcribe(path).strip()


@router.get("/aura-intelligence/api/multimodal-status")
def multimodal_status(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    speech = AuraSpeechService().diagnostics()
    return {
        "vision": AuraVisionService().diagnostics(),
        "audio_transcription": bool(
            speech.get("stt_command_configured")
            or (speech.get("whisper_cli") and speech.get("whisper_model_configured"))
        ),
        "video_frame_sampling": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
        "privacy": "Local-first attachment perception; private chat files are not exposed as public URLs.",
    }


@router.post("/aura-intelligence/api/threads/{thread_id}/attachments/{attachment_id}/analyze")
def analyze_attachment(thread_id: str, attachment_id: str, body: AttachmentAnalysisRequest, request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not store.thread(member.user_id, thread_id):
        raise HTTPException(404, "Aura conversation not found")
    item, path = _safe_attachment(member.user_id, thread_id, attachment_id)
    kind = str(item.get("kind") or "")
    instruction = (body.instruction or "Describe and analyse this attachment.").strip()
    try:
        if kind == "image":
            text = AuraVisionService().analyze_images([path], instruction)
            updated = _update_analysis(
                member.user_id, thread_id, attachment_id,
                text="Aura visual analysis:\n" + text,
                metadata={"vision_analyzed": True, "vision_model": AuraVisionService().model},
            )
            return {"attachment": {k: v for k, v in updated.items() if k != "stored_path"}, "analysis": text, "mode": "vision"}

        if kind == "audio":
            transcript = _audio_transcript(path)
            updated = _update_analysis(
                member.user_id, thread_id, attachment_id,
                text="Aura audio transcript:\n" + transcript,
                metadata={"audio_transcribed": True},
            )
            return {"attachment": {k: v for k, v in updated.items() if k != "stored_path"}, "analysis": transcript, "mode": "transcript"}

        if kind == "video":
            with tempfile.TemporaryDirectory(prefix="aura-video-perception-") as tmp:
                frames = _sample_video(path, Path(tmp))
                description = AuraVisionService().analyze_images(
                    frames,
                    instruction
                    + "\nThese are chronological sampled frames from one video. Describe the visual sequence, continuity, composition, text and edit opportunities. State that this is sampled-frame analysis, not frame-perfect review.",
                )
            text = "Aura sampled-frame video analysis:\n" + description
            updated = _update_analysis(
                member.user_id, thread_id, attachment_id,
                text=text,
                metadata={"video_vision_analyzed": True, "sampled_frames": len(frames), "vision_model": AuraVisionService().model},
            )
            return {"attachment": {k: v for k, v in updated.items() if k != "stored_path"}, "analysis": description, "mode": "sampled_video_vision"}

        extracted = str(item.get("extracted_text") or "").strip()
        if extracted:
            return {"analysis": extracted, "mode": "document_text", "already_extracted": True}
        raise RuntimeError("This attachment does not have an additional multimodal analyzer")
    except (RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        raise HTTPException(503, str(exc)) from exc
