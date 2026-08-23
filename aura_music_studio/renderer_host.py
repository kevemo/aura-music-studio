from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


MODES = {
    "ace": ["docker-compose.yml", "docker-compose.gpu.yml"],
    "yue": ["docker-compose.yml", "docker-compose.yue.yml"],
    "both": ["docker-compose.yml", "docker-compose.gpu.yml", "docker-compose.yue.yml"],
}


@dataclass(frozen=True)
class HostRendererStatus:
    docker_present: bool
    compose_present: bool
    nvidia_smi_present: bool
    nvidia_gpu_visible: bool
    ready_to_start: bool
    notes: list[str]


def _run(command: list[str], *, timeout: int = 20, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=check)


def host_renderer_status() -> HostRendererStatus:
    notes: list[str] = []
    docker_present = shutil.which("docker") is not None
    compose_present = False
    if docker_present:
        try:
            compose_present = _run(["docker", "compose", "version"], timeout=10).returncode == 0
        except Exception:
            compose_present = False
    if not docker_present:
        notes.append("Docker is not installed or is not on PATH.")
    elif not compose_present:
        notes.append("Docker Compose v2 is not available through `docker compose`.")

    nvidia_smi_present = shutil.which("nvidia-smi") is not None
    nvidia_gpu_visible = False
    if nvidia_smi_present:
        try:
            result = _run(["nvidia-smi", "-L"], timeout=10)
            nvidia_gpu_visible = result.returncode == 0 and "GPU" in (result.stdout or "")
        except Exception:
            nvidia_gpu_visible = False
    if not nvidia_smi_present:
        notes.append("NVIDIA driver tools are not installed or not on PATH.")
    elif not nvidia_gpu_visible:
        notes.append("NVIDIA tools are present but no usable GPU was reported.")

    return HostRendererStatus(
        docker_present=docker_present,
        compose_present=compose_present,
        nvidia_smi_present=nvidia_smi_present,
        nvidia_gpu_visible=nvidia_gpu_visible,
        ready_to_start=bool(docker_present and compose_present and nvidia_gpu_visible),
        notes=notes,
    )


def _read_env(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return lines, values


def ensure_renderer_secrets(env_path: Path = Path(".env"), *, mode: str = "ace") -> dict:
    if mode not in MODES:
        raise ValueError(f"Unknown renderer mode: {mode}")
    lines, values = _read_env(env_path)
    required = ["ACESTEP_API_KEY"] if mode == "ace" else ["YUE_API_KEY"] if mode == "yue" else ["ACESTEP_API_KEY", "YUE_API_KEY"]
    generated: list[str] = []
    for key in required:
        if not values.get(key):
            value = secrets.token_urlsafe(48)
            values[key] = value
            generated.append(key)
            replaced = False
            for index, line in enumerate(lines):
                if line.strip().startswith(key + "="):
                    lines[index] = f"{key}={value}"
                    replaced = True
                    break
            if not replaced:
                lines.append(f"{key}={value}")
    if "AURA_REQUIRE_LIVE_RENDERER" not in values:
        lines.append("AURA_REQUIRE_LIVE_RENDERER=true")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass
    return {"env": str(env_path), "generated_secret_names": generated, "secret_values_exposed": False}


def compose_command(mode: str, *tail: str, env_path: Path = Path(".env")) -> list[str]:
    if mode not in MODES:
        raise ValueError(f"Unknown renderer mode: {mode}")
    command = ["docker", "compose", "--env-file", str(env_path)]
    for filename in MODES[mode]:
        command += ["-f", filename]
    command += list(tail)
    return command


def _command_text(command: list[str]) -> str:
    import shlex
    return " ".join(shlex.quote(part) for part in command)


def start_renderers(*, mode: str = "ace", env_path: Path = Path(".env"), build: bool = True) -> dict:
    status = host_renderer_status()
    if not status.ready_to_start:
        raise RuntimeError("Renderer host is not ready: " + " ".join(status.notes))
    secrets_result = ensure_renderer_secrets(env_path, mode=mode)
    tail = ["up", "-d"] + (["--build"] if build else [])
    command = compose_command(mode, *tail, env_path=env_path)
    subprocess.run(command, check=True)
    smoke = compose_command(mode, "exec", "live-sound-studio", "aura", "renderer-smoke", env_path=env_path)
    return {
        "mode": mode,
        "started": True,
        "compose_files": MODES[mode],
        "secrets": secrets_result,
        "next_smoke_test": _command_text(smoke),
    }


def stop_renderers(*, mode: str = "ace", env_path: Path = Path(".env")) -> dict:
    status = host_renderer_status()
    if not status.docker_present or not status.compose_present:
        raise RuntimeError("Docker Compose is unavailable")
    command = compose_command(mode, "down", env_path=env_path)
    subprocess.run(command, check=True)
    return {"mode": mode, "stopped": True, "volumes_preserved": True}
