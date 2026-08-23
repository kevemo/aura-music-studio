from __future__ import annotations

import json
import mimetypes
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import librosa
import soundfile as sf
from pydantic import BaseModel, Field

from .rights import RightsLedger, RightsRecord

AUDIO_EXTS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
MODEL_EXTS = {".nam", ".onnx"}
SYMBOLIC_EXTS = {".mid", ".midi", ".musicxml", ".xml", ".mxl"}
SCORE_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
TEXT_EXTS = {".txt", ".md", ".lrc", ".srt", ".json", ".yaml", ".yml"}


class AssetRecord(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    kind: str
    path: str
    sha256: str
    mime_type: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    rights_record_id: str | None = None
    analysis: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    notes: str = ""


class AssetLibrary:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.assets_dir = self.project_root / "input" / "assets"
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.project_root / "assets.json"
        self.ledger = RightsLedger(self.project_root / ".aura_rights")

    def ingest(
        self,
        source: Path,
        *,
        kind: str = "auto",
        rights_basis: str = "user_owned_or_licensed",
        attestation: str = "I confirm I have the right to use this uploaded material in this project.",
        tags: list[str] | None = None,
        notes: str = "",
    ) -> AssetRecord:
        source = source.resolve()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(source)
        detected = self.detect_kind(source) if kind == "auto" else kind
        if detected == "unsupported":
            raise ValueError(f"Unsupported asset type: {source.suffix}")

        digest = self.ledger.sha256(source)
        target = self.assets_dir / f"{digest[:12]}_{source.name}"
        if not target.exists():
            shutil.copy2(source, target)

        rights = RightsRecord(
            asset_name=source.name,
            asset_sha256=digest,
            rights_basis=rights_basis,
            user_attestation=attestation,
        )
        self.ledger.add_rights_record(rights)
        record = AssetRecord(
            name=source.name,
            kind=detected,
            path=str(target.relative_to(self.project_root)),
            sha256=digest,
            mime_type=mimetypes.guess_type(source.name)[0],
            rights_record_id=rights.id,
            analysis=self._analyze(target, detected),
            tags=tags or [],
            notes=notes,
        )
        data = self._read_index()
        data = [x for x in data if x.get("sha256") != digest]
        data.append(record.model_dump())
        self.index_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return record

    def list(self) -> list[AssetRecord]:
        return [AssetRecord.model_validate(x) for x in self._read_index()]

    def get(self, asset_id: str) -> AssetRecord:
        for item in self.list():
            if item.id == asset_id:
                return item
        raise KeyError(asset_id)

    @staticmethod
    def detect_kind(path: Path) -> str:
        ext = path.suffix.lower()
        if ext in AUDIO_EXTS:
            return "audio"
        if ext in VIDEO_EXTS:
            return "video"
        if ext in MODEL_EXTS:
            return "model"
        if ext in SYMBOLIC_EXTS:
            return "symbolic"
        if ext in SCORE_EXTS:
            return "score"
        if ext in TEXT_EXTS:
            return "text"
        return "unsupported"

    def _read_index(self) -> list[dict]:
        if not self.index_path.exists():
            return []
        try:
            value = json.loads(self.index_path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except Exception:
            return []

    @staticmethod
    def _analyze(path: Path, kind: str) -> dict:
        if kind == "audio":
            try:
                info = sf.info(path)
                y, sr = librosa.load(path, sr=None, mono=True, duration=180)
                tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
                tempo_value = float(tempo[0] if hasattr(tempo, "__len__") else tempo)
                chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
                pitch_class = int(chroma.mean(axis=1).argmax()) if chroma.size else None
                names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
                return {
                    "duration_seconds": float(info.frames / info.samplerate),
                    "sample_rate": int(info.samplerate),
                    "channels": int(info.channels),
                    "estimated_bpm": tempo_value,
                    "dominant_pitch_class": names[pitch_class] if pitch_class is not None else None,
                }
            except Exception as exc:
                return {"analysis_error": f"{type(exc).__name__}: {exc}"}
        if kind == "video":
            try:
                proc = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                payload = json.loads(proc.stdout)
                fmt = payload.get("format", {})
                streams = payload.get("streams", [])
                video = next((s for s in streams if s.get("codec_type") == "video"), {})
                return {
                    "duration_seconds": float(fmt.get("duration", 0) or 0),
                    "width": video.get("width"),
                    "height": video.get("height"),
                    "video_codec": video.get("codec_name"),
                    "has_audio": any(s.get("codec_type") == "audio" for s in streams),
                }
            except Exception as exc:
                return {"analysis_error": f"{type(exc).__name__}: {exc}"}
        if kind == "model":
            return {"model_format": path.suffix.lower().lstrip(".")}
        return {}
