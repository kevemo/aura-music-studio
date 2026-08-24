from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .accounts import AccountStore
from .esp_command_center import EspStore, esp
from .esp_niche import EspNicheStore
from .esp_progress import EspProgressStore
from .plans import get_plan


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OwnerUserControl:
    """Owner-side account/ESP management.

    Subscription plan and ESP role are intentionally independent dimensions:
    a Free/Base/Pro user can be Regular, while an approved ESP member additionally has
    Creator, Agent, Both or Owner access.
    """

    def __init__(self, account_store: AccountStore | None = None, esp_store: EspStore | None = None):
        self.accounts = account_store or AccountStore()
        self.esp = esp_store or esp
        self.db_path = self.accounts.db_path
        self.niches = EspNicheStore(self.esp)
        self.progress = EspProgressStore(self.esp)

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def list_users(self) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT u.id,u.email,u.display_name,u.status,u.plan_id,u.requested_plan_id,u.billing_status,u.created_at,
                       e.status AS esp_status,e.roles AS esp_roles,e.tiktok_handle,e.region,
                       n.niche,n.sub_niche,n.network_status,
                       COUNT(DISTINCT ue.id) AS usage_events,
                       COUNT(DISTINCT ue.project_id) AS project_count,
                       COUNT(DISTINCT ps.id) AS progress_submissions,
                       MAX(ps.created_at) AS last_progress_at,
                       COALESCE(AVG(tp.percent),0) AS avg_training_percent
                FROM users u
                LEFT JOIN esp_memberships e ON e.user_id=u.id
                LEFT JOIN esp_niche_profiles n ON n.user_id=u.id
                LEFT JOIN usage_events ue ON ue.user_id=u.id
                LEFT JOIN esp_performance_submissions ps ON ps.user_id=u.id
                LEFT JOIN esp_training_progress tp ON tp.user_id=u.id
                GROUP BY u.id
                ORDER BY u.created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def detail(self, user_id: str) -> dict | None:
        user = self.accounts.get_user(user_id)
        if not user:
            return None
        membership = self.esp.membership(user_id)
        profile = self.niches.get(user_id)
        performance = self.progress.summary(user_id)
        training = self.esp.progress(user_id)
        with self._connect() as con:
            usage_rows = con.execute(
                """SELECT event_type,COUNT(*) AS n,MAX(occurred_at) AS last_at
                   FROM usage_events WHERE user_id=? GROUP BY event_type ORDER BY n DESC""",
                (user_id,),
            ).fetchall()
            projects = con.execute(
                "SELECT COUNT(DISTINCT project_id) AS n FROM usage_events WHERE user_id=? AND project_id IS NOT NULL",
                (user_id,),
            ).fetchone()["n"]
            pending = con.execute(
                """SELECT id,requested_role,tiktok_handle,region,note,status,created_at
                   FROM esp_access_requests WHERE user_id=? ORDER BY created_at DESC LIMIT 10""",
                (user_id,),
            ).fetchall()
        return {
            "user": user,
            "esp": membership,
            "niche": profile,
            "performance": performance,
            "training": training,
            "usage": [dict(row) for row in usage_rows],
            "projects": int(projects or 0),
            "esp_requests": [dict(row) for row in pending],
        }

    def set_plan(self, user_id: str, plan_id: str, actor: str = "ESP Owner") -> dict:
        plan = get_plan(plan_id)
        user = self.accounts.get_user(user_id)
        if not user:
            raise ValueError("User not found")
        with self._connect() as con:
            con.execute(
                """UPDATE users SET status='active',plan_id=?,requested_plan_id=?,billing_status='owner_override'
                   WHERE id=?""",
                (plan.id, plan.id, user_id),
            )
            con.execute(
                "INSERT INTO usage_events(id,user_id,event_type,project_id,occurred_at,metadata_json) VALUES (lower(hex(randomblob(16))),?,'owner_plan_override',NULL,?,?)",
                (user_id, _now(), json.dumps({"plan": plan.id, "actor": actor[:120]})),
            )
        return self.accounts.get_user(user_id) or {}

    def set_esp_role(self, user_id: str, role: str, actor: str = "ESP Owner") -> None:
        role = (role or "").strip().lower()
        if role not in {"regular", "creator", "agent", "both"}:
            raise ValueError("Role must be regular, creator, agent, or both")
        user = self.accounts.get_user(user_id)
        if not user:
            raise ValueError("User not found")
        now = _now()
        with self._connect() as con:
            if role == "regular":
                current = con.execute("SELECT status FROM esp_memberships WHERE user_id=?", (user_id,)).fetchone()
                if current:
                    con.execute(
                        """UPDATE esp_memberships SET status='revoked',roles='',revoked_at=?,revoked_by=?,updated_at=?
                           WHERE user_id=?""",
                        (now, actor[:120], now, user_id),
                    )
                if user.get("billing_status") == "esp_comped":
                    con.execute(
                        "UPDATE users SET plan_id='free',requested_plan_id='free',billing_status='not_required' WHERE id=?",
                        (user_id,),
                    )
                return

            con.execute(
                """INSERT INTO esp_memberships
                   (user_id,status,roles,tiktok_handle,region,approved_at,approved_by,updated_at)
                   VALUES (?,'active',?,'','',?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET status='active',roles=excluded.roles,
                     approved_at=COALESCE(esp_memberships.approved_at,excluded.approved_at),approved_by=excluded.approved_by,
                     revoked_at=NULL,revoked_by=NULL,updated_at=excluded.updated_at""",
                (user_id, role, now, actor[:120], now),
            )
            # ESP access carries Base at no charge unless the account already has Pro.
            if user.get("plan_id") != "pro" and user.get("billing_status") not in {"active", "owner_override"}:
                con.execute(
                    "UPDATE users SET status='active',plan_id='base',requested_plan_id='base',billing_status='esp_comped' WHERE id=?",
                    (user_id,),
                )

            # If a pending self-request exists, close it as owner-approved without needing an email token.
            con.execute(
                """UPDATE esp_access_requests SET status='approved',decided_at=?,decided_by=?,assigned_role=?
                   WHERE user_id=? AND status='pending'""",
                (now, actor[:120], role, user_id),
            )

    def decline_esp_requests(self, user_id: str, actor: str = "ESP Owner") -> None:
        now = _now()
        with self._connect() as con:
            con.execute(
                """UPDATE esp_access_requests SET status='rejected',decided_at=?,decided_by=?
                   WHERE user_id=? AND status='pending'""",
                (now, actor[:120], user_id),
            )
            con.execute(
                """INSERT INTO esp_memberships(user_id,status,roles,updated_at)
                   VALUES (?,'rejected','',?)
                   ON CONFLICT(user_id) DO UPDATE SET status='rejected',roles='',updated_at=excluded.updated_at""",
                (user_id, now),
            )
