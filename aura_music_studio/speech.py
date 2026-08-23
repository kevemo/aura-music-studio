from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import requests

from .producer import ProducerPlan, llm_plan


@dataclass
class SpeechResult:
    transcript: str
    plan: ProducerPlan
    spoken_text: str
    speech_file: str | None = None


class AuraSpeechService:
    """Offline-first multilingual speech interface for Aura.

    The preferred standalone path is whisper.cpp for STT plus a locally hosted TTS engine.
    Browser microphone formats such as WebM/Opus are converted to mono 16 kHz WAV through
    local ffmpeg before transcription. Locale is passed to locale-aware TTS backends rather
    than silently forcing English pronunciation.

    Capable TTS adapters also receive Aura's stable vocal direction (voice/style/rate/pitch)
    so her spoken presence remains warm, grounded and measured instead of falling back to a
    provider's arbitrary default voice. Backends that do not support these fields may ignore them.
    """

    def __init__(self):
        self.stt_cmd = (os.getenv("AURA_STT_CMD") or "").strip()
        self.tts_cmd = (os.getenv("AURA_TTS_CMD") or "").strip()
        self.tts_url = (os.getenv("AURA_TTS_URL") or "").rstrip("/")
        self.whisper_model = (os.getenv("AURA_WHISPER_MODEL") or "").strip()
        self.piper_model = (os.getenv("AURA_PIPER_MODEL") or "").strip()
        self.tts_voice = (os.getenv("AURA_TTS_VOICE") or "Aura").strip()
        self.tts_style = (
            os.getenv("AURA_TTS_STYLE")
            or "warm, resonant, grounded, measured, calm, compassionate, confident; deliberate pauses; no rushed delivery"
        ).strip()
        self.tts_rate = self._float_env("AURA_TTS_RATE", 0.94, 0.6, 1.35)
        self.tts_pitch = self._float_env("AURA_TTS_PITCH", 0.0, -12.0, 12.0)

    @staticmethod
    def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
        try:
            value = float(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _run_template(template: str, values: dict[str, str]) -> subprocess.CompletedProcess:
        rendered = template
        for key, value in values.items():
            rendered = rendered.replace("{" + key + "}", shlex.quote(value))
        return subprocess.run(rendered, shell=True, check=True, capture_output=True, text=True)

    @staticmethod
    def _prepare_audio(source: Path, work_dir: Path) -> Path:
        if source.suffix.lower() in {".wav", ".wave"}:
            return source
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg is required to transcode browser microphone audio for offline STT")
        target = work_dir / "speech-input.wav"
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(target),
            ],
            check=True,
            capture_output=True,
        )
        return target

    def transcribe(self, audio_path: str | Path) -> str:
        source = Path(audio_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)

        with tempfile.TemporaryDirectory(prefix="aura-speech-prep-") as prep_tmp:
            prepared = self._prepare_audio(source, Path(prep_tmp))

            if self.stt_cmd:
                with tempfile.TemporaryDirectory(prefix="aura-stt-") as tmp:
                    out = Path(tmp) / "transcript.txt"
                    result = self._run_template(self.stt_cmd, {"input": str(prepared), "output": str(out)})
                    if out.exists():
                        return out.read_text(encoding="utf-8").strip()
                    return (result.stdout or "").strip()

            whisper_cli = shutil.which("whisper-cli")
            if whisper_cli and self.whisper_model:
                with tempfile.TemporaryDirectory(prefix="aura-whisper-") as tmp:
                    prefix = Path(tmp) / "aura"
                    subprocess.run(
                        [whisper_cli, "-m", self.whisper_model, "-f", str(prepared), "-otxt", "-of", str(prefix)],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    transcript = prefix.with_suffix(".txt")
                    if transcript.exists():
                        return transcript.read_text(encoding="utf-8").strip()

        raise RuntimeError(
            "No local speech-to-text engine is configured. Set AURA_STT_CMD or install "
            "whisper.cpp and configure AURA_WHISPER_MODEL."
        )

    @staticmethod
    def _locale_env_key(locale: str) -> str:
        return re.sub(r"[^A-Z0-9]+", "_", locale.upper())

    def _piper_model_for_locale(self, locale: str) -> str:
        specific = os.getenv(f"AURA_PIPER_MODEL_{self._locale_env_key(locale)}", "").strip()
        language = locale.split("-", 1)[0]
        language_model = os.getenv(f"AURA_PIPER_MODEL_{self._locale_env_key(language)}", "").strip()
        if specific:
            return specific
        if language_model:
            return language_model
        default_locale = (os.getenv("AURA_PIPER_DEFAULT_LOCALE") or "en").lower()
        if language == default_locale.split("-", 1)[0].lower():
            return self.piper_model
        return ""

    def _voice_payload(self, *, text: str, locale: str) -> dict:
        return {
            "text": text,
            "locale": locale,
            "language": locale.split("-", 1)[0],
            "voice": self.tts_voice,
            "style": self.tts_style,
            "rate": self.tts_rate,
            "pitch_semitones": self.tts_pitch,
            "persona": "Sovereign Spiritual Love and Light",
        }

    def speak(self, text: str, output_path: str | Path, *, locale: str = "en") -> Path:
        text = (text or "").strip()
        locale = (locale or "en").strip().replace("_", "-")
        if not text:
            raise ValueError("Nothing to speak")
        output = Path(output_path).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        voice_payload = self._voice_payload(text=text, locale=locale)

        if self.tts_url:
            response = requests.post(
                f"{self.tts_url}/synthesize",
                json=voice_payload,
                timeout=120,
            )
            response.raise_for_status()
            output.write_bytes(response.content)
            return output

        if self.tts_cmd:
            self._run_template(
                self.tts_cmd,
                {
                    "text": text,
                    "output": str(output),
                    "locale": locale,
                    "language": locale.split("-", 1)[0],
                    "voice": self.tts_voice,
                    "style": self.tts_style,
                    "rate": f"{self.tts_rate:.3f}",
                    "pitch": f"{self.tts_pitch:.3f}",
                },
            )
            if not output.exists():
                raise RuntimeError("Configured AURA_TTS_CMD did not produce the requested audio file")
            return output

        piper_model = self._piper_model_for_locale(locale)
        if piper_model:
            subprocess.run(
                ["python", "-m", "piper", "-m", piper_model, "-f", str(output), "--", text],
                check=True,
                capture_output=True,
                text=True,
            )
            if output.exists():
                return output

        raise RuntimeError(
            f"No speech synthesis engine/voice is configured for locale {locale}. Set AURA_TTS_URL, "
            "a locale-aware AURA_TTS_CMD, or AURA_PIPER_MODEL_<LOCALE>."
        )

    @staticmethod
    def _spoken_summary(plan: ProducerPlan) -> str:
        if not plan.actions:
            return "I understood the request, but I do not yet have a studio action to perform."
        descriptions = []
        for action in plan.actions[:4]:
            phrase = action.action.replace("_", " ")
            if action.track_role:
                phrase += f" for {action.track_role.replace('_', ' ')}"
            if action.start_seconds is not None and action.end_seconds is not None:
                phrase += f" from {action.start_seconds:g} to {action.end_seconds:g} seconds"
            descriptions.append(phrase)
        sentence = "; then ".join(descriptions)
        suffix = " I need a little more detail before changing the audio." if plan.needs_confirmation else ""
        return f"I have mapped that to: {sentence}.{suffix}"

    def command(
        self,
        audio_path: str | Path,
        *,
        session_summary: dict | None = None,
        speech_output: str | Path | None = None,
        locale: str = "en",
    ) -> SpeechResult:
        transcript = self.transcribe(audio_path)
        plan = llm_plan(transcript, session_summary=session_summary)
        spoken = self._spoken_summary(plan)
        speech_file = None
        if speech_output is not None:
            speech_file = str(self.speak(spoken, speech_output, locale=locale))
        return SpeechResult(transcript=transcript, plan=plan, spoken_text=spoken, speech_file=speech_file)

    def diagnostics(self) -> dict:
        return {
            "stt_command_configured": bool(self.stt_cmd),
            "tts_command_configured": bool(self.tts_cmd),
            "tts_url_configured": bool(self.tts_url),
            "whisper_cli": shutil.which("whisper-cli"),
            "whisper_model_configured": bool(self.whisper_model),
            "piper_model_configured": bool(self.piper_model),
            "piper_default_locale": os.getenv("AURA_PIPER_DEFAULT_LOCALE", "en"),
            "locale_aware_tts": bool(self.tts_url or self.tts_cmd),
            "voice_profile": {
                "voice": self.tts_voice,
                "style": self.tts_style,
                "rate": self.tts_rate,
                "pitch_semitones": self.tts_pitch,
            },
            "ffmpeg": shutil.which("ffmpeg"),
            "browser_audio_transcoding": bool(shutil.which("ffmpeg")),
            "offline_first": True,
        }


def speech_result_json(result: SpeechResult) -> str:
    return json.dumps(
        {
            "transcript": result.transcript,
            "plan": result.plan.model_dump(),
            "spoken_text": result.spoken_text,
            "speech_file": result.speech_file,
        },
        indent=2,
    )
