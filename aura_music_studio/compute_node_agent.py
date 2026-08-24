from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import __version__
from .node_transfer import build_result_bundle, extract_project_bundle


def _truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _safe_coordinator_url(value: str) -> str:
    url = value.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.netloc:
        return url
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return url
    if parsed.scheme == "http" and _truthy("LSS_NODE_ALLOW_HTTP", False):
        return url
    raise ValueError("ESP compute nodes require an HTTPS coordinator URL unless HTTP is explicitly enabled by the owner")


def collect_hardware() -> dict:
    result = {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
    }
    if shutil.which("nvidia-smi"):
        try:
            text = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                text=True,
                timeout=10,
            ).strip()
            result["nvidia_gpus"] = [line.strip() for line in text.splitlines() if line.strip()]
        except Exception as exc:
            result["nvidia_error"] = f"{type(exc).__name__}: {exc}"
    return result


def collect_software() -> dict:
    result = {
        "live_sound_studio_version": __version__,
        "python": platform.python_version(),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "git": bool(shutil.which("git")),
        "video_engines": {},
    }
    try:
        from .acestep_api import AceStepClient
        url = (os.getenv("AURA_ACESTEP_API_URL") or "").strip()
        result["acestep_configured"] = bool(url)
        result["acestep_reachable"] = bool(url and AceStepClient(base_url=url).health())
    except Exception:
        result["acestep_reachable"] = False
    try:
        from .video_engines import public_video_engine_status
        result["video_engines"] = {
            item["id"]: bool(item["configured"])
            for item in public_video_engine_status()
        }
    except Exception:
        result["video_engines"] = {}
    return result


