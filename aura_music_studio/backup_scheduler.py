from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .backup import StudioBackupManager
from .studio_settings import StudioSettings


def _truthy_value(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_value(value: str | None, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(value if value is not None else default))
    except Exception:
        return default


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


class BackupScheduler:
    """Owner-controlled encrypted Recovery Vault backup rotation.

    Non-secret scheduling options can be changed live in the owner dashboard. Sensitive
    scheduled backups fail closed unless a public ``age`` recipient is configured in the
    deployment secret boundary; the private decryption identity is never stored here.
    """

    def __init__(self):
        self.manager = StudioBackupManager()
        self.settings = StudioSettings()
        self.status_path = Path(os.getenv("LSS_BACKUP_STATUS", "data/backup_scheduler_status.json"))
        self.status_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get_or(
            "auto_backup_enabled",
            _truthy_value(os.getenv("LSS_AUTO_BACKUP_ENABLED"), False),
        ))

    @property
    def encryption_recipient(self) -> str | None:
        value = (os.getenv("LSS_BACKUP_AGE_RECIPIENT") or "").strip()
        return value or None

    @property
    def encryption_ready(self) -> bool:
        return self.encryption_recipient is not None

    @property
    def operational(self) -> bool:
        return self.enabled and self.encryption_ready

    @property
    def interval_hours(self) -> int:
        return int(self.settings.get_or(
            "auto_backup_interval_hours",
            _int_value(os.getenv("LSS_AUTO_BACKUP_INTERVAL_HOURS"), 24),
        ))

    @property
    def interval_seconds(self) -> int:
        return self.interval_hours * 3600

    @property
    def keep(self) -> int:
        return int(self.settings.get_or(
            "auto_backup_keep",
            _int_value(os.getenv("LSS_AUTO_BACKUP_KEEP"), 7),
        ))

    @property
    def include_outputs(self) -> bool:
        return bool(self.settings.get_or(
            "auto_backup_include_outputs",
            _truthy_value(os.getenv("LSS_AUTO_BACKUP_INCLUDE_OUTPUTS"), False),
        ))

    @property
    def include_work(self) -> bool:
        return bool(self.settings.get_or(
            "auto_backup_include_work",
            _truthy_value(os.getenv("LSS_AUTO_BACKUP_INCLUDE_WORK"), True),
        ))

    def configuration(self) -> dict:
        return {
            "enabled": self.enabled,
            "operational": self.operational,
            "interval_hours": self.interval_hours,
            "retention_count": self.keep,
            "include_outputs": self.include_outputs,
            "include_work": self.include_work,
            "encryption_required": True,
            "encryption_ready": self.encryption_ready,
            "encrypted_when_recipient_configured": self.encryption_ready,
        }

    def _read_status(self) -> dict:
        if not self.status_path.is_file():
            return {}
        try:
            value = json.loads(self.status_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _status(self, **values) -> None:
        previous = self._read_status()
        payload = {
            **previous,
            "updated_at": _now(),
            **self.configuration(),
            **values,
        }
        self.status_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def prune(self) -> list[str]:
        root = self.manager.backup_dir
        candidates = sorted(
            [
                p for p in root.iterdir()
                if p.is_file() and p.name.startswith("ESP_Auto_")
                and (p.suffix.lower() == ".zip" or p.name.lower().endswith(".zip.age"))
            ],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        removed: list[str] = []
        for path in candidates[self.keep:]:
            path.unlink(missing_ok=True)
            removed.append(path.name)
        return removed

    def due(self, now: datetime | None = None) -> bool:
        if not self.operational:
            return False
        status = self._read_status()
        marker = status.get("last_attempt_at") or status.get("last_success_at")
        if not marker:
            return True
        try:
            previous = datetime.fromisoformat(str(marker))
            if previous.tzinfo is None:
                previous = previous.replace(tzinfo=timezone.utc)
        except Exception:
            return True
        return (now or _now_dt()) >= previous + timedelta(seconds=self.interval_seconds)

    def run_once(self, *, force: bool = False) -> dict:
        recipient = self.encryption_recipient
        if not recipient:
            summary = {
                "ran": False,
                "ok": False,
                "reason": "automatic backup encryption recipient is not configured",
            }
            self._status(last_failure_at=_now(), last_result=summary)
            return summary
        if not self.enabled and not force:
            result = {"ran": False, "reason": "automatic backups disabled"}
            self._status(last_result=result)
            return result
        if not force and not self.due():
            result = {"ran": False, "reason": "next automatic backup is not due yet"}
            self._status(last_result=result)
            return result

        stamp = _now_dt().strftime("%Y%m%dT%H%M%SZ")
        output = self.manager.backup_dir / f"ESP_Auto_{stamp}.zip"
        attempt_at = _now()
        self._status(last_attempt_at=attempt_at, last_result={"ran": True, "state": "running"})
        try:
            result = self.manager.create(
                output=output,
                include_outputs=self.include_outputs,
                include_work=self.include_work,
                age_recipient=recipient,
                keep_plain_when_encrypted=False,
            )
            if not result.get("encrypted_backup") or result.get("backup"):
                raise RuntimeError("Recovery Vault backup did not finish in encrypted-only state")
            removed = self.prune()
            summary = {
                "ran": True,
                "ok": True,
                "backup": Path(str(result["encrypted_backup"])).name,
                "encrypted": True,
                "bytes": result.get("bytes"),
                "removed_by_retention": removed,
                "project_file_count": result.get("manifest", {}).get("project_file_count"),
            }
            self._status(last_success_at=_now(), last_attempt_at=attempt_at, last_result=summary)
            return summary
        except Exception as exc:
            output.unlink(missing_ok=True)
            output.with_suffix(output.suffix + ".age").unlink(missing_ok=True)
            summary = {"ran": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            self._status(last_failure_at=_now(), last_attempt_at=attempt_at, last_result=summary)
            return summary

    def serve_forever(self) -> None:
        # Polling a small local settings table once per minute keeps owner changes live without restarting Docker.
        while True:
            try:
                if self.operational and self.due():
                    self.run_once()
                elif self.enabled and not self.encryption_ready:
                    self._status(
                        last_result={
                            "ran": False,
                            "ok": False,
                            "reason": "automatic backup encryption recipient is not configured",
                        }
                    )
                else:
                    self._status(last_result=self._read_status().get("last_result", {"ran": False, "reason": "waiting"}))
            except Exception as exc:
                self._status(last_failure_at=_now(), last_result={"ran": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            time.sleep(60)


def main() -> None:
    BackupScheduler().serve_forever()


if __name__ == "__main__":
    main()
