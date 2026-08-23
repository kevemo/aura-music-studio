from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT = Path(os.getenv("YUE_WORK_ROOT", "/var/lib/yue")).resolve()
YUE_ROOT = Path(os.getenv("YUE_SOURCE_ROOT", "/opt/yue")).resolve()
INFERENCE = YUE_ROOT / "inference" / "infer.py"
ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="ESP Private YuE Renderer", version="1.0")
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yue-gpu")
lock = threading.Lock()
jobs: dict[str, dict] = {}


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4096)
    lyrics: str = Field(min_length=1, max_length=20000)
    segments: int = Field(default=2, ge=1, le=8)
    seed: int = 42
    max_new_tokens: int = Field(default=3000, ge=256, le=6000)
    stage2_batch_size: int = Field(default=4, ge=1, le=16)
    stage1_model: str = "m-a-p/YuE-s1-7B-anneal-en-cot"
    stage2_model: str = "m-a-p/YuE-s2-1B-general"


def _auth(authorization: str | None = Header(default=None)) -> None:
    key = (os.getenv("YUE_API_KEY") or "").strip()
    if not key:
        return
    if authorization != f"Bearer {key}":
        raise HTTPException(status_code=401, detail="Invalid renderer credential")


def _safe_text(value: str, limit: int) -> str:
    return value.replace("\x00", "").strip()[:limit]


def _final_candidate(output_dir: Path) -> Path:
    candidates = [p for p in output_dir.iterdir() if p.is_file() and p.suffix.lower() in {".mp3", ".wav", ".flac"}]
    if not candidates:
        candidates = [p for p in output_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp3", ".wav", ".flac"} and "mix" in p.name.lower()]
    if not candidates:
        raise RuntimeError("YuE completed but no final audio mix was found")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _run_job(job_id: str, request: GenerateRequest) -> None:
    job_dir = ROOT / "jobs" / job_id
    output_dir = job_dir / "output"
    prompt_file = job_dir / "genre.txt"
    lyrics_file = job_dir / "lyrics.txt"
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(_safe_text(request.prompt, 4096), encoding="utf-8")
    lyrics_file.write_text(_safe_text(request.lyrics, 20000), encoding="utf-8")
    with lock:
        jobs[job_id].update({"status": "running", "started_at": time.time()})

    try:
        if not INFERENCE.is_file():
            raise FileNotFoundError(f"YuE inference script missing: {INFERENCE}")
        command = [
            sys.executable, str(INFERENCE),
            "--cuda_idx", os.getenv("YUE_CUDA_IDX", "0"),
            "--stage1_model", request.stage1_model,
            "--stage2_model", request.stage2_model,
            "--genre_txt", str(prompt_file),
            "--lyrics_txt", str(lyrics_file),
            "--run_n_segments", str(request.segments),
            "--stage2_batch_size", str(request.stage2_batch_size),
            "--output_dir", str(output_dir),
            "--max_new_tokens", str(request.max_new_tokens),
            "--repetition_penalty", os.getenv("YUE_REPETITION_PENALTY", "1.1"),
            "--seed", str(request.seed),
            "--rescale",
        ]
        log_path = job_dir / "render.log"
        with log_path.open("w", encoding="utf-8") as log:
            subprocess.run(
                command,
                cwd=YUE_ROOT / "inference",
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=int(os.getenv("YUE_JOB_TIMEOUT_SECONDS", "7200")),
            )
        source = _final_candidate(output_dir)
        final = job_dir / "final_48k.wav"
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-i", str(source), "-ar", "48000", "-ac", "2",
                "-c:a", "pcm_s24le", str(final),
            ],
            check=True,
            timeout=300,
        )
        if not final.is_file() or final.stat().st_size < 4096:
            raise RuntimeError("YuE final normalization produced no usable audio")
        with lock:
            jobs[job_id].update({
                "status": "completed",
                "completed_at": time.time(),
                "audio": str(final),
                "audio_url": f"/v1/audio/{job_id}",
            })
    except Exception as exc:
        with lock:
            jobs[job_id].update({
                "status": "failed",
                "completed_at": time.time(),
                "error": f"{type(exc).__name__}: {exc}",
            })


@app.get("/health")
def health():
    """Private-network health only; no secrets or model paths are returned."""
    with lock:
        running = sum(1 for item in jobs.values() if item.get("status") == "running")
        queued = sum(1 for item in jobs.values() if item.get("status") == "queued")
    return {
        "ok": True,
        "busy": bool(running or queued),
        "running": running,
        "queued": queued,
        "inference_present": INFERENCE.is_file(),
        "ffmpeg_present": shutil.which("ffmpeg") is not None,
        "gpu_visible": shutil.which("nvidia-smi") is not None,
    }


@app.post("/v1/jobs", status_code=202)
def create_job(request: GenerateRequest, _: None = Depends(_auth)):
    job_id = uuid.uuid4().hex
    with lock:
        jobs[job_id] = {"job_id": job_id, "status": "queued", "created_at": time.time()}
    executor.submit(_run_job, job_id, request)
    return {"job_id": job_id, "status": "queued"}


@app.get("/v1/jobs/{job_id}")
def job_status(job_id: str, _: None = Depends(_auth)):
    with lock:
        item = jobs.get(job_id)
        if not item:
            raise HTTPException(status_code=404, detail="Unknown YuE render job")
        public = {key: value for key, value in item.items() if key not in {"audio"}}
    return public


@app.get("/v1/audio/{job_id}")
def job_audio(job_id: str, _: None = Depends(_auth)):
    with lock:
        item = jobs.get(job_id)
        if not item:
            raise HTTPException(status_code=404, detail="Unknown YuE render job")
        if item.get("status") != "completed" or not item.get("audio"):
            raise HTTPException(status_code=409, detail="YuE render is not complete")
        path = Path(str(item["audio"])).resolve()
    if ROOT != path and ROOT not in path.parents:
        raise HTTPException(status_code=403, detail="Renderer output path rejected")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Rendered audio no longer exists")
    return FileResponse(path, media_type="audio/wav", filename=f"yue_{job_id}.wav")
