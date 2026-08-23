from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

from .acestep_api import AceStepClient, AceStepRequest
from .renderer_runtime import probe_real_audio, renderer_runtime_status


def _ace_smoke(output: Path) -> dict:
    client = AceStepClient()
    request = AceStepRequest(
        prompt=(
            "short professional instrumental production diagnostic, modern pop-rock, real drums, bass, "
            "clean electric guitar, piano, no spoken words, polished stereo mix"
        ),
        lyrics="[Instrumental]",
        task_type="text2music",
        model=os.getenv("AURA_ACESTEP_FULL_MODEL", "acestep-v15-xl-turbo"),
        bpm=112,
        key_scale="C Major",
        time_signature="4",
        audio_duration=8.0,
        thinking=False,
        use_format=False,
        audio_format="wav",
        inference_steps=8,
        guidance_scale=7.0,
        batch_size=1,
    )
    with tempfile.TemporaryDirectory(prefix="esp-ace-smoke-") as tmp:
        paths = client.generate(request, Path(tmp))
        if not paths:
            raise RuntimeError("ACE-Step returned no audio result")
        source = paths[0]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(source.read_bytes())
    probe = probe_real_audio(output, minimum_seconds=2.0)
    if not probe.valid:
        raise RuntimeError(f"ACE-Step smoke output failed waveform validation: {probe.error}")
    return {
        "engine": "ace-step",
        "real_audio": True,
        "duration_seconds": probe.duration_seconds,
        "sample_rate": probe.sample_rate,
        "channels": probe.channels,
        "output": str(output),
    }


def _yue_headers() -> dict[str, str]:
    key = (os.getenv("YUE_API_KEY") or "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def _yue_smoke(output: Path) -> dict:
    base = (os.getenv("AURA_YUE_API_URL") or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("AURA_YUE_API_URL is not configured")
    headers = _yue_headers()
    response = requests.post(
        f"{base}/v1/jobs",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "prompt": "uplifting modern pop rock, clean guitar, piano, live drums, warm lead vocal",
            "lyrics": "[Verse]\nWe test the sound and let the music rise\n[Chorus]\nLive sound, shining bright tonight",
            "segments": 1,
            "seed": 42,
            "max_new_tokens": min(3000, int(os.getenv("AURA_YUE_MAX_NEW_TOKENS", "3000"))),
            "stage2_batch_size": min(4, int(os.getenv("AURA_YUE_STAGE2_BATCH_SIZE", "4"))),
            "stage1_model": os.getenv("AURA_YUE_STAGE1_MODEL", "m-a-p/YuE-s1-7B-anneal-en-cot"),
            "stage2_model": os.getenv("AURA_YUE_STAGE2_MODEL", "m-a-p/YuE-s2-1B-general"),
        },
        timeout=30,
    )
    response.raise_for_status()
    job_id = str(response.json().get("job_id") or "")
    if not job_id:
        raise RuntimeError("YuE returned no smoke-test job ID")
    deadline = time.time() + int(os.getenv("AURA_YUE_TIMEOUT", "7200"))
    while time.time() < deadline:
        status_response = requests.get(f"{base}/v1/jobs/{job_id}", headers=headers, timeout=15)
        status_response.raise_for_status()
        state = status_response.json()
        phase = str(state.get("status") or "").lower()
        if phase == "failed":
            raise RuntimeError(f"YuE smoke generation failed: {state.get('error') or state}")
        if phase == "completed":
            audio_url = state.get("audio_url") or f"/v1/audio/{job_id}"
            url = audio_url if str(audio_url).startswith(("http://", "https://")) else urljoin(base + "/", str(audio_url).lstrip("/"))
            output.parent.mkdir(parents=True, exist_ok=True)
            with requests.get(url, headers=headers, stream=True, timeout=300) as audio:
                audio.raise_for_status()
                with output.open("wb") as handle:
                    for chunk in audio.iter_content(1024 * 1024):
                        if chunk:
                            handle.write(chunk)
            probe = probe_real_audio(output, minimum_seconds=2.0)
            if not probe.valid:
                raise RuntimeError(f"YuE smoke output failed waveform validation: {probe.error}")
            return {
                "engine": "yue",
                "real_audio": True,
                "duration_seconds": probe.duration_seconds,
                "sample_rate": probe.sample_rate,
                "channels": probe.channels,
                "output": str(output),
            }
        time.sleep(max(2, int(os.getenv("AURA_YUE_POLL_SECONDS", "5"))))
    raise TimeoutError("YuE smoke generation timed out")


def smoke(engine: str = "auto", output: Path | None = None) -> dict:
    status = renderer_runtime_status()
    if engine == "auto":
        engine = "ace" if any(x.get("id") == "ace-step" and x.get("reachable") for x in status["engines"]) else "yue"
    suffix = "ace" if engine in {"ace", "ace-step"} else "yue"
    out = output or Path(tempfile.gettempdir()) / f"esp_renderer_smoke_{suffix}.wav"
    if engine in {"ace", "ace-step"}:
        return _ace_smoke(out)
    if engine == "yue":
        return _yue_smoke(out)
    raise ValueError("engine must be auto, ace or yue")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove ESP Live Sound Studio can produce decodable neural waveform audio")
    parser.add_argument("--engine", choices=("auto", "ace", "yue"), default="auto")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    result = smoke(args.engine, args.output)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
