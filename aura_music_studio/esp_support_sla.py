from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .esp_command_center import EspStore, esp
from .esp_niche import require_esp_hub_member
from .esp_support_center import CreateCaseRequest, SupportCaseStore, support

router = APIRouter(tags=["ESP Support SLA Operations"])

Priority = Literal["P0", "P1", "P2", "P3"]
WaitingOn = Literal["none", "creator", "platform_partner", "esp_specialist", "owner_review"]

SERVICE_CATEGORIES = {
    "account_access": "Account / Access",
    "compliance_violation": "Compliance / Violation",
    "live_technical": "LIVE Technical",
    "traffic_distribution": "Traffic / Distribution",
    "collaboration_event": "Collaboration / Event",
    "shop_commerce": "Shop / Commerce",
    "brand_commercial": "Brand / Commercial",
    "mentor_communication": "Mentor / Communication",
    "safety_harassment_ip": "Safety / Harassment / IP",
    "creator_care": "Creator Care",
    "general_programme": "General Programme",
}

PRIORITY_POLICY = {
    "P0": {"label": "Immediate safety/security or major ESP service outage", "first_human_response_target_hours": 0, "target_clock": "staffed_coverage"},
    "P1": {"label": "Creator materially blocked or serious account/technical issue", "first_human_response_target_hours": 4, "target_clock": "staffed_hours"},
    "P2": {"label": "Important issue; creator can continue operating", "first_human_response_target_hours": 24, "target_clock": "business_day_policy"},
    "P3": {"label": "Routine question or request", "first_human_response_target_hours": 48, "target_clock": "business_day_policy"},
}

_LEGACY_CATEGORY = {
    "account_access": "other",
    "compliance_violation": "violation",
    "live_technical": "technical",
    "traffic_distribution": "traffic_health",
    "collaboration_event": "other",
    "shop_commerce": "commerce",
    "brand_commercial": "commerce",
    "mentor_communication": "other",
    "safety_harassment_ip": "harassment",
    "creator_care": "other",
    "general_programme": "other",
}

