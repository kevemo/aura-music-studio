from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from .accounts import AccountStore
from .esp_command_center import EspStore, esp
from .esp_niche import EspNicheStore
from .esp_progress import EspProgressStore
from .owner_identity import owner_actor
from .plans import get_plan


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OwnerUserControl:
    """Owner-side account, subscription, ESP and creator oversight.

    Subscription and ESP access are intentionally independent dimensions. Free/Base/Pro
    controls the normal Pulsar-Frequency House creative entitlement. ESP Creator/Agent/Both
    is a separate owner-approved role and never self-activates from a customer subscription.
    """

    def __init__(self, account_store: AccountStore | None = None, esp_store: EspStore | None = None):
        self.accounts = account_store or AccountStore()
        self.esp = esp_store or esp
        self.db_path = self.accounts.db_path
        self.niches = EspNicheStore(self.esp)
        self.progress = EspProgressStore(self.esp)
        self._init_owner_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_owner_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS owner_user_profiles (
                    user_id TEXT PRIMARY KEY,
                    sub_level TEXT NOT NULL DEFAULT '',
                    mentor TEXT NOT NULL DEFAULT '',
                    categories_json TEXT NOT NULL DEFAULT '[]',
                    owner_notes TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS owner_audit_log (
                    id TEXT PRIMARY KEY,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target_user_id TEXT,
                    before_json TEXT NOT NULL DEFAULT '{}',
                    after_json TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(target_user_id) REFERENCES users(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_owner_audit_created
                    ON owner_audit_log(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_owner_audit_target
                    ON owner_audit_log(target_user_id, created_at DESC);
                """
            )

    @staticmethod
    def _json(value) -> str:
        return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)

    def _audit(
        self,
        con: sqlite3.Connection,
        *,
        action: str,
        target_user_id: str | None,
        before: dict | None = None,
        after: dict | None = None,
        metadata: dict | None = None,
        actor: str = "ESP Owner",
    ) -> None:
        resolved_actor = owner_actor(actor)[:120]
        con.execute(
            """INSERT INTO owner_audit_log
               (id,actor,action,target_user_id,before_json,after_json,metadata_json,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                uuid4().hex,
                resolved_actor,
                action[:120],
                target_user_id,
                self._json(before),
                self._json(after),
                self._json(metadata),
                _now(),
            ),
        )

    def audit_log(self, user_id: str | None = None, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as con:
            if user_id:
                rows = con.execute(
                    """SELECT * FROM owner_audit_log WHERE target_user_id=?
                       ORDER BY created_at DESC LIMIT ?""",
                    (user_id, limit),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM owner_audit_log ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        out: list[dict] = []
        for row in rows:
            item = dict(row)
            for source, target in (
                ("before_json", "before"),
                ("after_json", "after"),
                ("metadata_json", "metadata"),
            ):
                try:
                    item[target] = json.loads(item.pop(source) or "{}")
                except Exception:
                    item[target] = {}
            out.append(item)
        return out

    def list_users(self) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT u.id,u.email,u.display_name,u.status,u.plan_id,u.requested_plan_id,u.billing_status,u.created_at,
                       e.status AS esp_status,e.roles AS esp_roles,e.tiktok_handle,e.region,
                       n.niche,n.sub_niche,n.network_status,
                       op.sub_level,op.mentor,op.categories_json,op.owner_notes,
                       COUNT(DISTINCT ue.id) AS usage_events,
                       COUNT(DISTINCT ue.project_id) AS project_count,
                       COUNT(DISTINCT ps.id) AS progress_submissions,
                       MAX(ps.created_at) AS last_progress_at,
                       COALESCE(AVG(tp.percent),0) AS avg_training_percent
                FROM users u
                LEFT JOIN esp_memberships e ON e.user_id=u.id
                LEFT JOIN esp_niche_profiles n ON n.user_id=u.id
                LEFT JOIN owner_user_profiles op ON op.user_id=u.id
                LEFT JOIN usage_events ue ON ue.user_id=u.id
                LEFT JOIN esp_performance_submissions ps ON ps.user_id=u.id
                LEFT JOIN esp_training_progress tp ON tp.user_id=u.id
                GROUP BY u.id
                ORDER BY u.created_at DESC
                """
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["categories"] = json.loads(item.pop("categories_json") or "[]")
            except Exception:
                item["categories"] = []
            result.append(item)
        return result

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
                """SELECT id,requested_role,tiktok_handle,region,note,status,created_at,decided_at,decided_by,assigned_role
                   FROM esp_access_requests WHERE user_id=? ORDER BY created_at DESC LIMIT 20""",
                (user_id,),
            ).fetchall()
            owner_profile_row = con.execute(
                "SELECT * FROM owner_user_profiles WHERE user_id=?",
                (user_id,),
            ).fetchone()
        owner_profile = dict(owner_profile_row) if owner_profile_row else {
            "user_id": user_id,
            "sub_level": "",
            "mentor": "",
            "categories_json": "[]",
            "owner_notes": "",
        }
        try:
            owner_profile["categories"] = json.loads(owner_profile.pop("categories_json") or "[]")
        except Exception:
            owner_profile["categories"] = []
        return {
            "user": user,
            "esp": membership,
            "niche": profile,
            "performance": performance,
            "training": training,
            "usage": [dict(row) for row in usage_rows],
            "projects": int(projects or 0),
            "esp_requests": [dict(row) for row in pending],
            "owner_profile": owner_profile,
            "owner_audit": self.audit_log(user_id, 50),
        }

    def dashboard_summary(self) -> dict:
        rows = self.list_users()
        return {
            "users": len(rows),
            "regular": sum(1 for row in rows if (row.get("esp_status") or "") not in {"active", "owner"}),
            "esp_pending": sum(1 for row in rows if row.get("esp_status") == "pending"),
            "esp_creators": sum(
                1 for row in rows
                if row.get("esp_status") in {"active", "owner"} and row.get("esp_roles") in {"creator", "both"}
            ),
            "esp_agents": sum(
                1 for row in rows
                if row.get("esp_status") in {"active", "owner"} and row.get("esp_roles") in {"agent", "both"}
            ),
            "progress_submissions": sum(int(row.get("progress_submissions") or 0) for row in rows),
            "usage_events": sum(int(row.get("usage_events") or 0) for row in rows),
            "plans": {
                plan: sum(1 for row in rows if row.get("plan_id") == plan)
                for plan in ("free", "base", "pro")
            },
        }

    def update_owner_profile(
        self,
        user_id: str,
        *,
        sub_level: str = "",
        mentor: str = "",
        categories: list[str] | None = None,
        owner_notes: str = "",
        actor: str = "ESP Owner",
    ) -> dict:
        user = self.accounts.get_user(user_id)
        if not user:
            raise ValueError("User not found")
        resolved_actor = owner_actor(actor)[:120]
        cleaned_categories = [str(value).strip()[:80] for value in (categories or []) if str(value).strip()][:30]
        cleaned = {
            "sub_level": (sub_level or "").strip()[:120],
            "mentor": (mentor or "").strip()[:120],
            "categories": cleaned_categories,
            "owner_notes": (owner_notes or "").strip()[:5000],
        }
        with self._connect() as con:
            before_row = con.execute("SELECT * FROM owner_user_profiles WHERE user_id=?", (user_id,)).fetchone()
            before = dict(before_row) if before_row else {}
            con.execute(
                """INSERT INTO owner_user_profiles
                   (user_id,sub_level,mentor,categories_json,owner_notes,updated_at,updated_by)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     sub_level=excluded.sub_level,mentor=excluded.mentor,categories_json=excluded.categories_json,
                     owner_notes=excluded.owner_notes,updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
                (
                    user_id,
                    cleaned["sub_level"],
                    cleaned["mentor"],
                    json.dumps(cleaned_categories, ensure_ascii=False),
                    cleaned["owner_notes"],
                    _now(),
                    resolved_actor,
                ),
            )
            self._audit(
                con,
                action="owner_user_profile_updated",
                target_user_id=user_id,
                before=before,
                after=cleaned,
                actor=resolved_actor,
            )
        return self.detail(user_id) or {}

    def set_plan(self, user_id: str, plan_id: str, actor: str = "ESP Owner") -> dict:
        plan = get_plan(plan_id)
        user = self.accounts.get_user(user_id)
        if not user:
            raise ValueError("User not found")
        resolved_actor = owner_actor(actor)
        before = {
            "status": user.get("status"),
            "plan_id": user.get("plan_id"),
            "requested_plan_id": user.get("requested_plan_id"),
            "billing_status": user.get("billing_status"),
        }
        with self._connect() as con:
            con.execute(
                """UPDATE users SET status='active',plan_id=?,requested_plan_id=?,billing_status='owner_override'
                   WHERE id=?""",
                (plan.id, plan.id, user_id),
            )
            con.execute(
                "INSERT INTO usage_events(id,user_id,event_type,project_id,occurred_at,metadata_json) VALUES (lower(hex(randomblob(16))),?,'owner_plan_override',NULL,?,?)",
                (user_id, _now(), json.dumps({"plan": plan.id, "actor": resolved_actor[:120]})),
            )
            self._audit(
                con,
                action="subscription_plan_changed",
                target_user_id=user_id,
                before=before,
                after={"status": "active", "plan_id": plan.id, "requested_plan_id": plan.id, "billing_status": "owner_override"},
                actor=resolved_actor,
            )
        return self.accounts.get_user(user_id) or {}

    def set_esp_role(self, user_id: str, role: str, actor: str = "ESP Owner") -> None:
        role = (role or "").strip().lower()
        if role not in {"regular", "creator", "agent", "both"}:
            raise ValueError("Role must be regular, creator, agent, or both")
        user = self.accounts.get_user(user_id)
        if not user:
            raise ValueError("User not found")
        resolved_actor = owner_actor(actor)
        now = _now()
        with self._connect() as con:
            current = con.execute(
                "SELECT status,roles,tiktok_handle,region FROM esp_memberships WHERE user_id=?",
                (user_id,),
            ).fetchone()
            before = dict(current) if current else {"status": "none", "roles": ""}

            if role == "regular":
                if current:
                    con.execute(
                        """UPDATE esp_memberships SET status='revoked',roles='',revoked_at=?,revoked_by=?,updated_at=?
                           WHERE user_id=?""",
                        (now, resolved_actor[:120], now, user_id),
                    )
                if user.get("billing_status") == "esp_comped":
                    con.execute(
                        "UPDATE users SET plan_id='free',requested_plan_id='free',billing_status='not_required' WHERE id=?",
                        (user_id,),
                    )
                self._audit(
                    con,
                    action="esp_role_changed",
                    target_user_id=user_id,
                    before=before,
                    after={"status": "revoked", "roles": "regular"},
                    metadata={"comped_base_removed": user.get("billing_status") == "esp_comped"},
                    actor=resolved_actor,
                )
                return

            # Preserve verified TikTok/region metadata when changing a role manually.
            tiktok_handle = current["tiktok_handle"] if current and current["tiktok_handle"] else ""
            region = current["region"] if current and current["region"] else ""
            con.execute(
                """INSERT INTO esp_memberships
                   (user_id,status,roles,tiktok_handle,region,approved_at,approved_by,updated_at)
                   VALUES (?,'active',?,?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET status='active',roles=excluded.roles,
                     tiktok_handle=CASE WHEN esp_memberships.tiktok_handle<>'' THEN esp_memberships.tiktok_handle ELSE excluded.tiktok_handle END,
                     region=CASE WHEN esp_memberships.region<>'' THEN esp_memberships.region ELSE excluded.region END,
                     approved_at=COALESCE(esp_memberships.approved_at,excluded.approved_at),approved_by=excluded.approved_by,
                     revoked_at=NULL,revoked_by=NULL,updated_at=excluded.updated_at""",
                (user_id, role, tiktok_handle, region, now, resolved_actor[:120], now),
            )
            # ESP access carries Base at no charge unless the account already has Pro or
            # another owner/payment state should remain authoritative.
            comped = False
            if user.get("plan_id") != "pro" and user.get("billing_status") not in {"active", "owner_override"}:
                con.execute(
                    "UPDATE users SET status='active',plan_id='base',requested_plan_id='base',billing_status='esp_comped' WHERE id=?",
                    (user_id,),
                )
                comped = True

            con.execute(
                """UPDATE esp_access_requests SET status='approved',decided_at=?,decided_by=?,assigned_role=?
                   WHERE user_id=? AND status='pending'""",
                (now, resolved_actor[:120], role, user_id),
            )
            self._audit(
                con,
                action="esp_role_changed",
                target_user_id=user_id,
                before=before,
                after={"status": "active", "roles": role},
                metadata={"esp_base_comped": comped},
                actor=resolved_actor,
            )

    def decline_esp_requests(self, user_id: str, actor: str = "ESP Owner") -> None:
        user = self.accounts.get_user(user_id)
        if not user:
            raise ValueError("User not found")
        resolved_actor = owner_actor(actor)
        now = _now()
        with self._connect() as con:
            pending = [dict(row) for row in con.execute(
                "SELECT id,requested_role,tiktok_handle,region,status FROM esp_access_requests WHERE user_id=? AND status='pending'",
                (user_id,),
            ).fetchall()]
            con.execute(
                """UPDATE esp_access_requests SET status='rejected',decided_at=?,decided_by=?
                   WHERE user_id=? AND status='pending'""",
                (now, resolved_actor[:120], user_id),
            )
            con.execute(
                """INSERT INTO esp_memberships(user_id,status,roles,updated_at)
                   VALUES (?,'rejected','',?)
                   ON CONFLICT(user_id) DO UPDATE SET status='rejected',roles='',updated_at=excluded.updated_at""",
                (user_id, now),
            )
            self._audit(
                con,
                action="esp_access_request_declined",
                target_user_id=user_id,
                before={"pending_requests": pending},
                after={"status": "rejected"},
                actor=resolved_actor,
            )
