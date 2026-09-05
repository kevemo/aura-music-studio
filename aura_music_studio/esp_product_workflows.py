from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .audit import AuditLedger
from .esp_command_center import EspStore, esp
from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["Chat 9 Product Workflows"])

LeadStatus = Literal[
    "discovered", "review", "assigned", "contacted", "replied", "interested",
    "follow_up", "applied", "accepted", "activated", "declined", "not_suitable",
    "do_not_contact",
]
EvidenceSource = Literal["screenshot", "csv", "xlsx", "pdf", "manual", "provider_api", "shared_sky"]
EvidenceStatus = Literal["draft", "reviewed", "confirmed", "rejected"]
AnnouncementAudience = Literal["everyone", "creators", "agents", "both", "region", "individual"]
AnnouncementStatus = Literal["draft", "scheduled", "published", "expired"]

_HANDLE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _clean(value: str | None, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


def _roles(membership: dict) -> set[str]:
    if membership.get("status") == "owner" or (membership.get("roles") or "").lower() == "owner":
        return {"owner", "creator", "agent", "both"}
    role = (membership.get("roles") or "").strip().lower()
    if role == "both":
        return {"creator", "agent", "both"}
    return {role} if role else set()


def _require_role(request: Request, *allowed: str):
    member, membership = require_esp_hub_member(request)
    if not _roles(membership).intersection(allowed):
        raise HTTPException(403, f"ESP {'/'.join(allowed)} authority is required")
    return member, membership


class StaleVersionError(RuntimeError):
    pass


class CreatorProfileUpdate(BaseModel):
    expected_version: int = Field(ge=0)
    public_display_name: str = Field(default="", max_length=120)
    avatar_ref: str | None = Field(default=None, max_length=240)
    banner_ref: str | None = Field(default=None, max_length=240)
    bio: str = Field(default="", max_length=1200)
    public_region: str = Field(default="", max_length=120)
    languages: list[str] = Field(default_factory=list, max_length=20)
    primary_niche: str = Field(default="", max_length=120)
    secondary_niche: str = Field(default="", max_length=120)
    public_social_links: dict[str, str] = Field(default_factory=dict)
    discoverable: bool = False
    timezone: str = Field(default="", max_length=80)
    live_experience: str = Field(default="", max_length=2000)
    goals: list[str] = Field(default_factory=list, max_length=30)
    schedule: dict[str, Any] = Field(default_factory=dict)
    equipment: list[str] = Field(default_factory=list, max_length=100)
    specialisms: list[str] = Field(default_factory=list, max_length=40)
    acknowledgements: dict[str, str | bool] = Field(default_factory=dict)
    onboarding_status: Literal["not_started", "in_progress", "complete"] = "in_progress"


class EvidenceMetricInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    value: Any = None
    unit: str = Field(default="", max_length=40)
    confidence: float | None = Field(default=None, ge=0, le=1)


class EvidenceBatchInput(BaseModel):
    creator_user_id: str | None = Field(default=None, max_length=128)
    source_type: EvidenceSource
    provider: str = Field(default="", max_length=80)
    period_start: str | None = Field(default=None, max_length=40)
    period_end: str | None = Field(default=None, max_length=40)
    captured_at: str | None = Field(default=None, max_length=80)
    raw_evidence_ref: str = Field(min_length=1, max_length=512)
    notes: str = Field(default="", max_length=2000)
    metrics: list[EvidenceMetricInput] = Field(default_factory=list, max_length=200)


class MetricCorrection(BaseModel):
    expected_version: int = Field(ge=1)
    value: Any = None
    unit: str = Field(default="", max_length=40)
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason: str = Field(min_length=2, max_length=1000)


class EvidenceStatusUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    status: EvidenceStatus
    reason: str = Field(default="", max_length=1000)


class LeadCreate(BaseModel):
    platform: str = Field(min_length=1, max_length=80)
    handle: str = Field(min_length=1, max_length=120)
    public_profile_url: str = Field(default="", max_length=1000)
    region: str = Field(default="", max_length=120)
    niche: str = Field(default="", max_length=120)
    source: str = Field(default="manual", max_length=160)
    notes: str = Field(default="", max_length=2000)
    follow_up_at: str | None = Field(default=None, max_length=80)


class LeadUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    status: LeadStatus | None = None
    follow_up_at: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=2000)
    do_not_contact: bool | None = None


class AnnouncementCreate(BaseModel):
    title: str = Field(min_length=2, max_length=240)
    body: str = Field(min_length=2, max_length=8000)
    audience: AnnouncementAudience
    audience_value: str = Field(default="", max_length=160)
    priority: Literal["normal", "high", "urgent"] = "normal"
    acknowledgement_required: bool = False
    publish_at: str | None = Field(default=None, max_length=80)
    expires_at: str | None = Field(default=None, max_length=80)
    status: AnnouncementStatus = "draft"
    reason: str = Field(default="", max_length=1000)
    confirm_publish: bool = False


