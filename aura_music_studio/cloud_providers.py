from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import requests


class ElevenMusicClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY")
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is not configured")
        self.base = os.getenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io").rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        return {"xi-api-key": self.api_key, "Content-Type": "application/json"}

    def compose(
        self,
        output: Path,
        *,
        prompt: str | None = None,
        composition_plan: dict | None = None,
        duration_seconds: float | None = None,
        instrumental: bool = False,
        model: str = "music_v2",
        finetune_id: str | None = None,
        seed: int | None = None,
        c2pa: bool = True,
    ) -> Path:
        if bool(prompt) == bool(composition_plan):
            raise ValueError("Provide exactly one of prompt or composition_plan")
        payload: dict[str, Any] = {
            "prompt": prompt,
            "composition_plan": composition_plan,
            "model_id": model,
            "force_instrumental": instrumental,
            "sign_with_c2pa": c2pa,
        }
        if duration_seconds:
            payload["music_length_ms"] = int(duration_seconds * 1000)
        if finetune_id:
            payload["finetune_id"] = finetune_id
        if seed is not None and composition_plan is not None:
            payload["seed"] = seed
        r = requests.post(
            f"{self.base}/v1/music",
            params={"output_format": "mp3_48000_192"},
            headers=self.headers,
            json=payload,
            timeout=1800,
        )
        r.raise_for_status()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(r.content)
        return output

    def make_plan(self, prompt: str, duration_seconds: float | None = None, model: str = "music_v2") -> dict:
        payload: dict[str, Any] = {"prompt": prompt, "model_id": model}
        if duration_seconds:
            payload["music_length_ms"] = int(duration_seconds * 1000)
        r = requests.post(f"{self.base}/v1/music/plan", headers=self.headers, json=payload, timeout=180)
        r.raise_for_status()
        return r.json()

    def upload_for_edit(self, path: Path, extract_plan: str = "music_v2", timestamps: bool = True) -> dict:
        headers = {"xi-api-key": self.api_key}
        with path.open("rb") as f:
            r = requests.post(
                f"{self.base}/v1/music/upload",
                headers=headers,
                files={"file": (path.name, f)},
                data={"extract_composition_plan": extract_plan, "with_timestamps": str(timestamps).lower()},
                timeout=1800,
            )
        r.raise_for_status()
        return r.json()

    def separate_stems(self, path: Path, output_zip: Path, six_stems: bool = True) -> Path:
        headers = {"xi-api-key": self.api_key}
        with path.open("rb") as f:
            r = requests.post(
                f"{self.base}/v1/music/stem-separation",
                headers=headers,
                params={"stem_variation_id": "six_stems_v1" if six_stems else "two_stems_v1"},
                files={"file": (path.name, f)},
                timeout=1800,
            )
        r.raise_for_status()
        output_zip.parent.mkdir(parents=True, exist_ok=True)
        output_zip.write_bytes(r.content)
        return output_zip


class MurekaClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("MUREKA_API_KEY")
        if not self.api_key:
            raise RuntimeError("MUREKA_API_KEY is not configured")
        self.base = os.getenv("MUREKA_BASE_URL", "https://api.mureka.ai").rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def lyrics_to_song(
        self,
        output: Path,
        *,
        lyrics: str,
        prompt: str,
        model: str = "auto",
        gender: str | None = None,
        reference_id: str | None = None,
        vocal_id: str | None = None,
        melody_id: str | None = None,
    ) -> Path:
        payload: dict[str, Any] = {"lyrics": lyrics, "prompt": prompt, "model": model, "n": 1}
        if gender:
            payload["gender"] = gender
        if reference_id:
            payload["reference_id"] = reference_id
        if vocal_id:
            payload["vocal_id"] = vocal_id
        if melody_id:
            payload["melody_id"] = melody_id
        task = self._post("/v1/song/generate", payload)
        result = self._poll(task["id"], "/v1/song/query/{id}")
        return self._download_choice(result, output)

    def instrumental(self, output: Path, *, prompt: str, model: str = "auto") -> Path:
        task = self._post("/v1/instrumental/generate", {"model": model, "prompt": prompt, "n": 1})
        result = self._poll(task["id"], "/v1/instrumental/query/{id}")
        return self._download_choice(result, output)

    def remix(self, output: Path, *, upload_audio_id: str, prompt: str, lyrics: str) -> Path:
        task = self._post("/v1/song/remix", {
            "upload_audio_id": upload_audio_id,
            "prompt": prompt,
            "lyrics": lyrics,
            "n": 1,
        })
        result = self._poll(task["id"], "/v1/song/query/{id}")
        return self._download_choice(result, output)

    def clone_vocal(self, path: Path, description: str) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with path.open("rb") as f:
            r = requests.post(
                f"{self.base}/v1/song/vocal-clone",
                headers=headers,
                files={"file": (path.name, f)},
                data={"description": description[:1024]},
                timeout=180,
            )
        r.raise_for_status()
        data = r.json()
        return data["vocal_id"]

    def upload(self, path: Path, purpose: str) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with path.open("rb") as f:
            r = requests.post(
                f"{self.base}/v1/files/upload",
                headers=headers,
                files={"file": (path.name, f)},
                data={"purpose": purpose},
                timeout=300,
            )
        r.raise_for_status()
        return r.json()

    def _post(self, endpoint: str, payload: dict) -> dict:
        r = requests.post(f"{self.base}{endpoint}", headers=self.headers, json=payload, timeout=120)
        r.raise_for_status()
        return r.json()

    def _poll(self, task_id: str, route: str, timeout: int = 1800) -> dict:
        deadline = time.time() + timeout
        headers = {"Authorization": f"Bearer {self.api_key}"}
        while time.time() < deadline:
            r = requests.get(f"{self.base}{route.format(id=task_id)}", headers=headers, timeout=60)
            r.raise_for_status()
            data = r.json()
            status = str(data.get("status", "")).lower()
            if status == "succeeded":
                return data
            if status in {"failed", "timeouted", "cancelled"}:
                raise RuntimeError(f"Mureka task failed: {data}")
            time.sleep(6)
        raise TimeoutError(f"Mureka task {task_id} timed out")

    def _download_choice(self, result: dict, output: Path) -> Path:
        url = _find_audio_url(result)
        if not url:
            raise RuntimeError(f"No downloadable audio URL in Mureka response: {result}")
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(r.content)
        return output


def _find_audio_url(value: Any) -> str | None:
    preferred_keys = ("wav_url", "url", "audio_url", "mp3_url", "stream_url")
    if isinstance(value, dict):
        for key in preferred_keys:
            v = value.get(key)
            if isinstance(v, str) and v.startswith("http"):
                return v
        for v in value.values():
            found = _find_audio_url(v)
            if found:
                return found
    elif isinstance(value, list):
        for v in value:
            found = _find_audio_url(v)
            if found:
                return found
    return None
