from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import esp_niche as esp_niche_module
from .esp_command_center import EspStore, esp
from .owner_user_control import OwnerUserControl


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EspSocialAccessControlStore:
    """Owner-managed exception layer for the private ESP Social Media Centre.

    Normal eligibility still requires active ESP membership, a niche profile and the
    ESP-only/no-other-network declaration. Ownership can additionally suspend the Social
    Media Centre without revoking the member's Creator/Agent training access or changing
    their Free/Basic/Pro creative subscription.
    """

    def __init__(self, esp_store: EspStore | None = None):
        self.esp = esp_store or esp
        self.db_path = self.esp.db_path
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS esp_social_access_controls (
                    user_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL DEFAULT 'default',
                    reason TEXT NOT NULL DEFAULT '',
                    updated_by TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_esp_social_access_state
                    ON esp_social_access_controls(state);
                """
            )

    def get(self, user_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM esp_social_access_controls WHERE user_id=?", (user_id,)
            ).fetchone()
        return dict(row) if row else {
            "user_id": user_id,
            "state": "default",
            "reason": "",
            "updated_by": "",
            "updated_at": "",
        }

    @staticmethod
    def _audit_snapshot(item: dict) -> dict:
        return {
            "user_id": item.get("user_id"),
            "state": item.get("state") or "default",
            "updated_by": item.get("updated_by") or "",
            "updated_at": item.get("updated_at") or "",
        }

    def _owner_control(self) -> OwnerUserControl:
        control = OwnerUserControl(self.esp.accounts, self.esp)
        if control.db_path != self.db_path:
            raise RuntimeError("ESP Social access and Owner audit must share the authoritative database")
        return control

    def suspend(self, user_id: str, *, actor: str, reason: str = "") -> dict:
        membership = self.esp.membership(user_id)
        if not membership:
            raise ValueError("ESP membership not found")
        before = self.get(user_id)
        resolved_reason = (reason or "Owner suspended Social Media Centre access")[:1000]
        now = _now()
        owner_control = self._owner_control()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                """INSERT INTO esp_social_access_controls(user_id,state,reason,updated_by,updated_at)
                   VALUES (?,'suspended',?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     state='suspended',reason=excluded.reason,updated_by=excluded.updated_by,
                     updated_at=excluded.updated_at""",
                (user_id, resolved_reason, actor[:120], now),
            )
            row = con.execute(
                "SELECT * FROM esp_social_access_controls WHERE user_id=?", (user_id,)
            ).fetchone()
            after = dict(row)
            owner_control._audit(
                con,
                action="esp_social_access_suspended",
                target_user_id=user_id,
                before=self._audit_snapshot(before),
                after=self._audit_snapshot(after),
                metadata={
                    "access_surface": "esp_social_media_centre",
                    "reason_present": bool(resolved_reason.strip()),
                    "esp_membership_changed": False,
                    "subscription_changed": False,
                },
                actor=actor,
            )
        return after

    def restore(self, user_id: str, *, actor: str) -> dict:
        membership = self.esp.membership(user_id)
        if not membership:
            raise ValueError("ESP membership not found")
        before = self.get(user_id)
        now = _now()
        owner_control = self._owner_control()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                """INSERT INTO esp_social_access_controls(user_id,state,reason,updated_by,updated_at)
                   VALUES (?,'default','',?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     state='default',reason='',updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                (user_id, actor[:120], now),
            )
            row = con.execute(
                "SELECT * FROM esp_social_access_controls WHERE user_id=?", (user_id,)
            ).fetchone()
            after = dict(row)
            owner_control._audit(
                con,
                action="esp_social_access_restored",
                target_user_id=user_id,
                before=self._audit_snapshot(before),
                after=self._audit_snapshot(after),
                metadata={
                    "access_surface": "esp_social_media_centre",
                    "reason_present": bool(str(before.get("reason") or "").strip()),
                    "esp_membership_changed": False,
                    "subscription_changed": False,
                },
                actor=actor,
            )
        return after

    def suspended(self, user_id: str) -> tuple[bool, str]:
        item = self.get(user_id)
        if item.get("state") == "suspended":
            reason = item.get("reason") or "Ownership has suspended ESP Social Media Centre access."
            return True, str(reason)
        return False, ""


social_access_controls = EspSocialAccessControlStore()
_ORIGINAL_SOCIAL_ACCESS_REASON = esp_niche_module.social_access_reason
_INSTALLED = False


def effective_social_access_reason(membership: dict | None, profile: dict | None) -> tuple[bool, str]:
    allowed, reason = _ORIGINAL_SOCIAL_ACCESS_REASON(membership, profile)
    if not allowed:
        return allowed, reason
    user_id = str((membership or {}).get("user_id") or "")
    if user_id:
        suspended, suspension_reason = social_access_controls.suspended(user_id)
        if suspended:
            return False, f"ESP Social Media Centre access is suspended by ownership. {suspension_reason}"
    return True, reason


def install_social_access_control() -> None:
    """Apply owner suspension to every existing require_esp_social_member call.

    Existing social routes resolve `social_access_reason` through the esp_niche module at
    request time, so installing this policy updates the common API boundary without
    duplicating or weakening any of the existing niche/no-poaching checks.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    esp_niche_module.social_access_reason = effective_social_access_reason
    _INSTALLED = True