class Chat9WorkflowStore:
    """Durable workflow state owned by Chat 9.

    This store intentionally does not implement auth, subscriptions, Coins/Gifts, LIVE transport,
    Battle scoring, creator-media storage or social-provider OAuth. It references canonical user IDs
    and existing assignment/audit infrastructure and keeps imported evidence explicitly separate from
    realtime/provider-authoritative data.
    """

    def __init__(self, esp_store: EspStore | None = None):
        self.esp = esp_store or esp
        self.db_path = self.esp.db_path
        self.audit = AuditLedger(self.esp.accounts)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS esp_creator_workflow_profiles (
                    user_id TEXT PRIMARY KEY,
                    public_display_name TEXT NOT NULL DEFAULT '',
                    avatar_ref TEXT,
                    banner_ref TEXT,
                    bio TEXT NOT NULL DEFAULT '',
                    public_region TEXT NOT NULL DEFAULT '',
                    languages_json TEXT NOT NULL DEFAULT '[]',
                    primary_niche TEXT NOT NULL DEFAULT '',
                    secondary_niche TEXT NOT NULL DEFAULT '',
                    public_social_links_json TEXT NOT NULL DEFAULT '{}',
                    discoverable INTEGER NOT NULL DEFAULT 0,
                    timezone TEXT NOT NULL DEFAULT '',
                    live_experience TEXT NOT NULL DEFAULT '',
                    goals_json TEXT NOT NULL DEFAULT '[]',
                    schedule_json TEXT NOT NULL DEFAULT '{}',
                    equipment_json TEXT NOT NULL DEFAULT '[]',
                    specialisms_json TEXT NOT NULL DEFAULT '[]',
                    acknowledgements_json TEXT NOT NULL DEFAULT '{}',
                    onboarding_status TEXT NOT NULL DEFAULT 'not_started',
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS esp_creator_evidence_batches (
                    id TEXT PRIMARY KEY,
                    creator_user_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT '',
                    period_start TEXT,
                    period_end TEXT,
                    captured_at TEXT,
                    imported_at TEXT NOT NULL,
                    uploader_user_id TEXT NOT NULL,
                    raw_evidence_ref TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'draft',
                    version INTEGER NOT NULL DEFAULT 1,
                    reviewed_by TEXT,
                    reviewed_at TEXT,
                    review_reason TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(creator_user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(uploader_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_chat9_evidence_creator
                    ON esp_creator_evidence_batches(creator_user_id, imported_at DESC);

                CREATE TABLE IF NOT EXISTS esp_creator_evidence_metrics (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    unit TEXT NOT NULL DEFAULT '',
                    confidence REAL,
                    needs_review INTEGER NOT NULL DEFAULT 1,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(batch_id, metric_name),
                    FOREIGN KEY(batch_id) REFERENCES esp_creator_evidence_batches(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS esp_creator_evidence_corrections (
                    id TEXT PRIMARY KEY,
                    metric_id TEXT NOT NULL,
                    previous_value_json TEXT NOT NULL,
                    new_value_json TEXT NOT NULL,
                    previous_unit TEXT NOT NULL DEFAULT '',
                    new_unit TEXT NOT NULL DEFAULT '',
                    previous_confidence REAL,
                    new_confidence REAL,
                    corrected_by TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(metric_id) REFERENCES esp_creator_evidence_metrics(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS esp_recruitment_leads (
                    id TEXT PRIMARY KEY,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    platform TEXT NOT NULL,
                    handle TEXT NOT NULL,
                    public_profile_url TEXT NOT NULL DEFAULT '',
                    region TEXT NOT NULL DEFAULT '',
                    niche TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'manual',
                    assigned_agent_user_id TEXT,
                    status TEXT NOT NULL DEFAULT 'discovered',
                    follow_up_at TEXT,
                    notes TEXT NOT NULL DEFAULT '',
                    do_not_contact INTEGER NOT NULL DEFAULT 0,
                    converted_user_id TEXT,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(assigned_agent_user_id) REFERENCES users(id) ON DELETE SET NULL,
                    FOREIGN KEY(converted_user_id) REFERENCES users(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chat9_leads_agent
                    ON esp_recruitment_leads(assigned_agent_user_id,status,follow_up_at);
                CREATE INDEX IF NOT EXISTS idx_chat9_leads_region
                    ON esp_recruitment_leads(region,status,updated_at DESC);

                CREATE TABLE IF NOT EXISTS esp_recruitment_lead_events (
                    id TEXT PRIMARY KEY,
                    lead_id TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(lead_id) REFERENCES esp_recruitment_leads(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS esp_announcements (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    audience TEXT NOT NULL,
                    audience_value TEXT NOT NULL DEFAULT '',
                    priority TEXT NOT NULL DEFAULT 'normal',
                    acknowledgement_required INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'draft',
                    publish_at TEXT,
                    expires_at TEXT,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_chat9_announcements_state
                    ON esp_announcements(status,publish_at,expires_at);

                CREATE TABLE IF NOT EXISTS esp_announcement_acknowledgements (
                    announcement_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    acknowledged_at TEXT NOT NULL,
                    PRIMARY KEY(announcement_id,user_id),
                    FOREIGN KEY(announcement_id) REFERENCES esp_announcements(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                """
            )

    def _user_exists(self, user_id: str) -> bool:
        return bool(self.esp.accounts.get_user(user_id))

    @staticmethod
    def _profile_dict(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        item = dict(row)
        for source, target, default in (
            ("languages_json", "languages", []),
            ("public_social_links_json", "public_social_links", {}),
            ("goals_json", "goals", []),
            ("schedule_json", "schedule", {}),
            ("equipment_json", "equipment", []),
            ("specialisms_json", "specialisms", []),
            ("acknowledgements_json", "acknowledgements", {}),
        ):
            item[target] = _loads(item.pop(source), default)
        item["discoverable"] = bool(item["discoverable"])
        return item

    def profile(self, user_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_creator_workflow_profiles WHERE user_id=?", (user_id,)).fetchone()
        return self._profile_dict(row)

    def save_profile(self, user_id: str, payload: CreatorProfileUpdate, *, actor: str) -> dict:
        if not self._user_exists(user_id):
            raise KeyError("User not found")
        now = _now()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            current = con.execute(
                "SELECT version FROM esp_creator_workflow_profiles WHERE user_id=?", (user_id,)
            ).fetchone()
            current_version = int(current["version"]) if current else 0
            if payload.expected_version != current_version:
                raise StaleVersionError(f"stale_version: expected {payload.expected_version}, current {current_version}")
            version = current_version + 1
            values = {
                "user_id": user_id,
                "public_display_name": _clean(payload.public_display_name, 120),
                "avatar_ref": payload.avatar_ref,
                "banner_ref": payload.banner_ref,
                "bio": (payload.bio or "").strip()[:1200],
                "public_region": _clean(payload.public_region, 120),
                "languages_json": _json([_clean(v, 80) for v in payload.languages if _clean(v, 80)]),
                "primary_niche": _clean(payload.primary_niche, 120),
                "secondary_niche": _clean(payload.secondary_niche, 120),
                "public_social_links_json": _json({str(k)[:80]: str(v)[:1000] for k, v in payload.public_social_links.items()}),
                "discoverable": 1 if payload.discoverable else 0,
                "timezone": _clean(payload.timezone, 80),
                "live_experience": (payload.live_experience or "").strip()[:2000],
                "goals_json": _json([_clean(v, 500) for v in payload.goals if _clean(v, 500)]),
                "schedule_json": _json(payload.schedule),
                "equipment_json": _json([_clean(v, 240) for v in payload.equipment if _clean(v, 240)]),
                "specialisms_json": _json([_clean(v, 120) for v in payload.specialisms if _clean(v, 120)]),
                "acknowledgements_json": _json(payload.acknowledgements),
                "onboarding_status": payload.onboarding_status,
                "version": version,
                "created_at": now,
                "updated_at": now,
            }
            con.execute(
                """INSERT INTO esp_creator_workflow_profiles
                   (user_id,public_display_name,avatar_ref,banner_ref,bio,public_region,languages_json,
                    primary_niche,secondary_niche,public_social_links_json,discoverable,timezone,live_experience,
                    goals_json,schedule_json,equipment_json,specialisms_json,acknowledgements_json,onboarding_status,
                    version,created_at,updated_at)
                   VALUES (:user_id,:public_display_name,:avatar_ref,:banner_ref,:bio,:public_region,:languages_json,
                    :primary_niche,:secondary_niche,:public_social_links_json,:discoverable,:timezone,:live_experience,
                    :goals_json,:schedule_json,:equipment_json,:specialisms_json,:acknowledgements_json,:onboarding_status,
                    :version,:created_at,:updated_at)
                   ON CONFLICT(user_id) DO UPDATE SET
                    public_display_name=excluded.public_display_name,avatar_ref=excluded.avatar_ref,banner_ref=excluded.banner_ref,
                    bio=excluded.bio,public_region=excluded.public_region,languages_json=excluded.languages_json,
                    primary_niche=excluded.primary_niche,secondary_niche=excluded.secondary_niche,
                    public_social_links_json=excluded.public_social_links_json,discoverable=excluded.discoverable,
                    timezone=excluded.timezone,live_experience=excluded.live_experience,goals_json=excluded.goals_json,
                    schedule_json=excluded.schedule_json,equipment_json=excluded.equipment_json,
                    specialisms_json=excluded.specialisms_json,acknowledgements_json=excluded.acknowledgements_json,
                    onboarding_status=excluded.onboarding_status,version=excluded.version,updated_at=excluded.updated_at""",
                values,
            )
        self.audit.append(
            actor=actor, action="chat9.creator_profile_updated", subject_user_id=user_id,
            details={"version": version, "discoverable": payload.discoverable, "onboarding_status": payload.onboarding_status},
        )
        return self.profile(user_id) or {}

    def public_profile(self, user_id: str) -> dict | None:
        profile = self.profile(user_id)
        if not profile or not profile.get("discoverable"):
            return None
        membership = self.esp.membership(user_id)
        if not membership or membership.get("status") not in {"active", "owner"}:
            return None
        if "creator" not in _roles(membership) and "owner" not in _roles(membership):
            return None
        return {
            "creator_user_id": user_id,
            "display_name": profile.get("public_display_name") or "",
            "avatar_ref": profile.get("avatar_ref"),
            "banner_ref": profile.get("banner_ref"),
            "bio": profile.get("bio") or "",
            "region": profile.get("public_region") or "",
            "languages": profile.get("languages") or [],
            "primary_niche": profile.get("primary_niche") or "",
            "secondary_niche": profile.get("secondary_niche") or "",
            "social_links": profile.get("public_social_links") or {},
            "discoverable": True,
            "source": "chat9_creator_profile",
        }

    def _assigned(self, agent_user_id: str, creator_user_id: str) -> bool:
        with self._connect() as con:
            row = con.execute(
                """SELECT 1 FROM esp_agent_creator_assignments
                   WHERE agent_user_id=? AND creator_user_id=? AND status='active' LIMIT 1""",
                (agent_user_id, creator_user_id),
            ).fetchone()
        return bool(row)

    @staticmethod
    def _validate_evidence_ref(value: str) -> str:
        clean = (value or "").strip()[:512]
        if not clean or clean.startswith("/") or clean.lower().startswith("file:") or ".." in clean:
            raise ValueError("raw_evidence_ref must be a canonical application asset/evidence reference")
        return clean

    def create_evidence(self, creator_user_id: str, payload: EvidenceBatchInput, *, uploader_user_id: str) -> dict:
        if not self._user_exists(creator_user_id):
            raise KeyError("Creator not found")
        membership = self.esp.membership(creator_user_id)
        if not membership or membership.get("status") not in {"active", "owner"}:
            raise ValueError("Creator does not have active ESP access")
        batch_id = uuid4().hex
        now = _now()
        raw_ref = self._validate_evidence_ref(payload.raw_evidence_ref)
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                """INSERT INTO esp_creator_evidence_batches
                   (id,creator_user_id,source_type,provider,period_start,period_end,captured_at,imported_at,
                    uploader_user_id,raw_evidence_ref,notes,status,version)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,'draft',1)""",
                (
                    batch_id, creator_user_id, payload.source_type, _clean(payload.provider, 80), payload.period_start,
                    payload.period_end, payload.captured_at, now, uploader_user_id, raw_ref, payload.notes.strip()[:2000],
                ),
            )
            seen: set[str] = set()
            for metric in payload.metrics:
                name = _clean(metric.name, 120).lower().replace(" ", "_")
                if not name or name in seen:
                    raise ValueError("Metric names must be unique within an evidence batch")
                seen.add(name)
                con.execute(
                    """INSERT INTO esp_creator_evidence_metrics
                       (id,batch_id,metric_name,value_json,unit,confidence,needs_review,version,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,1,?,?)""",
                    (
                        uuid4().hex, batch_id, name, _json(metric.value), _clean(metric.unit, 40), metric.confidence,
                        1 if metric.confidence is None or metric.confidence < 0.9 else 0, now, now,
                    ),
                )
        self.audit.append(
            actor=uploader_user_id, action="chat9.evidence_imported", subject_user_id=creator_user_id,
            details={"batch_id": batch_id, "source_type": payload.source_type, "provider": payload.provider, "metric_count": len(payload.metrics)},
        )
        return self.evidence_batch(batch_id)

    @staticmethod
    def _freshness(captured_at: str | None) -> str:
        if not captured_at:
            return "unknown"
        try:
            captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            if captured.tzinfo is None:
                captured = captured.replace(tzinfo=timezone.utc)
            days = max(0, (datetime.now(timezone.utc) - captured.astimezone(timezone.utc)).days)
        except Exception:
            return "unknown"
        if days <= 7:
            return "current"
        if days <= 30:
            return "aging"
        if days <= 60:
            return "update_recommended"
        return "stale"

    def evidence_batch(self, batch_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_creator_evidence_batches WHERE id=?", (batch_id,)).fetchone()
            if row is None:
                raise KeyError("Evidence batch not found")
            metrics = con.execute(
                "SELECT * FROM esp_creator_evidence_metrics WHERE batch_id=? ORDER BY metric_name", (batch_id,)
            ).fetchall()
        item = dict(row)
        item["freshness"] = self._freshness(item.get("captured_at"))
        item["realtime"] = item.get("source_type") in {"provider_api", "shared_sky"}
        item["imported_snapshot"] = not item["realtime"]
        item["metrics"] = []
        for metric in metrics:
            value = dict(metric)
            value["value"] = _loads(value.pop("value_json"), None)
            value["needs_review"] = bool(value["needs_review"])
            item["metrics"].append(value)
        return item

    def evidence_for_creator(self, creator_user_id: str, *, limit: int = 100) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT id FROM esp_creator_evidence_batches WHERE creator_user_id=? ORDER BY imported_at DESC LIMIT ?",
                (creator_user_id, max(1, min(limit, 500))),
            ).fetchall()
        return [self.evidence_batch(row["id"]) for row in rows]

    def correct_metric(self, metric_id: str, payload: MetricCorrection, *, actor: str) -> dict:
        now = _now()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            metric = con.execute("SELECT * FROM esp_creator_evidence_metrics WHERE id=?", (metric_id,)).fetchone()
            if metric is None:
                raise KeyError("Metric not found")
            if int(metric["version"]) != payload.expected_version:
                raise StaleVersionError("stale_version")
            batch = con.execute("SELECT * FROM esp_creator_evidence_batches WHERE id=?", (metric["batch_id"],)).fetchone()
            new_version = int(metric["version"]) + 1
            con.execute(
                """INSERT INTO esp_creator_evidence_corrections
                   (id,metric_id,previous_value_json,new_value_json,previous_unit,new_unit,previous_confidence,new_confidence,
                    corrected_by,reason,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    uuid4().hex, metric_id, metric["value_json"], _json(payload.value), metric["unit"],
                    _clean(payload.unit, 40), metric["confidence"], payload.confidence, actor,
                    payload.reason.strip()[:1000], now,
                ),
            )
            con.execute(
                """UPDATE esp_creator_evidence_metrics SET value_json=?,unit=?,confidence=?,needs_review=0,
                   version=?,updated_at=? WHERE id=?""",
                (_json(payload.value), _clean(payload.unit, 40), payload.confidence, new_version, now, metric_id),
            )
            con.execute(
                "UPDATE esp_creator_evidence_batches SET version=version+1 WHERE id=?", (metric["batch_id"],)
            )
        self.audit.append(
            actor=actor, action="chat9.evidence_metric_corrected",
            subject_user_id=batch["creator_user_id"] if batch else None,
            details={"batch_id": metric["batch_id"], "metric_id": metric_id, "reason": payload.reason[:300]},
        )
        return self.evidence_batch(metric["batch_id"])

    def set_evidence_status(self, batch_id: str, payload: EvidenceStatusUpdate, *, actor: str) -> dict:
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            batch = con.execute("SELECT * FROM esp_creator_evidence_batches WHERE id=?", (batch_id,)).fetchone()
            if batch is None:
                raise KeyError("Evidence batch not found")
            if int(batch["version"]) != payload.expected_version:
                raise StaleVersionError("stale_version")
            con.execute(
                """UPDATE esp_creator_evidence_batches SET status=?,version=version+1,reviewed_by=?,reviewed_at=?,review_reason=?
                   WHERE id=?""",
                (payload.status, actor, _now(), payload.reason.strip()[:1000], batch_id),
            )
        self.audit.append(
            actor=actor, action="chat9.evidence_status_changed", subject_user_id=batch["creator_user_id"],
            details={"batch_id": batch_id, "status": payload.status, "reason": payload.reason[:300]},
        )
        return self.evidence_batch(batch_id)

    @staticmethod
    def _lead_key(platform: str, handle: str) -> tuple[str, str, str]:
        provider = _clean(platform, 80).lower()
        normalized = (handle or "").strip().lstrip("@").lower()
        if not provider or not _HANDLE.fullmatch(normalized):
            raise ValueError("Lead platform and public handle are invalid")
        key = hashlib.sha256(f"{provider}|{normalized}".encode("utf-8")).hexdigest()
        return key, provider, normalized

    def create_lead(self, payload: LeadCreate, *, actor_user_id: str, assigned_agent_user_id: str) -> dict:
        key, platform, handle = self._lead_key(payload.platform, payload.handle)
        now = _now()
        lead_id = uuid4().hex
        if not self._user_exists(assigned_agent_user_id):
            raise KeyError("Assigned agent not found")
        try:
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute(
                    """INSERT INTO esp_recruitment_leads
                       (id,dedupe_key,platform,handle,public_profile_url,region,niche,source,assigned_agent_user_id,status,
                        follow_up_at,notes,do_not_contact,version,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,'discovered',?,?,0,1,?,?)""",
                    (
                        lead_id, key, platform, handle, payload.public_profile_url.strip()[:1000], _clean(payload.region, 120),
                        _clean(payload.niche, 120), _clean(payload.source, 160), assigned_agent_user_id, payload.follow_up_at,
                        payload.notes.strip()[:2000], now, now,
                    ),
                )
                self._lead_event(con, lead_id, actor_user_id, "lead_created", {"assigned_agent_user_id": assigned_agent_user_id})
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                raise FileExistsError("duplicate_lead: this public platform/handle is already in the ESP lead database") from exc
            raise
        self.audit.append(
            actor=actor_user_id, action="chat9.lead_created",
            details={"lead_id": lead_id, "platform": platform, "assigned_agent_user_id": assigned_agent_user_id},
        )
        return self.lead(lead_id)

    def _lead_event(self, con: sqlite3.Connection, lead_id: str, actor: str, action: str, details: dict | None = None) -> None:
        con.execute(
            "INSERT INTO esp_recruitment_lead_events(id,lead_id,actor_user_id,action,details_json,created_at) VALUES (?,?,?,?,?,?)",
            (uuid4().hex, lead_id, actor, action[:120], _json(details or {}), _now()),
        )

    def lead(self, lead_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_recruitment_leads WHERE id=?", (lead_id,)).fetchone()
            if row is None:
                raise KeyError("Lead not found")
            events = con.execute(
                "SELECT * FROM esp_recruitment_lead_events WHERE lead_id=? ORDER BY created_at", (lead_id,)
            ).fetchall()
        item = dict(row)
        item["do_not_contact"] = bool(item["do_not_contact"])
        item["history"] = [{**dict(event), "details": _loads(event["details_json"], {})} for event in events]
        for event in item["history"]:
            event.pop("details_json", None)
        return item

    def leads_for_agent(self, agent_user_id: str, *, limit: int = 200) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT id FROM esp_recruitment_leads WHERE assigned_agent_user_id=?
                   ORDER BY CASE WHEN follow_up_at IS NULL THEN 1 ELSE 0 END, follow_up_at, updated_at DESC LIMIT ?""",
                (agent_user_id, max(1, min(limit, 500))),
            ).fetchall()
        return [self.lead(row["id"]) for row in rows]

    def update_lead(self, lead_id: str, payload: LeadUpdate, *, actor_user_id: str, require_assignee: str | None = None) -> dict:
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM esp_recruitment_leads WHERE id=?", (lead_id,)).fetchone()
            if row is None:
                raise KeyError("Lead not found")
            if require_assignee and row["assigned_agent_user_id"] != require_assignee:
                raise PermissionError("Lead is not assigned to this agent")
            if int(row["version"]) != payload.expected_version:
                raise StaleVersionError("stale_version")
            do_not_contact = bool(row["do_not_contact"]) if payload.do_not_contact is None else payload.do_not_contact
            status = payload.status or row["status"]
            if do_not_contact:
                status = "do_not_contact"
            elif status in {"contacted", "replied", "interested", "follow_up", "applied", "accepted", "activated"} and bool(row["do_not_contact"]):
                raise PermissionError("Lead is marked do not contact")
            notes = row["notes"] if payload.notes is None else payload.notes.strip()[:2000]
            follow_up = row["follow_up_at"] if payload.follow_up_at is None else payload.follow_up_at
            con.execute(
                """UPDATE esp_recruitment_leads SET status=?,follow_up_at=?,notes=?,do_not_contact=?,version=version+1,
                   updated_at=? WHERE id=?""",
                (status, follow_up, notes, 1 if do_not_contact else 0, _now(), lead_id),
            )
            self._lead_event(
                con, lead_id, actor_user_id, "lead_updated",
                {"status": status, "do_not_contact": do_not_contact, "follow_up_at": follow_up},
            )
        self.audit.append(
            actor=actor_user_id, action="chat9.lead_updated",
            details={"lead_id": lead_id, "status": status, "do_not_contact": do_not_contact},
        )
        return self.lead(lead_id)

    def create_announcement(self, payload: AnnouncementCreate, *, actor_user_id: str) -> dict:
        if payload.status in {"published", "scheduled"} and not payload.confirm_publish:
            raise PermissionError("high_impact_confirmation_required")
        if payload.audience in {"region", "individual"} and not _clean(payload.audience_value, 160):
            raise ValueError("audience_value is required for region/individual announcements")
        now = _now()
        announcement_id = uuid4().hex
        publish_at = payload.publish_at or (now if payload.status == "published" else None)
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_announcements
                   (id,title,body,audience,audience_value,priority,acknowledgement_required,status,publish_at,expires_at,
                    created_by,created_at,updated_at,version)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                (
                    announcement_id, _clean(payload.title, 240), payload.body.strip()[:8000], payload.audience,
                    _clean(payload.audience_value, 160), payload.priority, 1 if payload.acknowledgement_required else 0,
                    payload.status, publish_at, payload.expires_at, actor_user_id, now, now,
                ),
            )
        self.audit.append(
            actor=actor_user_id, action="chat9.announcement_created",
            details={"announcement_id": announcement_id, "status": payload.status, "audience": payload.audience, "reason": payload.reason[:300]},
        )
        return self.announcement(announcement_id)

    def announcement(self, announcement_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_announcements WHERE id=?", (announcement_id,)).fetchone()
        if row is None:
            raise KeyError("Announcement not found")
        item = dict(row)
        item["acknowledgement_required"] = bool(item["acknowledgement_required"])
        return item

    @staticmethod
    def _announcement_matches(item: dict, user_id: str, membership: dict) -> bool:
        audience = item["audience"]
        role_set = _roles(membership)
        if "owner" in role_set:
            return True
        if audience == "everyone":
            return True
        if audience == "creators":
            return "creator" in role_set
        if audience == "agents":
            return "agent" in role_set
        if audience == "both":
            return "both" in role_set
        if audience == "region":
            return (membership.get("region") or "").strip().lower() == (item.get("audience_value") or "").strip().lower()
        if audience == "individual":
            return item.get("audience_value") == user_id
        return False

    def visible_announcements(self, user_id: str, membership: dict) -> list[dict]:
        now = _now()
        with self._connect() as con:
            rows = con.execute(
                """SELECT * FROM esp_announcements
                   WHERE status IN ('published','scheduled')
                     AND (publish_at IS NULL OR publish_at<=?)
                     AND (expires_at IS NULL OR expires_at>?)
                   ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 ELSE 2 END, created_at DESC""",
                (now, now),
            ).fetchall()
        return [dict(row) for row in rows if self._announcement_matches(dict(row), user_id, membership)]

    def acknowledge_announcement(self, announcement_id: str, user_id: str, membership: dict) -> dict:
        item = self.announcement(announcement_id)
        if not self._announcement_matches(item, user_id, membership):
            raise PermissionError("Announcement is not visible to this user")
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_announcement_acknowledgements(announcement_id,user_id,acknowledged_at)
                   VALUES (?,?,?) ON CONFLICT(announcement_id,user_id) DO NOTHING""",
                (announcement_id, user_id, _now()),
            )
        return {"announcement_id": announcement_id, "user_id": user_id, "acknowledged": True}


workflows = Chat9WorkflowStore()


def _creator_access(request: Request):
    return _require_role(request, "creator", "both", "owner")


def _agent_access(request: Request):
    return _require_role(request, "agent", "both", "owner")


def _owner_access(request: Request):
    return _require_role(request, "owner")


def _authorize_creator_record(actor_user_id: str, membership: dict, creator_user_id: str) -> None:
    roles = _roles(membership)
    if "owner" in roles or actor_user_id == creator_user_id:
        return
    if "agent" in roles and workflows._assigned(actor_user_id, creator_user_id):
        return
    raise HTTPException(403, "Creator record is outside the authorised assignment boundary")


@router.get("/command-center/api/workflows/capabilities")
def capability_manifest(request: Request):
    _member, membership = require_esp_hub_member(request)
    return {
        "roles": sorted(_roles(membership)),
        "creator_profile": "built",
        "creator_onboarding": "built",
        "evidence_import": "built_manual_review_required",
        "tiktok_live_backstage": "external_not_connected",
        "recruitment_crm": "built_manual_authorised_inputs_only",
        "announcements": "built",
        "shared_sky_profile_handoff": "built_read_model",
        "economy": "external_chat5_contract",
        "battle": "external_chat6_contract",
        "stream_transport": "external_chat2_contract",
        "social_provider_oauth": "existing_provider_capability_registry",
    }


@router.get("/command-center/api/workflows/creator-profile")
def get_creator_profile(request: Request):
    member, _membership = _creator_access(request)
    return {"profile": workflows.profile(member.user_id), "missing_is_missing": True}


@router.put("/command-center/api/workflows/creator-profile")
def put_creator_profile(body: CreatorProfileUpdate, request: Request):
    member, _membership = _creator_access(request)
    try:
        return {"profile": workflows.save_profile(member.user_id, body, actor=member.user_id)}
    except StaleVersionError as exc:
        raise HTTPException(409, {"code": "stale_version", "message": str(exc)}) from exc
    except KeyError as exc:
        raise HTTPException(404, "Creator account not found") from exc


@router.get("/shared-sky/public/creators/{creator_user_id}")
def shared_sky_public_creator(creator_user_id: str):
    profile = workflows.public_profile(creator_user_id)
    if profile is None:
        raise HTTPException(404, "Public creator profile not found")
    return {"creator": profile, "private_fields_included": False}


@router.post("/command-center/api/workflows/evidence")
def create_evidence(body: EvidenceBatchInput, request: Request):
    member, membership = require_esp_hub_member(request)
    creator_user_id = body.creator_user_id or member.user_id
    _authorize_creator_record(member.user_id, membership, creator_user_id)
    try:
        batch = workflows.create_evidence(creator_user_id, body, uploader_user_id=member.user_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"batch": batch, "direct_backstage_connection": False}


@router.get("/command-center/api/workflows/evidence/{creator_user_id}")
def list_evidence(creator_user_id: str, request: Request):
    member, membership = require_esp_hub_member(request)
    _authorize_creator_record(member.user_id, membership, creator_user_id)
    return {"batches": workflows.evidence_for_creator(creator_user_id), "direct_backstage_connection": False}


@router.patch("/command-center/api/workflows/evidence/metrics/{metric_id}")
def correct_evidence_metric(metric_id: str, body: MetricCorrection, request: Request):
    member, membership = require_esp_hub_member(request)
    try:
        with workflows._connect() as con:
            row = con.execute(
                """SELECT b.creator_user_id FROM esp_creator_evidence_metrics m
                   JOIN esp_creator_evidence_batches b ON b.id=m.batch_id WHERE m.id=?""",
                (metric_id,),
            ).fetchone()
        if row is None:
            raise KeyError("Metric not found")
        _authorize_creator_record(member.user_id, membership, row["creator_user_id"])
        return {"batch": workflows.correct_metric(metric_id, body, actor=member.user_id)}
    except StaleVersionError as exc:
        raise HTTPException(409, {"code": "stale_version"}) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.patch("/command-center/api/workflows/evidence/{batch_id}/status")
def update_evidence_status(batch_id: str, body: EvidenceStatusUpdate, request: Request):
    member, membership = require_esp_hub_member(request)
    try:
        batch = workflows.evidence_batch(batch_id)
        _authorize_creator_record(member.user_id, membership, batch["creator_user_id"])
        return {"batch": workflows.set_evidence_status(batch_id, body, actor=member.user_id)}
    except StaleVersionError as exc:
        raise HTTPException(409, {"code": "stale_version"}) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/command-center/api/workflows/leads")
def agent_leads(request: Request):
    member, _membership = _agent_access(request)
    return {"leads": workflows.leads_for_agent(member.user_id), "scope": "assigned_agent_only"}


@router.post("/command-center/api/workflows/leads")
def create_agent_lead(body: LeadCreate, request: Request):
    member, _membership = _agent_access(request)
    try:
        return {"lead": workflows.create_lead(body, actor_user_id=member.user_id, assigned_agent_user_id=member.user_id)}
    except FileExistsError as exc:
        raise HTTPException(409, {"code": "duplicate_lead", "message": str(exc)}) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/command-center/api/workflows/leads/{lead_id}")
def update_agent_lead(lead_id: str, body: LeadUpdate, request: Request):
    member, membership = _agent_access(request)
    assignee_guard = None if "owner" in _roles(membership) else member.user_id
    try:
        return {"lead": workflows.update_lead(lead_id, body, actor_user_id=member.user_id, require_assignee=assignee_guard)}
    except StaleVersionError as exc:
        raise HTTPException(409, {"code": "stale_version"}) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/command-center/api/workflows/announcements")
def visible_announcements(request: Request):
    member, membership = require_esp_hub_member(request)
    return {"announcements": workflows.visible_announcements(member.user_id, membership)}


@router.post("/command-center/api/workflows/announcements")
def create_announcement(body: AnnouncementCreate, request: Request):
    member, _membership = _owner_access(request)
    try:
        return {"announcement": workflows.create_announcement(body, actor_user_id=member.user_id)}
    except PermissionError as exc:
        raise HTTPException(409, {"code": "high_impact_confirmation_required", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/command-center/api/workflows/announcements/{announcement_id}/acknowledge")
def acknowledge_announcement(announcement_id: str, request: Request):
    member, membership = require_esp_hub_member(request)
    try:
        return workflows.acknowledge_announcement(announcement_id, member.user_id, membership)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


__all__ = ["Chat9WorkflowStore", "workflows", "router", "StaleVersionError"]