_LEGACY_SEVERITY = {"P0": "urgent", "P1": "high", "P2": "normal", "P3": "low"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_owner(membership: dict) -> bool:
    return membership.get("status") == "owner" or str(membership.get("roles") or "").lower() == "owner"


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class ServiceCaseCreate(BaseModel):
    category: str = Field(min_length=2, max_length=80)
    subcategory: str = Field(default="", max_length=120)
    priority: Priority = "P2"
    region: str = Field(default="", max_length=80)
    subject: str = Field(min_length=3, max_length=240)
    description: str = Field(min_length=3, max_length=6000)


class SupportTouchCreate(BaseModel):
    kind: str = Field(default="substantive_response", max_length=80)
    note: str = Field(min_length=2, max_length=4000)
    substantive_human_response: bool = True
    creator_visible: bool = True


class ServiceMetaUpdate(BaseModel):
    waiting_on: WaitingOn = "none"
    external_reference: str = Field(default="", max_length=500)
    escalation_level: str = Field(default="", max_length=120)
    closure_code: str = Field(default="", max_length=120)
    creator_confirmed: bool = False
    follow_up_at: str = Field(default="", max_length=80)


class EspSupportSlaStore:
    """Sidecar service-operations layer for the existing private support centre.

    The base support case remains the canonical private ticket/evidence record. This layer
    adds the benchmark P0-P3 taxonomy, response semantics and transparent service metrics.
    It deliberately does not convert external platform waiting time into an ESP promise.
    """

    def __init__(self, esp_store: EspStore | None = None, support_store: SupportCaseStore | None = None):
        self.esp = esp_store or esp
        self.support = support_store or support
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
                CREATE TABLE IF NOT EXISTS esp_support_service_meta (
                    case_id TEXT PRIMARY KEY,
                    canonical_category TEXT NOT NULL,
                    subcategory TEXT NOT NULL DEFAULT '',
                    priority TEXT NOT NULL,
                    region TEXT NOT NULL DEFAULT '',
                    acknowledged_at TEXT,
                    first_substantive_response_at TEXT,
                    waiting_on TEXT NOT NULL DEFAULT 'none',
                    external_reference TEXT NOT NULL DEFAULT '',
                    escalation_level TEXT NOT NULL DEFAULT '',
                    closure_code TEXT NOT NULL DEFAULT '',
                    creator_confirmed INTEGER NOT NULL DEFAULT 0,
                    follow_up_at TEXT NOT NULL DEFAULT '',
                    last_creator_update_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES esp_support_cases(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_support_service_queue
                    ON esp_support_service_meta(priority,waiting_on,updated_at DESC);
                CREATE TABLE IF NOT EXISTS esp_support_service_touches (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    note TEXT NOT NULL,
                    substantive_human_response INTEGER NOT NULL DEFAULT 0,
                    creator_visible INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES esp_support_cases(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_support_service_touch_case
                    ON esp_support_service_touches(case_id,created_at);
                """
            )

    def _ensure_case(self, case_id: str) -> dict:
        try:
            return self.support.get(case_id, owner=True)
        except KeyError as exc:
            raise KeyError("Support case not found") from exc

    def create(self, user_id: str, body: ServiceCaseCreate) -> dict:
        if body.category not in SERVICE_CATEGORIES:
            raise ValueError("Unsupported support service category")
        base = self.support.create_case(
            user_id,
            category=_LEGACY_CATEGORY[body.category],
            severity=_LEGACY_SEVERITY[body.priority],
            subject=body.subject,
            description=body.description,
        )
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_support_service_meta
                (case_id,canonical_category,subcategory,priority,region,waiting_on,updated_at)
                VALUES (?,?,?,?,?,'none',?)""",
                (base["id"], body.category, body.subcategory.strip()[:120], body.priority, body.region.strip()[:80], _now()),
            )
        return self.get(base["id"], user_id=user_id)

    def attach_existing(self, case_id: str, *, category: str, priority: str, region: str = "", subcategory: str = "") -> dict:
        self._ensure_case(case_id)
        if category not in SERVICE_CATEGORIES:
            raise ValueError("Unsupported support service category")
        if priority not in PRIORITY_POLICY:
            raise ValueError("Unsupported support priority")
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_support_service_meta
                (case_id,canonical_category,subcategory,priority,region,waiting_on,updated_at)
                VALUES (?,?,?,?,?,'none',?)
                ON CONFLICT(case_id) DO UPDATE SET canonical_category=excluded.canonical_category,
                subcategory=excluded.subcategory,priority=excluded.priority,region=excluded.region,updated_at=excluded.updated_at""",
                (case_id, category, subcategory.strip()[:120], priority, region.strip()[:80], _now()),
            )
        return self.get(case_id, owner=True)

    def add_touch(self, case_id: str, body: SupportTouchCreate, *, actor: str) -> dict:
        self._ensure_case(case_id)
        now = _now()
        with self._connect() as con:
            meta = con.execute("SELECT * FROM esp_support_service_meta WHERE case_id=?", (case_id,)).fetchone()
            if not meta:
                raise ValueError("Attach the support case to the SLA service model before recording service touches")
            touch_id = uuid4().hex
            con.execute(
                """INSERT INTO esp_support_service_touches
                (id,case_id,actor,kind,note,substantive_human_response,creator_visible,created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (
                    touch_id,
                    case_id,
                    actor[:160],
                    body.kind.strip()[:80],
                    body.note.strip()[:4000],
                    int(body.substantive_human_response),
                    int(body.creator_visible),
                    now,
                ),
            )
            acknowledged = meta["acknowledged_at"] or now
            first_response = meta["first_substantive_response_at"]
            if body.substantive_human_response and not first_response:
                first_response = now
            con.execute(
                """UPDATE esp_support_service_meta SET acknowledged_at=?,first_substantive_response_at=?,updated_at=? WHERE case_id=?""",
                (acknowledged, first_response, now, case_id),
            )
        return self.get(case_id, owner=True)

    def update_meta(self, case_id: str, body: ServiceMetaUpdate) -> dict:
        self._ensure_case(case_id)
        if body.waiting_on not in {"none", "creator", "platform_partner", "esp_specialist", "owner_review"}:
            raise ValueError("Unsupported waiting-on state")
        with self._connect() as con:
            meta = con.execute("SELECT case_id FROM esp_support_service_meta WHERE case_id=?", (case_id,)).fetchone()
            if not meta:
                raise ValueError("Support case is not attached to the SLA service model")
            con.execute(
                """UPDATE esp_support_service_meta SET waiting_on=?,external_reference=?,escalation_level=?,closure_code=?,creator_confirmed=?,follow_up_at=?,updated_at=? WHERE case_id=?""",
                (
                    body.waiting_on,
                    body.external_reference.strip()[:500],
                    body.escalation_level.strip()[:120],
                    body.closure_code.strip()[:120],
                    int(body.creator_confirmed),
                    body.follow_up_at.strip()[:80],
                    _now(),
                    case_id,
                ),
            )
        return self.get(case_id, owner=True)

    def _projection(self, base: dict, meta: dict | None, touches: list[dict], *, creator_view: bool) -> dict:
        if not meta:
            return {"case": base, "service": None, "touches": []}
        priority = meta["priority"]
        policy = PRIORITY_POLICY[priority]
        created = _parse(base.get("created_at"))
        first_response = _parse(meta.get("first_substantive_response_at"))
        elapsed_hours = None
        first_response_hours = None
        now = datetime.now(timezone.utc)
        if created:
            elapsed_hours = round((now - created).total_seconds() / 3600, 2)
            if first_response:
                first_response_hours = round((first_response - created).total_seconds() / 3600, 2)
        service = dict(meta)
        service["creator_confirmed"] = bool(service["creator_confirmed"])
        service["category_label"] = SERVICE_CATEGORIES.get(service["canonical_category"], service["canonical_category"])
        service["policy"] = policy
        service["raw_elapsed_hours"] = elapsed_hours
        service["first_substantive_response_raw_hours"] = first_response_hours
        service["sla_interpretation"] = (
            "Raw elapsed time is shown for transparency. Policy targets use staffed/business coverage; this system does not claim external platform response times."
        )
        visible_touches = [touch for touch in touches if not creator_view or bool(touch["creator_visible"])]
        for touch in visible_touches:
            touch["substantive_human_response"] = bool(touch["substantive_human_response"])
            touch["creator_visible"] = bool(touch["creator_visible"])
        return {"case": base, "service": service, "touches": visible_touches}

    def get(self, case_id: str, *, user_id: str | None = None, owner: bool = False) -> dict:
        base = self.support.get(case_id, user_id=user_id, owner=owner)
        with self._connect() as con:
            meta_row = con.execute("SELECT * FROM esp_support_service_meta WHERE case_id=?", (case_id,)).fetchone()
            touch_rows = con.execute(
                "SELECT * FROM esp_support_service_touches WHERE case_id=? ORDER BY created_at",
                (case_id,),
            ).fetchall()
        return self._projection(base, dict(meta_row) if meta_row else None, [dict(row) for row in touch_rows], creator_view=not owner)

    def list_for_user(self, user_id: str) -> list[dict]:
        return [self.get(row["id"], user_id=user_id) for row in self.support.list_for_user(user_id)]

    def owner_queue(self) -> list[dict]:
        return [self.get(row["id"], owner=True) for row in self.support.list_all()]

    def metrics(self) -> dict:
        rows = self.owner_queue()
        attached = [row for row in rows if row["service"]]
        by_priority = {key: 0 for key in PRIORITY_POLICY}
        first_response_recorded = 0
        waiting: dict[str, int] = {}
        for row in attached:
            service = row["service"]
            by_priority[service["priority"]] += 1
            if service["first_substantive_response_at"]:
                first_response_recorded += 1
            waiting[service["waiting_on"]] = waiting.get(service["waiting_on"], 0) + 1
        return {
            "total_cases": len(rows),
            "sla_attached": len(attached),
            "by_priority": by_priority,
            "first_substantive_response_recorded": first_response_recorded,
            "waiting_on": waiting,
            "target_clock_is_staffed_or_business_policy": True,
            "external_platform_time_is_not_esp_sla": True,
        }


support_sla = EspSupportSlaStore()


@router.get("/command-center/api/support-v2/cases")
def member_service_cases(request: Request):
    member, membership = require_esp_hub_member(request)
    return {
        "cases": support_sla.list_for_user(member.user_id),
        "categories": SERVICE_CATEGORIES,
        "priority_policy": PRIORITY_POLICY,
        "owner": _is_owner(membership),
    }


@router.post("/command-center/api/support-v2/cases")
def create_service_case(body: ServiceCaseCreate, request: Request):
    member, _membership = require_esp_hub_member(request)
    try:
        return {"case": support_sla.create(member.user_id, body)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/command-center/api/support-v2/owner/queue")
def owner_service_queue(request: Request):
    _member, membership = require_esp_hub_member(request)
    if not _is_owner(membership):
        raise HTTPException(403, "ESP Owner access is required")
    return {"cases": support_sla.owner_queue(), "metrics": support_sla.metrics()}


@router.post("/command-center/api/support-v2/owner/cases/{case_id}/touches")
def owner_service_touch(case_id: str, body: SupportTouchCreate, request: Request):
    member, membership = require_esp_hub_member(request)
    if not _is_owner(membership):
        raise HTTPException(403, "ESP Owner access is required")
    try:
        return {"case": support_sla.add_touch(case_id, body, actor=member.user_id)}
    except KeyError as exc:
        raise HTTPException(404, "Support case not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/command-center/api/support-v2/owner/cases/{case_id}/service")
def owner_service_meta(case_id: str, body: ServiceMetaUpdate, request: Request):
    _member, membership = require_esp_hub_member(request)
    if not _is_owner(membership):
        raise HTTPException(403, "ESP Owner access is required")
    try:
        return {"case": support_sla.update_meta(case_id, body)}
    except KeyError as exc:
        raise HTTPException(404, "Support case not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


__all__ = ["router", "support_sla", "EspSupportSlaStore", "SERVICE_CATEGORIES", "PRIORITY_POLICY"]
