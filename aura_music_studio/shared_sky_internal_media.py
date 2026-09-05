from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse
from uuid import uuid4


class SharedSkyInternalMediaError(RuntimeError):
    pass


_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_RENDITIONS = {
    "1080p": {"height": 1080, "bitrate": 4_500_000, "audio": "160k"},
    "720p": {"height": 720, "bitrate": 2_500_000, "audio": "128k"},
    "480p": {"height": 480, "bitrate": 1_200_000, "audio": "128k"},
    "360p": {"height": 360, "bitrate": 700_000, "audio": "96k"},
}
_ALLOWED_INPUT_SCHEMES = {"rtmp", "rtmps", "srt", "http", "https"}


def _bool(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or default).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(low, min(high, value))


def _safe_id(value: str, *, label: str) -> str:
    clean = (value or "").strip()
    if not _SAFE_ID.fullmatch(clean):
        raise SharedSkyInternalMediaError(f"Invalid Shared Sky {label}")
    return clean


def _safe_input_url(value: str) -> str:
    clean = (value or "").strip()
    parsed = urlparse(clean)
    if parsed.scheme.lower() not in _ALLOWED_INPUT_SCHEMES or not parsed.netloc:
        raise SharedSkyInternalMediaError("Shared Sky internal media input URL is invalid")
    if parsed.username or parsed.password:
        raise SharedSkyInternalMediaError("Shared Sky internal media input URL must not embed credentials")
    if any(ch in clean for ch in ("\n", "\r", "\x00")):
        raise SharedSkyInternalMediaError("Shared Sky internal media input URL is invalid")
    return clean


def _normalise_renditions(profile: dict | None) -> list[str]:
    raw = (profile or {}).get("renditions")
    if isinstance(raw, str):
        requested = [item.strip() for item in raw.split(",")]
    elif isinstance(raw, list):
        requested = [str(item).strip() for item in raw]
    else:
        requested = ["720p", "480p"]
    result: list[str] = []
    for item in requested:
        lowered = item.lower().replace("30fps", "").replace("60fps", "")
        lowered = lowered.replace("30", "").replace("60", "")
        match = next((key for key in _RENDITIONS if lowered.startswith(key)), None)
        if match and match not in result:
            result.append(match)
        if len(result) >= 3:
            break
    return result or ["720p"]


@dataclass(frozen=True)
class InternalMediaSettings:
    enabled: bool
    media_root: Path | None
    recording_root: Path | None
    ffmpeg_binary: str
    ffprobe_binary: str
    segment_seconds: int
    window_segments: int

    @classmethod
    def from_env(cls) -> "InternalMediaSettings":
        media_raw = (os.getenv("SHARED_SKY_INTERNAL_MEDIA_ROOT") or "").strip()
        recording_raw = (os.getenv("SHARED_SKY_RECORDING_LOCAL_ROOT") or "").strip()
        return cls(
            enabled=_bool("SHARED_SKY_INTERNAL_MEDIA_ENABLED"),
            media_root=Path(media_raw).expanduser().resolve() if media_raw else None,
            recording_root=Path(recording_raw).expanduser().resolve() if recording_raw else None,
            ffmpeg_binary=(os.getenv("SHARED_SKY_FFMPEG_BIN") or "ffmpeg").strip() or "ffmpeg",
            ffprobe_binary=(os.getenv("SHARED_SKY_FFPROBE_BIN") or "ffprobe").strip() or "ffprobe",
            segment_seconds=_int("SHARED_SKY_HLS_SEGMENT_SECONDS", 2, 1, 6),
            window_segments=_int("SHARED_SKY_HLS_WINDOW_SEGMENTS", 6, 3, 30),
        )


@dataclass(frozen=True)
class InternalMediaHealth:
    enabled: bool
    configured: bool
    ffmpeg_available: bool
    ffprobe_available: bool
    media_root: str | None
    recording_root_configured: bool
    active_jobs: int
    runtime_mode: str = "web-process-media-supervisor"


@dataclass
class MediaProcess:
    job_id: str
    broadcast_id: str
    kind: str
    rendition: str | None
    process: subprocess.Popen
    output_path: Path


