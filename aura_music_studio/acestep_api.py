from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from pydantic import BaseModel, Field


ACE_TRACKS = {
    "vocals", "backing_vocals", "drums", "bass", "guitar", "keyboard", "percussion",
    "strings", "synth", "fx", "brass", "woodwinds",
}


class AceStepRequest(BaseModel):
    prompt: str = ""
    lyrics: str = ""
    task_type: str = Field(default="text2music", pattern="^(text2music|cover|repaint|lego|extract|complete)$")
    model: str | None = None
    bpm: int | None = None
    key_scale: str | None = None
    time_signature: str = "4"
    audio_duration: float | None = None
    thinking: bool = True
    use_format: bool = True
    vocal_language: str = "en"
    audio_format: str = "wav"
    inference_steps: int = 8
    guidance_scale: float = 7.0
    seed: int | None = None
    batch_size: int = Field(default=1, ge=1, le=8)
    instruction: str | None = None
    repainting_start: float = 0.0
    repainting_end: float | None = None
    chunk_mask_mode: str = "auto"
    audio_cover_strength: float = Field(default=0.75, ge=0.0, le=1.0)
    src_audio: str | None = None
    reference_audio: str | None = None


class AceStepClient:
    """Client for ACE-Step 1.5's documented async REST server (`uv run acestep-api`)."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: int = 1800):
        self.base_url = (base_url or os.getenv("AURA_ACESTEP_API_URL") or "http://127.0.0.1:8001").rstrip("/") + "/"
        self.api_key = api_key or os.getenv("ACESTEP_API_KEY")
        self.timeout = int(os.getenv("AURA_ACESTEP_TIMEOUT", str(timeout)))
        self.session = requests.Session()
        if self.api_key:
            self.session.headers["Authorization"] = f"Bearer {self.api_key}"

    def health(self) -> bool:
        for endpoint in ("health", "v1/models"):
            try:
                r = self.session.get(urljoin(self.base_url, endpoint), timeout=10)
                if r.ok:
                    return True
            except Exception:
                pass
        return False

    def generate(self, request: AceStepRequest, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        task_id = self.submit(request)
        items = self.wait(task_id)
        outputs = []
        for i, item in enumerate(items, 1):
            file_url = item.get("file") or item.get("url")
            if not file_url:
                continue
            suffix = "." + request.audio_format.replace("wav32", "wav")
            out = output_dir / f"ace_step_{request.task_type}_{i:02d}{suffix}"
            self.download(file_url, out)
            outputs.append(out)
        if not outputs:
            raise RuntimeError(f"ACE-Step task {task_id} succeeded but returned no downloadable audio")
        return outputs

    def submit(self, request: AceStepRequest) -> str:
        data = request.model_dump(exclude={"src_audio", "reference_audio"}, exclude_none=True)
        if request.seed is not None:
            data["use_random_seed"] = False
        else:
            data["use_random_seed"] = True
            data["seed"] = -1
        if request.task_type in {"cover", "repaint", "extract"}:
            data["thinking"] = False

        files = {}
        handles = []
        try:
            if request.src_audio:
                p = Path(request.src_audio)
                if not p.exists():
                    raise FileNotFoundError(p)
                h = p.open("rb"); handles.append(h); files["src_audio"] = (p.name, h)
            if request.reference_audio:
                p = Path(request.reference_audio)
                if not p.exists():
                    raise FileNotFoundError(p)
                h = p.open("rb"); handles.append(h); files["reference_audio"] = (p.name, h)
            if files:
                form = {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in data.items()}
                r = self.session.post(urljoin(self.base_url, "release_task"), data=form, files=files, timeout=120)
            else:
                r = self.session.post(urljoin(self.base_url, "release_task"), json=data, timeout=120)
            r.raise_for_status()
            payload = r.json()
        finally:
            for h in handles:
                h.close()
        if payload.get("code", 200) != 200:
            raise RuntimeError(payload.get("error") or payload)
        task = payload.get("data") or {}
        task_id = task.get("task_id")
        if not task_id:
            raise RuntimeError(f"ACE-Step returned no task_id: {payload}")
        return str(task_id)

    def wait(self, task_id: str) -> list[dict]:
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            r = self.session.post(urljoin(self.base_url, "query_result"), json={"task_id_list": [task_id]}, timeout=60)
            r.raise_for_status()
            payload = r.json()
            rows = payload.get("data") or []
            if rows:
                row = rows[0]
                status = int(row.get("status", 0))
                if status == 2:
                    raise RuntimeError(f"ACE-Step task failed: {row}")
                if status == 1:
                    result = row.get("result", [])
                    if isinstance(result, str):
                        result = json.loads(result or "[]")
                    return result if isinstance(result, list) else [result]
            time.sleep(2)
        raise TimeoutError(f"ACE-Step task timed out: {task_id}")

    def download(self, file_url: str, output: Path) -> Path:
        url = file_url if file_url.startswith("http://") or file_url.startswith("https://") else urljoin(self.base_url, file_url.lstrip("/"))
        output.parent.mkdir(parents=True, exist_ok=True)
        with self.session.get(url, stream=True, timeout=180) as r:
            r.raise_for_status()
            with output.open("wb") as f:
                for chunk in r.iter_content(1024 * 1024):
                    if chunk:
                        f.write(chunk)
        return output

    def repaint(self, source: Path, output_dir: Path, *, prompt: str, start: float, end: float, strength: float = .75, model: str | None = None) -> Path:
        return self.generate(AceStepRequest(
            prompt=prompt, task_type="repaint", src_audio=str(source), repainting_start=start,
            repainting_end=end, chunk_mask_mode="explicit", audio_cover_strength=strength, model=model,
        ), output_dir)[0]

    def cover(self, source: Path, output_dir: Path, *, prompt: str, strength: float = .75, model: str | None = None, bpm: int | None = None, key: str | None = None) -> Path:
        return self.generate(AceStepRequest(
            prompt=prompt, task_type="cover", src_audio=str(source), audio_cover_strength=strength,
            model=model, bpm=bpm, key_scale=key,
        ), output_dir)[0]

    def add_track(self, source: Path, output_dir: Path, *, track: str, prompt: str, model: str | None = None, start: float = 0.0, end: float = -1) -> Path:
        track = track.strip().lower()
        if track not in ACE_TRACKS:
            raise ValueError(f"Unsupported ACE-Step Lego track: {track}. Choose from {sorted(ACE_TRACKS)}")
        return self.generate(AceStepRequest(
            prompt=prompt,
            task_type="lego",
            src_audio=str(source),
            model=model,
            thinking=True,
            instruction=f"Generate the {track} track based on the audio context:",
            repainting_start=start,
            repainting_end=end,
        ), output_dir)[0]

    def extract_track(self, source: Path, output_dir: Path, *, track: str, model: str | None = None) -> Path:
        track = track.strip().lower()
        if track not in ACE_TRACKS:
            raise ValueError(f"Unsupported ACE-Step Extract track: {track}. Choose from {sorted(ACE_TRACKS)}")
        return self.generate(AceStepRequest(
            task_type="extract",
            src_audio=str(source),
            model=model,
            thinking=False,
            instruction=f"Extract the {track} track from the audio:",
        ), output_dir)[0]

    def complete(self, source: Path, output_dir: Path, *, tracks: list[str], prompt: str, model: str | None = None) -> Path:
        normalized = [x.strip().lower() for x in tracks]
        invalid = [x for x in normalized if x not in ACE_TRACKS]
        if invalid:
            raise ValueError(f"Unsupported ACE-Step Complete tracks: {invalid}")
        joined = ", ".join(normalized)
        return self.generate(AceStepRequest(
            prompt=prompt,
            task_type="complete",
            src_audio=str(source),
            model=model,
            thinking=True,
            instruction=f"Complete the input track with {joined}:",
        ), output_dir)[0]
