from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import librosa
import soundfile as sf

from .speech import AuraSpeechService


class AuraAttachmentUnderstandingError(RuntimeError):
    pass


class AuraAttachmentUnderstandingService:
    """Extract real, bounded context from files attached to Aura conversations.

    Text/documents are parsed locally. Audio is technically analysed and, when a real STT
    backend is configured, transcribed. Video is probed and its audio may be transcribed.
    Image/video semantic vision is delegated only to a configured real multimodal analyser;
    Aura never invents visual understanding from a filename or metadata alone.
    """

    TEXT_EXTS = {
        ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm",
        ".py", ".js", ".ts", ".tsx", ".jsx", ".css", ".sql", ".log", ".rtf",
    }
    AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wma"}
    VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpeg", ".mpg"}
    IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"}

    def __init__(self):
        self.speech = AuraSpeechService()
        self.media_analyzer_cmd = (os.getenv("AURA_MEDIA_ANALYZER_CMD") or "").strip()
        self.max_text_chars = int(os.getenv("AURA_CHAT_TEXT_ATTACHMENT_CHARS", "50000"))
        self.max_transcript_chars = int(os.getenv("AURA_CHAT_TRANSCRIPT_CHARS", "50000"))

    @staticmethod
    def _clean_text(value: str) -> str:
        value = value.replace("\x00", " ")
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n{4,}", "\n\n\n", value)
        return value.strip()

    def _text_file(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")[: self.max_text_chars]
        return {"kind": "text", "text_excerpt": self._clean_text(text), "parsed": True}

    def _pdf(self, path: Path) -> dict[str, Any]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise AuraAttachmentUnderstandingError("PDF parsing requires the pypdf package") from exc
        reader = PdfReader(str(path))
        chunks: list[str] = []
        chars = 0
        for page_number, page in enumerate(reader.pages, 1):
            if chars >= self.max_text_chars:
                break
            text = page.extract_text() or ""
            room = self.max_text_chars - chars
            piece = text[:room]
            chunks.append(f"[Page {page_number}]\n{piece}")
            chars += len(piece)
        return {
            "kind": "document",
            "format": "pdf",
            "pages": len(reader.pages),
            "text_excerpt": self._clean_text("\n\n".join(chunks)),
            "parsed": True,
        }

    def _docx(self, path: Path) -> dict[str, Any]:
        with zipfile.ZipFile(path) as archive:
            raw = archive.read("word/document.xml")
        root = ElementTree.fromstring(raw)
        texts: list[str] = []
        for node in root.iter():
            if node.tag.endswith("}t") and node.text:
                texts.append(node.text)
            elif node.tag.endswith("}p"):
                texts.append("\n")
        text = self._clean_text(" ".join(texts))[: self.max_text_chars]
        return {"kind": "document", "format": "docx", "text_excerpt": text, "parsed": True}

    @staticmethod
    def _ffprobe(path: Path) -> dict[str, Any]:
        binary = shutil.which("ffprobe")
        if not binary:
            return {"available": False, "reason": "ffprobe is not installed"}
        completed = subprocess.run(
            [
                binary, "-v", "error", "-show_entries",
                "format=duration,size,bit_rate,format_name:stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
                "-of", "json", str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if completed.returncode != 0:
            return {"available": False, "reason": (completed.stderr or "ffprobe failed")[-600:]}
        try:
            payload = json.loads(completed.stdout or "{}")
        except Exception:
            payload = {}
        return {"available": True, **payload}

    def _audio(self, path: Path) -> dict[str, Any]:
        info = sf.info(str(path))
        result: dict[str, Any] = {
            "kind": "audio",
            "duration_seconds": float(info.frames / info.samplerate) if info.samplerate else None,
            "sample_rate": int(info.samplerate),
            "channels": int(info.channels),
            "format": info.format,
            "subtype": info.subtype,
            "ffprobe": self._ffprobe(path),
        }
        try:
            y, sr = librosa.load(path, sr=None, mono=True, duration=180.0)
            if y.size:
                rms = librosa.feature.rms(y=y)[0]
                centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
                result["analysis_first_180_seconds"] = {
                    "rms_mean": float(rms.mean()),
                    "spectral_centroid_mean_hz": float(centroid.mean()),
                    "tempo_bpm_estimate": float(librosa.feature.tempo(y=y, sr=sr)[0]),
                }
        except Exception as exc:
            result["technical_analysis_warning"] = f"{type(exc).__name__}: {exc}"

        if self._stt_available():
            try:
                result["transcript"] = self.speech.transcribe(path)[: self.max_transcript_chars]
                result["transcribed"] = True
            except Exception as exc:
                result["transcribed"] = False
                result["transcription_warning"] = f"{type(exc).__name__}: {exc}"
        else:
            result["transcribed"] = False
            result["transcription_warning"] = "No speech-to-text engine is configured."
        return result

    def _video(self, path: Path) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": "video", "ffprobe": self._ffprobe(path)}
        if self._stt_available() and shutil.which("ffmpeg"):
            with tempfile.TemporaryDirectory(prefix="aura-video-audio-") as tmp:
                audio = Path(tmp) / "audio.wav"
                completed = subprocess.run(
                    [
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
                        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=180,
                )
                if completed.returncode == 0 and audio.is_file() and audio.stat().st_size > 1000:
                    try:
                        result["audio_transcript"] = self.speech.transcribe(audio)[: self.max_transcript_chars]
                        result["audio_transcribed"] = True
                    except Exception as exc:
                        result["audio_transcribed"] = False
                        result["audio_transcription_warning"] = f"{type(exc).__name__}: {exc}"
        visual = self._external_media_analysis(path, kind="video")
        if visual:
            result["visual_analysis"] = visual
            result["visual_analysis_available"] = True
        else:
            result["visual_analysis_available"] = False
            result["visual_analysis_note"] = (
                "No real visual-semantic analyser is configured for this deployment; Aura may use technical metadata/audio transcript only."
            )
        return result

    def _image(self, path: Path) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": "image", "ffprobe": self._ffprobe(path)}
        visual = self._external_media_analysis(path, kind="image")
        if visual:
            result["visual_analysis"] = visual
            result["visual_analysis_available"] = True
        else:
            result["visual_analysis_available"] = False
            result["visual_analysis_note"] = (
                "No real visual-semantic analyser is configured for this deployment; Aura must not infer image content from metadata."
            )
        return result

    def _external_media_analysis(self, path: Path, *, kind: str) -> dict[str, Any] | str | None:
        if not self.media_analyzer_cmd:
            return None
        with tempfile.TemporaryDirectory(prefix="aura-media-analysis-") as tmp:
            output = Path(tmp) / "analysis.json"
            env = os.environ.copy()
            env.update(
                {
                    "AURA_MEDIA_INPUT": str(path.resolve()),
                    "AURA_MEDIA_KIND": kind,
                    "AURA_MEDIA_OUTPUT": str(output.resolve()),
                }
            )
            completed = subprocess.run(
                shlex.split(self.media_analyzer_cmd),
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=int(os.getenv("AURA_MEDIA_ANALYZER_TIMEOUT", "300")),
            )
            if completed.returncode != 0:
                raise AuraAttachmentUnderstandingError(
                    (completed.stderr or f"Configured {kind} analyser failed")[-1200:]
                )
            if output.is_file():
                raw = output.read_text(encoding="utf-8", errors="replace")
                try:
                    return json.loads(raw)
                except Exception:
                    return raw[: self.max_text_chars]
            stdout = (completed.stdout or "").strip()
            if stdout:
                try:
                    return json.loads(stdout)
                except Exception:
                    return stdout[: self.max_text_chars]
        return None

    def _stt_available(self) -> bool:
        diagnostics = self.speech.diagnostics()
        return bool(
            diagnostics.get("stt_command_configured")
            or (diagnostics.get("whisper_cli") and diagnostics.get("whisper_model_configured"))
        )

    def understand(self, path: str | Path, *, mime_type: str = "") -> dict[str, Any]:
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(source)
        suffix = source.suffix.lower()
        mime = (mime_type or "").lower()
        try:
            if suffix in self.TEXT_EXTS or mime.startswith("text/"):
                return self._text_file(source)
            if suffix == ".pdf" or mime == "application/pdf":
                return self._pdf(source)
            if suffix == ".docx" or "wordprocessingml.document" in mime:
                return self._docx(source)
            if suffix in self.AUDIO_EXTS or mime.startswith("audio/"):
                return self._audio(source)
            if suffix in self.VIDEO_EXTS or mime.startswith("video/"):
                return self._video(source)
            if suffix in self.IMAGE_EXTS or mime.startswith("image/"):
                return self._image(source)
        except Exception as exc:
            return {
                "kind": "unknown",
                "understood": False,
                "warning": f"{type(exc).__name__}: {exc}",
            }
        return {
            "kind": "unknown",
            "understood": False,
            "warning": "This attachment format is stored securely but has no configured parser/analyser yet.",
        }
