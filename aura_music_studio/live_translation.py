from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .localization import AuraTranslationService, LocalizationError, normalize_locale
from .speech import AuraSpeechService


class AuraLiveTranslationError(RuntimeError):
    pass


@dataclass
class LiveTranslationResult:
    session_id: str
    sequence: int
    source_locale: str
    target_locale: str
    transcript: str
    translation: str
    speech_file: str | None
    provider: str

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "sequence": self.sequence,
            "source_locale": self.source_locale,
            "target_locale": self.target_locale,
            "transcript": self.transcript,
            "translation": self.translation,
            "speech_file": self.speech_file,
            "provider": self.provider,
        }


class AuraLiveTranslator:
    """Low-latency segmented interpreter with a pluggable true-realtime path.

    Browsers can send short microphone segments in sequence. Each segment is transcribed,
    translated, and optionally spoken in the target locale. A deployment may later replace
    the segmented path with a dedicated streaming bridge (for example a realtime translation
    model) without changing the public session contract.
    """

    def __init__(self):
        self.speech = AuraSpeechService()
        self.translation = AuraTranslationService()
        self.realtime_bridge = (os.getenv("AURA_LIVE_TRANSLATE_URL") or "").strip()
        self.realtime_model = os.getenv("AURA_LIVE_TRANSLATE_MODEL", "gpt-realtime-translate")

    @staticmethod
    def new_session_id() -> str:
        return uuid4().hex

    def capabilities(self) -> dict:
        return {
            "live_translation": True,
            "two_way_interpreter": True,
            "bilingual_captions": True,
            "speech_to_text_to_translation_to_speech": True,
            "segmented_microphone_fallback": True,
            "realtime_bridge_configured": bool(self.realtime_bridge),
            "realtime_model": self.realtime_model if os.getenv("OPENAI_API_KEY") else None,
            "source_language": "automatic or explicitly selected",
            "target_language": "any locale exposed by the Studio language catalogue, subject to provider speech coverage",
            "note": (
                "Text translation and spoken output are separate capabilities. If a TTS voice/model is unavailable "
                "for a selected locale, Aura still returns the translated captions instead of pretending speech succeeded."
            ),
        }

    def translate_text(
        self,
        text: str,
        *,
        target_locale: str,
        source_locale: str = "auto",
    ) -> dict:
        target = normalize_locale(target_locale)
        source = "auto" if source_locale == "auto" else normalize_locale(source_locale)
        try:
            translated = self.translation.translate(target, [text])
        except LocalizationError as exc:
            raise AuraLiveTranslationError(str(exc)) from exc
        return {
            "source_locale": source,
            "target_locale": target,
            "source_text": text,
            "translated_text": translated["translations"][0],
            "provider": translated.get("provider"),
            "translated": translated.get("translated", False),
        }

    def translate_audio_segment(
        self,
        audio_path: str | Path,
        *,
        target_locale: str,
        source_locale: str = "auto",
        session_id: str | None = None,
        sequence: int = 0,
        speak_translation: bool = True,
        output_path: str | Path | None = None,
    ) -> LiveTranslationResult:
        source_path = Path(audio_path)
        if not source_path.is_file():
            raise AuraLiveTranslationError("Live translation audio segment was not found")
        target = normalize_locale(target_locale)
        source = "auto" if source_locale == "auto" else normalize_locale(source_locale)
        try:
            transcript = self.speech.transcribe(source_path).strip()
        except Exception as exc:
            raise AuraLiveTranslationError(f"Aura could not transcribe this audio segment: {exc}") from exc
        if not transcript:
            raise AuraLiveTranslationError("No speech was detected in this audio segment")

        translated = self.translate_text(transcript, target_locale=target, source_locale=source)
        translation = str(translated["translated_text"])
        speech_file: str | None = None
        if speak_translation:
            if output_path is None:
                fd, filename = tempfile.mkstemp(prefix="aura-translate-", suffix=".wav")
                os.close(fd)
                Path(filename).unlink(missing_ok=True)
                output_path = filename
            try:
                speech_file = str(self.speech.speak(translation, output_path, locale=target))
            except Exception:
                # Translation/captions remain useful even when the selected TTS backend cannot voice this locale.
                speech_file = None

        return LiveTranslationResult(
            session_id=session_id or self.new_session_id(),
            sequence=max(0, int(sequence)),
            source_locale=source,
            target_locale=target,
            transcript=transcript,
            translation=translation,
            speech_file=speech_file,
            provider=str(translated.get("provider") or "unknown"),
        )

    def session_manifest(self, *, source_locale: str, target_locale: str) -> dict:
        source = "auto" if source_locale == "auto" else normalize_locale(source_locale)
        target = normalize_locale(target_locale)
        return {
            "session_id": self.new_session_id(),
            "source_locale": source,
            "target_locale": target,
            "mode": "two_way_live_interpreter",
            "segment_seconds_recommended": float(os.getenv("AURA_LIVE_TRANSLATE_SEGMENT_SECONDS", "2.5")),
            "realtime_bridge_configured": bool(self.realtime_bridge),
            "realtime_model": self.realtime_model,
        }
