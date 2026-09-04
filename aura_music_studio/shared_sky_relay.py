from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse


class SharedSkyRelayError(RuntimeError):
    pass


_ALLOWED_INPUT_SCHEMES = {"rtmp", "rtmps", "srt", "http", "https"}
_ALLOWED_OUTPUT_SCHEMES = {"rtmp", "rtmps", "srt", "rist"}


def _validate_url(value: str, *, output: bool) -> str:
    clean = (value or "").strip()
    parsed = urlparse(clean)
    allowed = _ALLOWED_OUTPUT_SCHEMES if output else _ALLOWED_INPUT_SCHEMES
    if parsed.scheme.lower() not in allowed or not parsed.netloc:
        kind = "output" if output else "input"
        raise SharedSkyRelayError(f"Unsupported or invalid Shared Sky {kind} URL")
    return clean


@dataclass(frozen=True)
class RelayHealth:
    enabled: bool
    ffmpeg_available: bool
    ffmpeg_binary: str
    active_outputs: int
    runtime_mode: str


@dataclass
class RelayProcess:
    output_id: str
    destination_id: str
    process: subprocess.Popen
    input_url: str
    output_url: str


class SharedSkyRelaySupervisor:
    """Fail-closed FFmpeg relay supervisor for the web-process development runtime.

    Each destination gets an independent FFmpeg process so one destination failure does
    not automatically terminate other outputs. Production deployments should run this
    same control contract in a dedicated relay worker/service rather than relying on a
    multi-worker web process to own child processes.
    """

    def __init__(self):
        self.enabled = os.getenv("SHARED_SKY_RELAY_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
        self.ffmpeg_binary = os.getenv("SHARED_SKY_FFMPEG_BIN", "ffmpeg").strip() or "ffmpeg"
        self._lock = threading.RLock()
        self._processes: dict[str, RelayProcess] = {}

    def health(self) -> RelayHealth:
        with self._lock:
            self._prune()
            return RelayHealth(
                enabled=self.enabled,
                ffmpeg_available=bool(shutil.which(self.ffmpeg_binary)),
                ffmpeg_binary=self.ffmpeg_binary,
                active_outputs=len(self._processes),
                runtime_mode="web-process-supervisor",
            )

    def _prune(self) -> None:
        finished = [key for key, item in self._processes.items() if item.process.poll() is not None]
        for key in finished:
            self._processes.pop(key, None)

    def start_output(
        self,
        *,
        output_id: str,
        destination_id: str,
        input_url: str,
        output_url: str,
        passthrough: bool = True,
    ) -> int:
        if not self.enabled:
            raise SharedSkyRelayError("Shared Sky relay is disabled by deployment configuration")
        if not shutil.which(self.ffmpeg_binary):
            raise SharedSkyRelayError("FFmpeg is not available in the Shared Sky runtime")
        source = _validate_url(input_url, output=False)
        destination = _validate_url(output_url, output=True)
        with self._lock:
            self._prune()
            current = self._processes.get(output_id)
            if current and current.process.poll() is None:
                raise SharedSkyRelayError("This Shared Sky output is already running")

            args = [
                self.ffmpeg_binary,
                "-hide_banner",
                "-loglevel",
                "warning",
                "-nostdin",
                "-reconnect",
                "1",
                "-reconnect_streamed",
                "1",
                "-reconnect_delay_max",
                "5",
                "-i",
                source,
            ]
            if passthrough:
                args += ["-map", "0:v?", "-map", "0:a?", "-c", "copy"]
            else:
                args += [
                    "-map",
                    "0:v?",
                    "-map",
                    "0:a?",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "160k",
                ]
            if destination.startswith(("rtmp://", "rtmps://")):
                args += ["-f", "flv"]
            elif destination.startswith("srt://"):
                args += ["-f", "mpegts"]
            args.append(destination)

            # Never use shell=True and never include destination URLs in application logs.
            process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            self._processes[output_id] = RelayProcess(
                output_id=output_id,
                destination_id=destination_id,
                process=process,
                input_url=source,
                output_url=destination,
            )
            return int(process.pid)

    def stop_output(self, output_id: str, *, timeout_seconds: float = 4.0) -> bool:
        with self._lock:
            item = self._processes.pop(output_id, None)
        if not item:
            return False
        if item.process.poll() is not None:
            return True
        item.process.terminate()
        try:
            item.process.wait(timeout=max(0.5, timeout_seconds))
        except subprocess.TimeoutExpired:
            item.process.kill()
            item.process.wait(timeout=2)
        return True

    def stop_many(self, output_ids: Iterable[str]) -> int:
        stopped = 0
        for output_id in list(output_ids):
            if self.stop_output(output_id):
                stopped += 1
        return stopped

    def output_state(self, output_id: str) -> dict:
        with self._lock:
            item = self._processes.get(output_id)
            if not item:
                return {"running": False, "pid": None, "returncode": None}
            returncode = item.process.poll()
            if returncode is not None:
                self._processes.pop(output_id, None)
                return {"running": False, "pid": item.process.pid, "returncode": returncode}
            return {"running": True, "pid": item.process.pid, "returncode": None}


relay = SharedSkyRelaySupervisor()


__all__ = ["SharedSkyRelayError", "SharedSkyRelaySupervisor", "RelayHealth", "relay"]
