from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .backup import StudioBackupManager


def _truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except Exception:
        return default


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BackupScheduler:
    """Owner-configured local backup rotation with no cloud dependency."""

    def __init__(self):
        self.manager = StudioBackupManager()
        self.status_path = Path(os.getenv("LSS_BACKUP_STATUS", "data/backup_scheduler_status.json"))
        self.status_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return _truthy("LSS_AUTO_BACKUP_ENABLED", False)

    @property
    def interval_seconds(self) -> int:
        return _int("LSS_AUTO_BACKUP_INTERVAL_HOURS", 24) * 3600

    @property
    def keep(self) -> int:
        return _int("LSS_AUTO_BACKUP_KEEP", 7)

    def _status(self, **values) -> None:
        payload = {
            "updated_at": _now(),
            "enabled": self.enabled,
            "interval_hours": self.interval_seconds // 3600,
            "retention_count": self.keep,
            **values,
        }
        self.status_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def prune(self) -> list[str]:
        root = self.manager.backup_dir
        candidates = sorted(
            [
                p for p in root.iterdir()
                if p.is_file() and (
                    p.name.startswith("ESP_Auto_") and (p.suffix.lower() == ".zip" or p.name.lower().endswith(".zip.age"))
                )
            ],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        removed: list[str] = []
        for path in candidates[self.keep:]:
            path.unlink(missing_ok=True)
            removed.append(str(path))
        return removed

    def run_once(self) -> dict:
        if not self.enabled:
            result = {"ran": False, "reason": "automatic backups disabled"}
            self._status(last_result=result)
            return result

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = self.manager.backup_dir / f"ESP_Auto_{stamp}.zip"
        try:
            result = self.manager.create(
                output=output,
                include_outputs=_truthy("LSS_AUTO_BACKUP_INCLUDE_OUTPUTS", False),
                include_work=_truthy("LSS_AUTO_BACKUP_INCLUDE_WORK", True),
                age_recipient=(os.getenv("LSS_BACKUP_AGE_RECIPIENT") or "").strip() or None,
                keep_plain_when_encrypted=False,
            )
            removed = self.prune()
            summary = {
                "ran": True,
                "ok": True,
                "backup": result.get("encrypted_backup") or result.get("backup"),
                "bytes": result.get("bytes"),
                "removed_by_retention": removed,
                "project_file_count": result.get("manifest", {}).get("project_file_count"),
            }
            self._status(last_success_at=_now(), last_result=summary)
            return summary
        except Exception as exc:
            summary = {"ran": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            self._status(last_failure_at=_now(), last_result=summary)
            return summary

    def serve_forever(self) -> None:
        # The service remains lightweight while disabled so an owner can enable it by editing .env
        # and restarting the container without changing the Compose topology.
        while True:
            if self.enabled:
                self.run_once()
                time.sleep(self.interval_seconds)
            else:
                self._status(last_result={"ran": False, "reason": "automatic backups disabled"})
                time.sleep(300)


def main() -> None:
    BackupScheduler().serve_forever()


if __name__ == "__main__":
    main()
