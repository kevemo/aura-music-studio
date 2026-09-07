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
    """Offline-first speech interface for Aura.

    The preferred standalone path is whisper.cpp for STT plus a locally hosted TTS engine.
    Browser microphone formats such as WebM/Opus are converted to mono 16 kHz WAV through
    local ffmpeg before transcription. Studio actions remain entitlement-checked by the API.
    """

    _FORBIDDEN_SHELL_SYNTAX = (";", "|", "&", "<", ">", "`", "$(", "\n", "\r")
    _PLACEHOLDER_PATTERN = re.compile(r"\{[^{}]+\}")

    def __init__(self):
        self.stt_cmd = (os.getenv("AURA_STT_CMD") or "").strip()
        self.tts_cmd = (os.getenv("AURA_TTS_CMD") or "").strip()
        self.tts_url = (os.getenv("AURA_TTS_URL") or "").rstrip("/")
        self.whisper_model = (os.getenv("AURA_WHISPER_MODEL") or "").strip()
        self.piper_model = (os.getenv("AURA_PIPER_MODEL") or "").strip()

    @classmethod
    def _run_template(cls, template: str, values: dict[str, str]) -> subprocess.CompletedProcess:
        """Run a configured provider command without invoking a command shell.

        Command configuration is parsed into argv before substitutions are applied so a
        replacement containing whitespace remains one argument. Shell control syntax is
        rejected even though ``shell=False`` makes it inert, keeping the production contract
        fail-closed and preventing configuration from relying on shell semantics.
        """

        if not template or not template.strip():
            raise RuntimeError("Configured speech command is empty")
        if any(marker in template for marker in cls._FORBIDDEN_SHELL_SYNTAX):
            raise RuntimeError("Configured speech command contains forbidden shell syntax")

        try:
            argv = shlex.split(template, posix=True)
        except ValueError as exc:
            raise RuntimeError("Configured speech command has invalid quoting") from exc
        if not argv:
            raise RuntimeError("Configured speech command is empty")

        rendered: list[str] = []
        for token in argv:
            for key, value in values.items():
                token = token.replace("{" + key + "}", value)
            if cls._PLACEHOLDER_PATTERN.search(token):
                raise RuntimeError("Configured speech command contains an unsupported placeholder")
            rendered.append(token)

        return subprocess.run(rendered, shell=False, check=True, capture_output=True, text=True)

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

    def speak(self, text: str, output_path: str | Path) -> Path:
        text = (text or "").strip()
        if not text:
            raise ValueError("Nothing to speak")
        output = Path(output_path).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

        if self.tts_url:
            response = requests.post(f"{self.tts_url}/synthesize", json={"text": text}, timeout=120)
            response.raise_for_status()
            output.write_bytes(response.content)
            return output

        if self.tts_cmd:
            self._run_template(self.tts_cmd, {"text": text, "output": str(output)})
            if not output.exists():
                raise RuntimeError("Configured AURA_TTS_CMD did not produce the requested audio file")
            return output

        if self.piper_model:
            subprocess.run(
                ["python", "-m", "piper", "-m", self.piper_model, "-f", str(output), "--", text],
                check=True,
                capture_output=True,
                text=True,
            )
            if output.exists():
                return output

        raise RuntimeError(
            "No local speech synthesis engine is configured. Set AURA_TTS_URL/AURA_TTS_CMD "
            "or configure AURA_PIPER_MODEL."
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
    ) -> SpeechResult:
        transcript = self.transcribe(audio_path)
        plan = llm_plan(transcript, session_summary=session_summary)
        spoken = self._spoken_summary(plan)
        speech_file = None
        if speech_output is not None:
            speech_file = str(self.speak(spoken, speech_output))
        return SpeechResult(transcript=transcript, plan=plan, spoken_text=spoken, speech_file=speech_file)

    def diagnostics(self) -> dict:
        return {
            "stt_command_configured": bool(self.stt_cmd),
            "tts_command_configured": bool(self.tts_cmd),
            "tts_url_configured": bool(self.tts_url),
            "whisper_cli": shutil.which("whisper-cli"),
            "whisper_model_configured": bool(self.whisper_model),
            "piper_model_configured": bool(self.piper_model),
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