class SharedSkyInternalMediaSupervisor:
    """FFmpeg-backed first-party HLS and local recording supervisor.

    This is the self-host/single-process media runtime. Durable ownership remains in the
    transport database so Chat 10 can replace this process-local supervisor with dedicated
    workers without changing Chat 3/4 contracts. It never claims cluster failover.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._jobs: dict[str, MediaProcess] = {}

    @property
    def settings(self) -> InternalMediaSettings:
        return InternalMediaSettings.from_env()

    def health(self) -> InternalMediaHealth:
        settings = self.settings
        with self._lock:
            self._prune()
            active = len(self._jobs)
        return InternalMediaHealth(
            enabled=settings.enabled,
            configured=bool(settings.enabled and settings.media_root),
            ffmpeg_available=bool(shutil.which(settings.ffmpeg_binary)),
            ffprobe_available=bool(shutil.which(settings.ffprobe_binary)),
            media_root=str(settings.media_root) if settings.media_root else None,
            recording_root_configured=bool(settings.recording_root),
            active_jobs=active,
        )

    def _prune(self) -> None:
        finished = [job_id for job_id, item in self._jobs.items() if item.process.poll() is not None]
        for job_id in finished:
            self._jobs.pop(job_id, None)

    def _require_runtime(self) -> InternalMediaSettings:
        settings = self.settings
        if not settings.enabled:
            raise SharedSkyInternalMediaError("Shared Sky internal media runtime is disabled")
        if not settings.media_root:
            raise SharedSkyInternalMediaError("SHARED_SKY_INTERNAL_MEDIA_ROOT is not configured")
        if not shutil.which(settings.ffmpeg_binary):
            raise SharedSkyInternalMediaError("FFmpeg is unavailable for Shared Sky internal media")
        settings.media_root.mkdir(parents=True, exist_ok=True)
        return settings

    @staticmethod
    def _inside(root: Path, candidate: Path) -> Path:
        root = root.resolve()
        candidate = candidate.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise SharedSkyInternalMediaError("Shared Sky media path escaped its configured root") from exc
        return candidate

    def broadcast_root(self, broadcast_id: str) -> Path:
        settings = self.settings
        if not settings.media_root:
            raise SharedSkyInternalMediaError("SHARED_SKY_INTERNAL_MEDIA_ROOT is not configured")
        safe = _safe_id(broadcast_id, label="broadcast ID")
        return self._inside(settings.media_root, settings.media_root / safe)

    def playback_asset(self, broadcast_id: str, asset_path: str) -> Path:
        root = self.broadcast_root(broadcast_id)
        clean = (asset_path or "").strip().lstrip("/")
        if not clean or "\x00" in clean:
            raise SharedSkyInternalMediaError("Invalid Shared Sky playback asset")
        candidate = self._inside(root, root / clean)
        if candidate.suffix.lower() not in {".m3u8", ".ts", ".m4s"}:
            raise SharedSkyInternalMediaError("Unsupported Shared Sky playback asset type")
        return candidate

    def _spawn(self, *, job_id: str, broadcast_id: str, kind: str, rendition: str | None, args: list[str], output_path: Path) -> MediaProcess:
        process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        item = MediaProcess(job_id, broadcast_id, kind, rendition, process, output_path)
        with self._lock:
            self._jobs[job_id] = item
        return item

    def start_hls(self, *, broadcast_id: str, input_url: str, profile: dict | None=None) -> list[dict]:
        settings = self._require_runtime()
        source = _safe_input_url(input_url)
        broadcast_id = _safe_id(broadcast_id, label="broadcast ID")
        root = self.broadcast_root(broadcast_id)
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)
        renditions = _normalise_renditions(profile)
        started: list[MediaProcess] = []
        try:
            for rendition in renditions:
                spec = _RENDITIONS[rendition]
                target = root / rendition
                target.mkdir(parents=True, exist_ok=True)
                playlist = target / "index.m3u8"
                segment = target / "segment_%09d.ts"
                job_id = f"media_{uuid4().hex}"
                args = [
                    settings.ffmpeg_binary,
                    "-hide_banner",
                    "-loglevel",
                    "warning",
                    "-nostdin",
                    "-y",
                    "-reconnect",
                    "1",
                    "-reconnect_streamed",
                    "1",
                    "-reconnect_delay_max",
                    "5",
                    "-i",
                    source,
                    "-map",
                    "0:v?",
                    "-map",
                    "0:a?",
                    "-vf",
                    f"scale=-2:{spec['height']}:force_original_aspect_ratio=decrease",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-profile:v",
                    "high",
                    "-pix_fmt",
                    "yuv420p",
                    "-b:v",
                    str(spec["bitrate"]),
                    "-maxrate",
                    str(int(spec["bitrate"] * 1.12)),
                    "-bufsize",
                    str(spec["bitrate"] * 2),
                    "-g",
                    str(settings.segment_seconds * 30),
                    "-keyint_min",
                    str(settings.segment_seconds * 30),
                    "-sc_threshold",
                    "0",
                    "-c:a",
                    "aac",
                    "-b:a",
                    spec["audio"],
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-f",
                    "hls",
                    "-hls_time",
                    str(settings.segment_seconds),
                    "-hls_list_size",
                    str(settings.window_segments),
                    "-hls_flags",
                    "delete_segments+append_list+independent_segments+program_date_time+temp_file",
                    "-hls_segment_filename",
                    str(segment),
                    str(playlist),
                ]
                started.append(
                    self._spawn(
                        job_id=job_id,
                        broadcast_id=broadcast_id,
                        kind="hls",
                        rendition=rendition,
                        args=args,
                        output_path=playlist,
                    )
                )
            self._write_master(root, renditions)
        except Exception:
            for item in started:
                self.stop_job(item.job_id)
            raise
        return [self._public_job(item) for item in started]

    @staticmethod
    def _write_master(root: Path, renditions: list[str]) -> None:
        lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-INDEPENDENT-SEGMENTS"]
        for rendition in renditions:
            spec = _RENDITIONS[rendition]
            lines.append(
                f'#EXT-X-STREAM-INF:BANDWIDTH={int(spec["bitrate"] * 1.25)},AVERAGE-BANDWIDTH={spec["bitrate"]},NAME="{rendition}"'
            )
            lines.append(f"{rendition}/index.m3u8")
        tmp = root / "master.m3u8.tmp"
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(root / "master.m3u8")

    def start_recording(self, *, broadcast_id: str, input_url: str, kind: str="programme") -> dict:
        settings = self.settings
        if not settings.enabled or not settings.recording_root:
            raise SharedSkyInternalMediaError("Shared Sky local recording runtime is not configured")
        if not shutil.which(settings.ffmpeg_binary):
            raise SharedSkyInternalMediaError("FFmpeg is unavailable for Shared Sky recording")
        source = _safe_input_url(input_url)
        broadcast_id = _safe_id(broadcast_id, label="broadcast ID")
        kind = _safe_id(kind, label="recording kind")
        root = self._inside(settings.recording_root, settings.recording_root / broadcast_id)
        root.mkdir(parents=True, exist_ok=True)
        target = self._inside(root, root / f"{kind}_{uuid4().hex}.mkv")
        job_id = f"media_{uuid4().hex}"
        args = [
            settings.ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-y",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
            "-i",
            source,
            "-map",
            "0:v?",
            "-map",
            "0:a?",
            "-c",
            "copy",
            "-f",
            "matroska",
            str(target),
        ]
        item = self._spawn(
            job_id=job_id,
            broadcast_id=broadcast_id,
            kind=f"recording:{kind}",
            rendition=None,
            args=args,
            output_path=target,
        )
        return self._public_job(item)

    def stop_job(self, job_id: str, *, timeout_seconds: float=5.0) -> dict | None:
        with self._lock:
            item = self._jobs.pop(job_id, None)
        if not item:
            return None
        if item.process.poll() is None:
            item.process.terminate()
            try:
                item.process.wait(timeout=max(0.5, timeout_seconds))
            except subprocess.TimeoutExpired:
                item.process.kill()
                item.process.wait(timeout=2)
        result = self._public_job(item)
        result["returncode"] = item.process.poll()
        return result

    def stop_broadcast(self, broadcast_id: str, *, kinds: Iterable[str] | None=None) -> list[dict]:
        allowed = set(kinds or [])
        with self._lock:
            job_ids = [
                job_id
                for job_id, item in self._jobs.items()
                if item.broadcast_id == broadcast_id and (not allowed or item.kind in allowed or any(item.kind.startswith(value + ":") for value in allowed))
            ]
        results = []
        for job_id in sorted(job_ids):
            result = self.stop_job(job_id)
            if result:
                results.append(result)
        return results

    def state(self, job_id: str) -> dict:
        with self._lock:
            item = self._jobs.get(job_id)
            if not item:
                return {"running": False, "managed": False, "pid": None, "returncode": None}
            code = item.process.poll()
            if code is not None:
                self._jobs.pop(job_id, None)
                return {"running": False, "managed": True, "pid": item.process.pid, "returncode": code}
            return {"running": True, "managed": True, "pid": item.process.pid, "returncode": None}

    @staticmethod
    def _public_job(item: MediaProcess) -> dict:
        return {
            "job_id": item.job_id,
            "broadcast_id": item.broadcast_id,
            "kind": item.kind,
            "rendition": item.rendition,
            "pid": int(item.process.pid),
            "output_path": str(item.output_path),
        }

    def recording_metadata(self, path: str | Path) -> dict:
        target = Path(path).resolve()
        if not target.exists() or not target.is_file():
            return {"exists": False, "size_bytes": None, "checksum_sha256": None, "duration_ms": None}
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        duration_ms = None
        settings = self.settings
        if shutil.which(settings.ffprobe_binary):
            try:
                result = subprocess.run(
                    [
                        settings.ffprobe_binary,
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "json",
                        str(target),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                payload = json.loads(result.stdout or "{}")
                duration = float((payload.get("format") or {}).get("duration") or 0)
                duration_ms = max(0, int(duration * 1000))
            except (subprocess.SubprocessError, ValueError, TypeError, json.JSONDecodeError):
                duration_ms = None
        return {
            "exists": True,
            "size_bytes": target.stat().st_size,
            "checksum_sha256": digest.hexdigest(),
            "duration_ms": duration_ms,
        }


internal_media = SharedSkyInternalMediaSupervisor()


__all__ = [
    "InternalMediaHealth",
    "InternalMediaSettings",
    "SharedSkyInternalMediaError",
    "SharedSkyInternalMediaSupervisor",
    "internal_media",
]