def write_node_env(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                existing[key.strip()] = value
    existing.update(values)
    lines = ["# ESP Live Sound Studio compute-node credential file — do not commit or share"]
    for key in sorted(existing):
        lines.append(f"{key}={existing[key]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def enroll_node(
    coordinator_url: str,
    enrollment_token: str,
    *,
    name: str | None = None,
    capabilities: list[str] | None = None,
    env_path: Path = Path(".env.node"),
) -> dict:
    base = _safe_coordinator_url(coordinator_url)
    caps = capabilities or ["music_generation", "engineering"]
    response = requests.post(
        f"{base}/node-coordinator/enroll",
        json={
            "token": enrollment_token,
            "name": name or socket.gethostname(),
            "capabilities": caps,
            "hardware": collect_hardware(),
            "software": collect_software(),
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    write_node_env(env_path, {
        "LSS_NODE_COORDINATOR_URL": base,
        "LSS_NODE_ID": data["node_id"],
        "LSS_NODE_SECRET": data["node_secret"],
        "LSS_NODE_NAME": data.get("name") or name or socket.gethostname(),
        "LSS_NODE_CAPABILITIES": ",".join(data.get("capabilities") or caps),
    })
    return {
        "node_id": data["node_id"],
        "name": data.get("name"),
        "capabilities": data.get("capabilities"),
        "credential_written_to": str(env_path),
        "node_secret_returned_to_console": False,
    }


class ESPComputeNodeAgent:
    """Outbound-only worker agent for ESP-controlled compute machines."""

    def __init__(self):
        self.base = _safe_coordinator_url(os.getenv("LSS_NODE_COORDINATOR_URL") or "")
        self.node_id = (os.getenv("LSS_NODE_ID") or "").strip()
        self.secret = (os.getenv("LSS_NODE_SECRET") or "").strip()
        self.name = (os.getenv("LSS_NODE_NAME") or socket.gethostname()).strip()
        self.capabilities = [x.strip().lower() for x in (os.getenv("LSS_NODE_CAPABILITIES") or "music_generation,engineering").split(",") if x.strip()]
        self.work_root = Path(os.getenv("LSS_NODE_WORK_DIR", "data/node_work"))
        self.work_root.mkdir(parents=True, exist_ok=True)
        if not self.node_id or not self.secret:
            raise RuntimeError("This machine is not enrolled as an ESP compute node")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.secret}",
            "X-ESP-Node-ID": self.node_id,
            "User-Agent": f"ESP-Live-Sound-Studio-Node/{__version__}",
        }

    def heartbeat(self) -> dict:
        response = requests.post(
            f"{self.base}/node-coordinator/heartbeat",
            headers=self._headers(),
            json={
                "capabilities": self.capabilities,
                "hardware": collect_hardware(),
                "software": collect_software(),
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def claim(self) -> dict | None:
        response = requests.post(
            f"{self.base}/node-coordinator/claim",
            headers=self._headers(),
            timeout=45,
        )
        response.raise_for_status()
        return response.json().get("job")

    def renew_lease(self, job: dict) -> bool:
        response = requests.post(
            f"{self.base}{job['lease_url']}",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return bool(response.json().get("renewed"))

    def _lease_loop(self, job: dict, stop: threading.Event) -> None:
        interval = max(10, int(os.getenv("LSS_NODE_HEARTBEAT_SECONDS", "30")))
        while not stop.wait(interval):
            try:
                self.renew_lease(job)
            except Exception:
                # Temporary connectivity loss does not kill a local render. If the lease truly expires,
                # the coordinator rejects the eventual result rather than accepting duplicate ownership.
                continue

    def _download(self, url: str, destination: Path) -> None:
        maximum = max(64 * 1024 * 1024, int(os.getenv("LSS_NODE_MAX_BUNDLE_BYTES", str(2 * 1024**3))))
        with requests.get(f"{self.base}{url}", headers=self._headers(), stream=True, timeout=(30, 1800)) as response:
            response.raise_for_status()
            total = 0
            with destination.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > maximum:
                        raise ValueError("Coordinator project bundle exceeded configured node limit")
                    handle.write(chunk)

    @staticmethod
    def _execute(job: dict, project: Path) -> dict:
        job_type = job["job_type"]
        payload = job.get("payload") or {}
        if job_type == "produce":
            from .pipeline import AuraPipeline
            return AuraPipeline(project).run()
        if job_type == "build_around":
            from .build_around import BuildAroundRequest, build_around_upload
            return build_around_upload(project, BuildAroundRequest.model_validate(payload))
        if job_type.startswith("engineering:"):
            from .engineering_jobs import run_engineering_job
            return run_engineering_job(project, payload)
        if job_type.startswith("video:"):
            from .video_jobs import run_video_job
            return run_video_job(project, job_type, payload)
        raise ValueError(f"Unsupported ESP node job type: {job_type}")

    def _upload_result(self, job: dict, result_bundle: Path) -> dict:
        with result_bundle.open("rb") as handle:
            response = requests.post(
                f"{self.base}{job['result_url']}",
                headers=self._headers(),
                files={"result": (result_bundle.name, handle, "application/zip")},
                timeout=(30, 1800),
            )
        response.raise_for_status()
        return response.json()

    def _report_failure(self, job: dict, error: str) -> None:
        try:
            requests.post(
                f"{self.base}{job['fail_url']}",
                headers=self._headers(),
                json={"error": error[:8000]},
                timeout=30,
            ).raise_for_status()
        except Exception:
            pass

    def run_once(self) -> dict | None:
        self.heartbeat()
        job = self.claim()
        if not job:
            return None
        job_dir = Path(tempfile.mkdtemp(prefix=f"esp-node-{job['id'][:8]}-", dir=self.work_root))
        lease_stop = threading.Event()
        lease_thread = threading.Thread(target=self._lease_loop, args=(job, lease_stop), daemon=True)
        lease_thread.start()
        try:
            bundle = job_dir / "job.zip"
            project = job_dir / "project"
            self._download(job["bundle_url"], bundle)
            manifest = extract_project_bundle(bundle, project)
            manifest_job = manifest.get("job") or {}
            if manifest_job.get("id") != job["id"] or manifest_job.get("job_type") != job["job_type"]:
                raise ValueError("Coordinator bundle metadata does not match the claimed job")
            executable_job = {**job, "payload": manifest_job.get("payload") or {}}
            result = self._execute(executable_job, project)
            result_bundle = job_dir / "result.zip"
            build_result_bundle(project, job["id"], result_bundle)
            accepted = self._upload_result(job, result_bundle)
            return {
                "job_id": job["id"],
                "status": "completed",
                "coordinator_accepted": bool(accepted.get("accepted")),
                "local_result_summary": result,
            }
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            self._report_failure(job, message)
            return {"job_id": job["id"], "status": "failed", "error": message}
        finally:
            lease_stop.set()
            lease_thread.join(timeout=2)
            shutil.rmtree(job_dir, ignore_errors=True)

    def serve_forever(self) -> None:
        heartbeat_seconds = max(10, int(os.getenv("LSS_NODE_HEARTBEAT_SECONDS", "30")))
        idle_seconds = min(heartbeat_seconds, 10)
        while True:
            try:
                result = self.run_once()
                if result is None:
                    time.sleep(idle_seconds)
            except Exception:
                time.sleep(heartbeat_seconds)


def main() -> None:
    ESPComputeNodeAgent().serve_forever()


if __name__ == "__main__":
    main()
