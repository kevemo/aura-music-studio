from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests


def _lyrics() -> str:
    path = (os.getenv("AURA_LYRICS") or "").strip()
    if not path:
        return ""
    p = Path(path)
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def _segments(lyrics: str) -> int:
    explicit = len(re.findall(r"(?m)^\s*\[[^\]]+\]\s*$", lyrics))
    maximum = max(1, int(os.getenv("AURA_YUE_MAX_SEGMENTS", "2")))
    requested = explicit if explicit else 2
    return max(1, min(requested, maximum))


def main() -> int:
    base = (os.getenv("AURA_YUE_API_URL") or "").strip().rstrip("/")
    output = Path(os.environ.get("AURA_OUTPUT", "neural_master_yue.wav"))
    prompt = (os.getenv("AURA_PROMPT") or "").strip()
    lyrics = _lyrics()
    if not base:
        raise RuntimeError("AURA_YUE_API_URL is not configured")
    if not lyrics:
        raise RuntimeError("YuE is a lyrics-first renderer and requires user/project lyrics")

    headers = {"Content-Type": "application/json"}
    if os.getenv("YUE_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['YUE_API_KEY']}"
    body = {
        "prompt": prompt,
        "lyrics": lyrics,
        "segments": _segments(lyrics),
        "seed": int(os.getenv("AURA_YUE_SEED", "42")),
        "max_new_tokens": int(os.getenv("AURA_YUE_MAX_NEW_TOKENS", "3000")),
        "stage2_batch_size": int(os.getenv("AURA_YUE_STAGE2_BATCH_SIZE", "4")),
        "stage1_model": os.getenv("AURA_YUE_STAGE1_MODEL", "m-a-p/YuE-s1-7B-anneal-en-cot"),
        "stage2_model": os.getenv("AURA_YUE_STAGE2_MODEL", "m-a-p/YuE-s2-1B-general"),
    }
    response = requests.post(f"{base}/v1/jobs", headers=headers, json=body, timeout=30)
    response.raise_for_status()
    job = response.json()
    job_id = str(job.get("job_id") or "")
    if not job_id:
        raise RuntimeError(f"YuE worker returned no job_id: {job}")

    deadline = time.time() + int(os.getenv("AURA_YUE_TIMEOUT", "7200"))
    poll = max(2, int(os.getenv("AURA_YUE_POLL_SECONDS", "5")))
    while time.time() < deadline:
        status = requests.get(f"{base}/v1/jobs/{job_id}", headers={k: v for k, v in headers.items() if k != "Content-Type"}, timeout=15)
        status.raise_for_status()
        data = status.json()
        state = str(data.get("status") or "").lower()
        if state == "failed":
            raise RuntimeError(f"YuE generation failed: {data.get('error') or data}")
        if state == "completed":
            audio_url = data.get("audio_url") or f"/v1/audio/{job_id}"
            url = audio_url if str(audio_url).startswith(("http://", "https://")) else urljoin(base + "/", str(audio_url).lstrip("/"))
            output.parent.mkdir(parents=True, exist_ok=True)
            with requests.get(url, headers={k: v for k, v in headers.items() if k != "Content-Type"}, stream=True, timeout=300) as audio:
                audio.raise_for_status()
                with output.open("wb") as handle:
                    for chunk in audio.iter_content(1024 * 1024):
                        if chunk:
                            handle.write(chunk)
            if not output.is_file() or output.stat().st_size < 4096:
                raise RuntimeError("YuE worker returned an empty/invalid audio file")
            print(json.dumps({"renderer": "yue", "job_id": job_id, "output": str(output)}))
            return 0
        time.sleep(poll)
    raise TimeoutError(f"YuE generation timed out: {job_id}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"YuE renderer bridge failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
