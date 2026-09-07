from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .producer import llm_plan
from .speech import AuraSpeechService

# AuraSpeechService remains an internal compatibility identifier. Public product/API language is
# Rhiannon; the service implementation is migrated incrementally without creating a competing
# speech stack.
router = APIRouter(prefix="/speech", tags=["Rhiannon Voice"])


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class TextCommandRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    project_name: str | None = None


def _delete(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


@router.post("/text-command")
def text_command(request: TextCommandRequest):
    plan = llm_plan(request.text, session_summary={"project": request.project_name} if request.project_name else None)
    spoken = AuraSpeechService._spoken_summary(plan)
    return {"transcript": request.text, "plan": plan.model_dump(), "spoken_text": spoken}


@router.post("/synthesize")
def synthesize(request: SpeakRequest, background_tasks: BackgroundTasks):
    fd, filename = tempfile.mkstemp(prefix="rhiannon-reply-", suffix=".wav")
    os.close(fd)
    target = Path(filename)
    try:
        AuraSpeechService().speak(request.text, target)
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(503, f"Rhiannon speech synthesis is unavailable: {type(exc).__name__}: {exc}") from exc
    background_tasks.add_task(_delete, str(target))
    return FileResponse(
        target,
        media_type="audio/wav",
        filename="Rhiannon_Reply.wav",
        background=background_tasks,
    )
