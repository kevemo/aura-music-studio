from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .live_translation import AuraLiveTranslationError, AuraLiveTranslator

router = APIRouter(prefix="/speech/live-translate", tags=["Aura Live Translator"])
translator = AuraLiveTranslator()


class StartLiveTranslationBody(BaseModel):
    source_locale: str = Field(default="auto", max_length=64)
    target_locale: str = Field(default="en", min_length=2, max_length=64)


class TranslateTextBody(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    source_locale: str = Field(default="auto", max_length=64)
    target_locale: str = Field(default="en", min_length=2, max_length=64)


def _delete(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


@router.get("/capabilities")
def capabilities():
    return translator.capabilities()


@router.post("/sessions")
def create_session(body: StartLiveTranslationBody):
    try:
        return translator.session_manifest(
            source_locale=body.source_locale,
            target_locale=body.target_locale,
        )
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/text")
def translate_text(body: TranslateTextBody):
    try:
        return translator.translate_text(
            body.text,
            source_locale=body.source_locale,
            target_locale=body.target_locale,
        )
    except AuraLiveTranslationError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/chunk")
async def translate_audio_chunk(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    target_locale: str = Form("en"),
    source_locale: str = Form("auto"),
    session_id: str = Form(""),
    sequence: int = Form(0),
    speak_translation: bool = Form(True),
):
    suffix = Path(audio.filename or "speech.webm").suffix or ".webm"
    fd, source_name = tempfile.mkstemp(prefix="aura-live-in-", suffix=suffix)
    os.close(fd)
    source = Path(source_name)
    speech_out: Path | None = None
    try:
        with source.open("wb") as handle:
            while chunk := await audio.read(1024 * 1024):
                handle.write(chunk)
        if source.stat().st_size > int(os.getenv("AURA_LIVE_TRANSLATE_MAX_CHUNK_BYTES", str(8 * 1024 * 1024))):
            raise AuraLiveTranslationError("Live translation audio segment is too large")
        if speak_translation:
            fd, speech_name = tempfile.mkstemp(prefix="aura-live-out-", suffix=".wav")
            os.close(fd)
            speech_out = Path(speech_name)
            speech_out.unlink(missing_ok=True)
        result = translator.translate_audio_segment(
            source,
            target_locale=target_locale,
            source_locale=source_locale,
            session_id=session_id or None,
            sequence=sequence,
            speak_translation=speak_translation,
            output_path=speech_out,
        )
        payload = result.to_dict()
        # Audio is downloaded through a separate endpoint contract in production; this chunk
        # endpoint returns captions immediately and signals whether a spoken rendering succeeded.
        payload["spoken_translation_available"] = bool(result.speech_file)
        if result.speech_file:
            payload["speech_token"] = Path(result.speech_file).name
        return payload
    except AuraLiveTranslationError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        source.unlink(missing_ok=True)
        # The generated speech file is intentionally retained briefly for /audio below.


@router.get("/audio/{speech_token}")
def translated_audio(speech_token: str, background_tasks: BackgroundTasks):
    safe = Path(speech_token).name
    if safe != speech_token or not safe.startswith("aura-live-out-") or not safe.endswith(".wav"):
        raise HTTPException(404, "Translated speech is unavailable")
    path = Path(tempfile.gettempdir()) / safe
    if not path.is_file():
        raise HTTPException(404, "Translated speech is unavailable or has expired")
    background_tasks.add_task(_delete, str(path))
    return FileResponse(
        path,
        media_type="audio/wav",
        filename="Aura_Translated_Speech.wav",
        background=background_tasks,
    )
