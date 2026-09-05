from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import requests
from pydantic import BaseModel, Field

ACE_TRACKS = {
    "vocals", "backing_vocals", "drums", "bass", "guitar", "keyboard", "percussion",
    "strings", "synth", "fx", "brass", "woodwinds",
}

_PROVIDER_REDIRECTS = 5
_DEFAULT_MAX_AUDIO_BYTES = 512 * 1024 * 1024
_HARD_MAX_AUDIO_BYTES = 2 * 1024 * 1024 * 1024
_MIN_AUDIO_BYTES = 4096


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


def _effective_port(parsed) -> int:
    if parsed.port is not None:
        return parsed.port
    if parsed.scheme == "https":
        return 443
    if parsed.scheme == "http":
        return 80
    raise ValueError("ACE-Step provider URL must use HTTP(S)")


def _provider_origin(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not host:
        raise ValueError("ACE-Step provider URL must use HTTP(S) with a hostname")
    if parsed.username or parsed.password:
        raise ValueError("ACE-Step provider URLs must not contain embedded credentials")
    return scheme, host, _effective_port(parsed)


def _audio_download_limit() -> int:
    raw = (os.getenv("AURA_ACESTEP_MAX_AUDIO_BYTES") or str(_DEFAULT_MAX_AUDIO_BYTES)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("AURA_ACESTEP_MAX_AUDIO_BYTES must be an integer") from exc
    if value < _MIN_AUDIO_BYTES:
        raise ValueError(f"AURA_ACESTEP_MAX_AUDIO_BYTES must be at least {_MIN_AUDIO_BYTES}")
    return min(value, _HARD_MAX_AUDIO_BYTES)


class AceStepClient:
    """Client for ACE-Step 1.5's documented async REST server (`uv run acestep-api`)."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: int = 1800):
        self.base_url = (base_url or os.getenv("AURA_ACESTEP_API_URL") or "http://127.0.0.1:8001").rstrip("/") + "/"
        _provider_origin(self.base_url)
        self.api_key = api_key or os.getenv("ACESTEP_API_KEY")
        self.timeout = int(os.getenv("AURA_ACESTEP_TIMEOUT", str(timeout)))
        self.session = requests.Session()
        if self.api_key:
            self.session.headers["Authorization"] = f"Bearer {self.api_key}"

    def _provider_url(self, value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("ACE-Step provider returned an empty audio URL")
        candidate = urljoin(self.base_url, raw.lstrip("/")) if not raw.startswith(("http://", "https://")) else raw
        parsed = urlparse(candidate)
        if parsed.fragment:
            raise ValueError("ACE-Step provider download URLs must not contain fragments")
        if _provider_origin(candidate) != _provider_origin(self.base_url):
            raise PermissionError("ACE-Step provider response attempted a cross-origin audio download")
        return candidate

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
            if not isinstance(item, dict):
                raise RuntimeError(f"ACE-Step task {task_id} returned an invalid result item")
            file_url = item.get("file") or item.get("url")
            if not file_url:
                continue
            suffix = "." + request.audio_format.replace("wav32", "wav")
            out = output_dir / f"ace_step_{request.task_type}_{i:02d}{suffix}"
            self.download(str(file_url), out)
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
        current = self._provider_url(file_url)
        max_bytes = _audio_download_limit()
        response = None

        for _ in range(_PROVIDER_REDIRECTS + 1):
            current = self._provider_url(current)
            response = self.session.get(
                current,
                stream=True,
                timeout=180,
                allow_redirects=False,
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                response.close()
                response = None
                if not location:
                    raise ValueError("ACE-Step provider redirect did not include a Location header")
                current = self._provider_url(urljoin(current, location))
                continue
            break
        else:
            raise ValueError("ACE-Step provider audio download exceeded the redirect limit")

        if response is None:
            raise RuntimeError("ACE-Step provider returned no audio response")

        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(f".{output.name}.{uuid4().hex}.part")
        try:
            with response:
                response.raise_for_status()
                length_header = response.headers.get("content-length")
                if length_header:
                    try:
                        content_length = int(length_header)
                    except ValueError as exc:
                        raise ValueError("ACE-Step provider returned an invalid Content-Length") from exc
                    if content_length < 0 or content_length > max_bytes:
                        raise ValueError("ACE-Step provider audio exceeded AURA_ACESTEP_MAX_AUDIO_BYTES")

                total = 0
                with partial.open("xb") as handle:
                    try:
                        os.chmod(partial, 0o600)
                    except OSError:
                        pass
                    for chunk in response.iter_content(1024 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError("ACE-Step provider audio exceeded AURA_ACESTEP_MAX_AUDIO_BYTES")
                        handle.write(chunk)

            if total < _MIN_AUDIO_BYTES:
                raise RuntimeError("ACE-Step provider returned an empty/invalid audio file")
            partial.replace(output)
            return output
        except Exception:
            partial.unlink(missing_ok=True)
            raise

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
            prompt=prompt, task_type="lego", src_audio=str(source), model=model, thinking=True,
            instruction=f"Generate the {track} track based on the audio context:",
            repainting_start=start, repainting_end=end, inference_steps=32, guidance_scale=7.0,
        ), output_dir)[0]

    def extract_track(self, source: Path, output_dir: Path, *, track: str, model: str | None = None) -> Path:
        track = track.strip().lower()
        if track not in ACE_TRACKS:
            raise ValueError(f"Unsupported ACE-Step Extract track: {track}. Choose from {sorted(ACE_TRACKS)}")
        return self.generate(AceStepRequest(
            task_type="extract", src_audio=str(source), model=model, thinking=False,
            instruction=f"Extract the {track} track from the audio:", inference_steps=32, guidance_scale=7.0,
        ), output_dir)[0]

    def complete(
        self,
        source: Path,
        output_dir: Path,
        *,
        tracks: list[str],
        prompt: str,
        lyrics: str = "",
        model: str | None = None,
        bpm: int | None = None,
        key: str | None = None,
        meter: str = "4",
        language: str = "en",
    ) -> Path:
        """Complete an isolated vocal/instrument with selected missing tracks.

        `lyrics` is optional but enables the complete workflow to add a new lead vocal when the
        uploaded source is an instrument. The source performance remains the conditioning audio.
        """
        normalized = [x.strip().lower() for x in tracks]
        invalid = [x for x in normalized if x not in ACE_TRACKS]
        if invalid:
            raise ValueError(f"Unsupported ACE-Step Complete tracks: {invalid}")
        joined = ", ".join(normalized)
        return self.generate(AceStepRequest(
            prompt=prompt,
            lyrics=lyrics,
            task_type="complete",
            src_audio=str(source),
            model=model,
            bpm=bpm,
            key_scale=key,
            time_signature=meter,
            vocal_language=language,
            thinking=True,
            instruction=f"Complete the input track with {joined}:",
            inference_steps=32,
            guidance_scale=7.0,
        ), output_dir)[0]
