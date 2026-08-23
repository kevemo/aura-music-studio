from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter

from .compute_nodes import ComputeNodeRegistry
from .doctor import system_report
from .model_catalog import public_catalog

router = APIRouter(prefix="/system", tags=["Studio System"])


def _safe_backup_status() -> dict:
    path = Path(os.getenv("LSS_BACKUP_STATUS", "data/backup_scheduler_status.json"))
    value: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            value = loaded if isinstance(loaded, dict) else {}
        except Exception:
            value = {}
    last = value.get("last_result") if isinstance(value.get("last_result"), dict) else {}
    backup = last.get("backup")
    return {
        "automatic_enabled": os.getenv("LSS_AUTO_BACKUP_ENABLED", "false").lower() == "true",
        "interval_hours": int(os.getenv("LSS_AUTO_BACKUP_INTERVAL_HOURS", "24")),
        "retention_count": int(os.getenv("LSS_AUTO_BACKUP_KEEP", "7")),
        "last_success_at": value.get("last_success_at"),
        "last_failure_at": value.get("last_failure_at"),
        "last_ok": last.get("ok"),
        "last_archive": Path(str(backup)).name if backup else None,
        "encrypted_when_recipient_configured": bool(os.getenv("LSS_BACKUP_AGE_RECIPIENT")),
        "filesystem_path_exposed": False,
    }


def independence_status() -> dict:
    try:
        nodes = ComputeNodeRegistry().summary()
    except Exception as exc:
        nodes = {"enabled": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "self_host_first": True,
        "paid_domain_required": False,
        "cloud_host_required": False,
        "automatic_local_backups": _safe_backup_status(),
        "esp_compute_nodes": nodes,
        "compute_node_credentials_exposed": False,
        "compute_node_network_addresses_exposed": False,
    }


@router.get("/doctor")
def doctor():
    report = system_report()
    report["independence"] = independence_status()
    return report


@router.get("/independence")
def independence():
    return independence_status()


@router.get("/model-catalog")
def model_catalog():
    return {
        "policy": (
            "ESP Live Sound Studio never assumes that an open repository or downloadable checkpoint is "
            "commercially unrestricted. Code and model/voice licences are tracked separately."
        ),
        "components": public_catalog(),
    }
