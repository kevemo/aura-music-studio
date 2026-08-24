from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .localization import LocalePreferenceStore, LocalizationError, normalize_locale
from .plans import AURA_SPEECH
from .speech import AuraSpeechService

router = APIRouter(prefix="/api/aura/voice", tags=["Aura Workpage Voice"])
speech = AuraSpeechService()
locales = LocalePreferenceStore(os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3")


class SpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    locale: str | None = Field(default=None, max_length=64)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(AURA_SPEECH):
        raise HTTPException(403, "Aura speech is not available on this membership")
    return member


def _delete(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


@router.post("/speak")
def speak_reply(body: SpeakBody, request: Request, background_tasks: BackgroundTasks):
    member = _member(request)
    raw_locale = body.locale or locales.get_user_locale(member.user_id) or "en"
    try:
        locale = normalize_locale(raw_locale)
    except LocalizationError as exc:
        raise HTTPException(422, str(exc)) from exc

    fd, filename = tempfile.mkstemp(prefix="aura-workpage-reply-", suffix=".wav")
    os.close(fd)
    target = Path(filename)
    target.unlink(missing_ok=True)
    try:
        speech.speak(body.text, target, locale=locale)
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(503, f"Aura cannot speak this locale with the configured voice system: {exc}") from exc
    background_tasks.add_task(_delete, str(target))
    return FileResponse(
        target,
        media_type="audio/wav",
        filename=f"Aura_{locale}_Reply.wav",
        background=background_tasks,
    )
