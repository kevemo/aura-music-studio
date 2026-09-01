from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter

from .aura_sec_portal import router as aura_sec_portal_router
from .backup_scheduler import BackupScheduler
from .compute_capabilities import compatibility
from .compute_nodes import ComputeNodeRegistry
from .doctor import system_report
from .model_catalog import public_catalog
from .renderer_runtime import renderer_runtime_status

router = APIRouter(prefix="/system", tags=["Studio System"])


def _safe_backup_status() -> dict:
    scheduler = BackupScheduler()
    config = scheduler.configuration()
    value: dict = {}
    if scheduler.status_path.is_file():
        try:
            loaded = json.loads(scheduler.status_path.read_text(encoding="utf-8"))
            value = loaded if isinstance(loaded, dict) else {}
        except Exception:
            value = {}
    last = value.get("last_result") if isinstance(value.get("last_result"), dict) else {}
    backup = last.get("backup")
    return {
        "automatic_enabled": config["enabled"],
        "interval_hours": config["interval_hours"],
        "retention_count": config["retention_count"],
        "include_finished_outputs": config["include_outputs"],
        "include_work_files": config["include_work"],
        "last_success_at": value.get("last_success_at"),
        "last_failure_at": value.get("last_failure_at"),
        "last_ok": last.get("ok"),
        "last_archive": Path(str(backup)).name if backup else None,
        "encrypted_when_recipient_configured": config["encrypted_when_recipient_configured"],
        "filesystem_path_exposed": False,
        "encryption_recipient_exposed": False,
    }


def _safe_node_status() -> dict:
    try:
        registry = ComputeNodeRegistry()
        summary = registry.summary()
        nodes = registry.list_nodes(stale_after_seconds=max(60, int(os.getenv("LSS_NODE_HEARTBEAT_SECONDS", "30")) * 4))
        compatible_online = sum(
            1 for node in nodes
            if node.get("online") and node.get("status") != "revoked" and compatibility(node)["compatible"]
        )
        return {**summary, "compatible_online": compatible_online, "same_version_required": os.getenv("LSS_NODE_REQUIRE_SAME_VERSION", "true").lower() == "true"}
    except Exception as exc:
        return {"enabled": False, "error": f"{type(exc).__name__}: {exc}"}


def independence_status() -> dict:
    return {
        "self_host_first": True,
        "paid_domain_required": False,
        "cloud_host_required": False,
        "automatic_local_backups": _safe_backup_status(),
        "esp_compute_nodes": _safe_node_status(),
        "compute_node_credentials_exposed": False,
        "compute_node_network_addresses_exposed": False,
    }


@router.get("/doctor")
def doctor():
    report = system_report()
    report["independence"] = independence_status()
    report["live_renderers"] = renderer_runtime_status()
    return report


@router.get("/renderers")
def live_renderers():
    """Member-safe renderer readiness; internal renderer URLs and credentials are never returned."""
    return renderer_runtime_status()


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


# The production app mounts this system router directly. Composing the member-safe Aura Sec
# router here therefore keeps one application, one member identity boundary and one middleware
# stack. APIRouter preserves the child's public `/aura-sec` prefix; native trust channels remain
# outside the browser surface.
router.include_router(aura_sec_portal_router)
