"""Vercel entrypoint for the real Pulsar-Frequency House FastAPI application.

Vercel functions ship the repository as read-only code and provide /tmp for ephemeral
writes. The production/self-hosted deployment uses durable storage instead; this bootstrap
only supplies safe ephemeral defaults when the VERCEL environment variable is present.
No secrets or provider credentials are created here.
"""

from __future__ import annotations

import os
from pathlib import Path


def _configure_vercel_runtime() -> None:
    if not os.getenv("VERCEL"):
        return

    root = Path("/tmp/pulsar-frequency-house")
    data = root / "data"
    defaults = {
        "AURA_PROJECTS_ROOT": str(root / "projects"),
        "LSS_DB_PATH": str(data / "live_sound_studio.sqlite3"),
        "AURA_SOCIAL_ROOT": str(data / "social"),
        "AURA_SOCIAL_OAUTH_DB_PATH": str(data / "social_oauth.sqlite3"),
        "AURA_CHAT_ATTACHMENT_DIR": str(data / "aura" / "attachments"),
        "AURA_CHAT_SPEECH_DIR": str(data / "aura" / "speech"),
        "AURA_WEB_CACHE_DIR": str(data / "web_cache"),
        "LSS_PUBLIC_ADDRESS_STATUS": str(data / "public_address_status.json"),
        "LSS_BACKUP_DIR": str(root / "backups"),
        "LSS_BACKUP_STATUS": str(data / "backup_scheduler_status.json"),
        "LSS_NODE_WORK_DIR": str(data / "node_work"),
        # A serverless preview must never try router/DDNS or automatic backup management.
        "LSS_DDNS_PROVIDER": "none",
        "LSS_PUBLIC_IP_DISCOVERY": "false",
        "LSS_UPNP_DISCOVERY": "false",
        "LSS_UPNP_PORT_FORWARD": "false",
        "LSS_AUTO_BACKUP_ENABLED": "false",
        "LSS_COOKIE_SECURE": "true",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)

    host = (os.getenv("VERCEL_PROJECT_PRODUCTION_URL") or os.getenv("VERCEL_URL") or "").strip()
    if host:
        if not host.startswith(("http://", "https://")):
            host = f"https://{host}"
        os.environ.setdefault("LSS_PUBLIC_BASE_URL", host.rstrip("/"))

    for path_key in (
        "AURA_PROJECTS_ROOT",
        "AURA_SOCIAL_ROOT",
        "AURA_CHAT_ATTACHMENT_DIR",
        "AURA_CHAT_SPEECH_DIR",
        "AURA_WEB_CACHE_DIR",
        "LSS_BACKUP_DIR",
        "LSS_NODE_WORK_DIR",
    ):
        Path(os.environ[path_key]).mkdir(parents=True, exist_ok=True)
    Path(os.environ["LSS_DB_PATH"]).parent.mkdir(parents=True, exist_ok=True)
    Path(os.environ["AURA_SOCIAL_OAUTH_DB_PATH"]).parent.mkdir(parents=True, exist_ok=True)


_configure_vercel_runtime()

# Import only after writable paths and public base URL have been configured.
from app import app  # noqa: E402

__all__ = ["app"]
