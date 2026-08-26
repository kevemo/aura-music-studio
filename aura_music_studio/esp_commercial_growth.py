from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_command_center import esp
from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["ESP Commercial Growth"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _roles(membership: dict) -> set[str]:
    if membership.get("status") == "owner":
        return {"creator", "agent", "owner"}
    value = str(membership.get("roles") or "").lower()
    if value == "both":
        return {"creator", "agent"}
    return {value} if value else set()


def _is_owner(membership: dict) -> bool:
    return membership.get("status") == "owner"


def _require_creator(membership: dict) -> None:
    if "creator" not in _roles(membership) and not _is_owner(membership):
        raise HTTPException(403, "Creator or Owner ESP role required")


def _require_agent(membership: dict) -> None:
    if "agent" not in _roles(membership) and not _is_owner(membership):
        raise HTTPException(403, "Agent or Owner ESP role required")


def _safe_url(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) > 2000:
        raise ValueError("External URL is too long")
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("External workflow links must use public HTTPS URLs")
    return text


def _clean_list(values: list[str] | None, *, limit: int = 20, item_limit: int = 100) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        clean = " ".join(str(value).split())[:item_limit]
        folded = clean.casefold()
        if clean and folded not in seen:
            out.append(clean)
            seen.add(folded)
        if len(out) >= limit:
            break
    return out


class CommercialProfileUpdate(BaseModel):
    shop_opt_in: bool = False
    brand_opt_in: bool = False
    region: str = Field(default="", max_length=80)
    niches: list[str] = Field(default_factory=list)
    media_kit_url: str = Field(default="", max_length=2000)
    rate_notes: str = Field(default="", max_length=1200)
    disclosure_acknowledged: bool = False


class OpportunityCreate(BaseModel):
    title: str = Field(min_length=2, max_length=220)
    region: str = Field(default="global", max_length=80)
    niche: str = Field(default="all", max_length=100)
    category: str = Field(default="general", max_length=120)
    brief: str = Field(default="", max_length=5000)
    deadline: str = Field(default="", max_length=80)
    official_url: str = Field(default="", max_length=2000)
    compensation_note: str = Field(default="", max_length=800)
    status: str = Field(default="draft", max_length=40)


class ApplyRequest(BaseModel):
    note: str = Field(default="", max_length=2000)


class ApplicationUpdate(BaseModel):
    status: str = Field(default="review", max_length=40)
    sample_status: str = Field(default="not_applicable", max_length=40)
    deliverable_status: str = Field(default="not_started", max_length=40)
    content_deadline: str = Field(default="", max_length=80)
    tracking_note: str = Field(default="", max_length=2500)


class BrandLeadCreate(BaseModel):
    brand_name: str = Field(min_length=2, max_length=180)
    source_url: str = Field(default="", max_length=2000)
    contact_channel: str = Field(default="", max_length=160)
    region: str = Field(default="global", max_length=80)
    niche: str = Field(default="all", max_length=100)
    stage: str = Field(default="research", max_length=40)
    next_followup_at: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=3000)


class BrandLeadUpdate(BaseModel):
    stage: str = Field(default="research", max_length=40)
    next_followup_at: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=3000)


class CommercialGrowthStore:
    """Private ESP Shop and brand workflows inside Pulsar-Frequency House.

    This is an internal CRM/tracking layer. It never claims that an external TikTok One,
    TikTok Shop or brand action occurred unless an ESP member records the verified outcome.
    Creator opportunities are opt-in and disclosure-aware; Agent draft/lead ownership stays
    isolated unless an ESP Owner is reviewing the whole network.
    """

    OPPORTUNITY_STATUSES = {"draft", "open", "paused", "closed"}
    APP_STATUSES = {"interested", "applied", "review", "accepted", "declined", "withdrawn", "completed"}
    SAMPLE_STATUSES = {"not_applicable", "requested", "approved", "shipped", "received", "content_submitted", "complete", "declined"}
    DELIVERABLE_STATUSES = {"not_started", "planning", "in_production", "submitted", "revision", "approved", "published", "complete"}
    BRAND_STAGES = {"research", "qualified", "outreach_planned", "contacted", "follow_up", "conversation", "proposal", "won", "lost", "paused"}

    def __init__(self, db_path=None):
        self.db_path = db_path or esp.db_path
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
                CREATE TABLE IF NOT EXISTS esp_commercial_profiles (
                    user_id TEXT PRIMARY KEY,
                    shop_opt_in INTEGER NOT NULL DEFAULT 0,
                    brand_opt_in INTEGER NOT NULL DEFAULT 0,
                    region TEXT NOT NULL DEFAULT '',
                    niches_json TEXT NOT NULL DEFAULT '[]',
                    media_kit_url TEXT NOT NULL DEFAULT '',
                    rate_notes TEXT NOT NULL DEFAULT '',
                    disclosure_acknowledged INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS esp_shop_opportunities (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    region TEXT NOT NULL DEFAULT 'global',
                    niche TEXT NOT NULL DEFAULT 'all',
                    category TEXT NOT NULL DEFAULT 'general',
                    brief TEXT NOT NULL DEFAULT '',
                    deadline TEXT NOT NULL DEFAULT '',
                    official_url TEXT NOT NULL DEFAULT '',
                    compensation_note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'draft',
                    created_by_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shop_opportunity_status
                    ON esp_shop_opportunities(status,deadline,created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_shop_opportunity_owner
                    ON esp_shop_opportunities(created_by_user_id,status,created_at DESC);

                CREATE TABLE IF NOT EXISTS esp_shop_applications (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'interested',
                    note TEXT NOT NULL DEFAULT '',
                    sample_status TEXT NOT NULL DEFAULT 'not_applicable',
                    deliverable_status TEXT NOT NULL DEFAULT 'not_started',
                    content_deadline TEXT NOT NULL DEFAULT '',
                    tracking_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(opportunity_id,user_id),
                    FOREIGN KEY(opportunity_id) REFERENCES esp_shop_opportunities(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS esp_brand_leads (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    brand_name TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    contact_channel TEXT NOT NULL DEFAULT '',
                    region TEXT NOT NULL DEFAULT 'global',
                    niche TEXT NOT NULL DEFAULT 'all',
                    stage TEXT NOT NULL DEFAULT 'research',
                    next_followup_at TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(owner_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_brand_lead_owner_stage
                    ON esp_brand_leads(owner_user_id,stage,next_followup_at);

                CREATE TABLE IF NOT EXISTS esp_brand_opportunities (
                    id TEXT PRIMARY KEY,
                    brand_lead_id TEXT,
                    title TEXT NOT NULL,
                    region TEXT NOT NULL DEFAULT 'global',
                    niche TEXT NOT NULL DEFAULT 'all',
                    category TEXT NOT NULL DEFAULT 'brand_campaign',
                    brief TEXT NOT NULL DEFAULT '',
                    deadline TEXT NOT NULL DEFAULT '',
                    official_url TEXT NOT NULL DEFAULT '',
                    compensation_note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'draft',
                    created_by_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(brand_lead_id) REFERENCES esp_brand_leads(id) ON DELETE SET NULL,
                    FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_brand_opportunity_status
                    ON esp_brand_opportunities(status,deadline,created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_brand_opportunity_owner
                    ON esp_brand_opportunities(created_by_user_id,status,created_at DESC);

                CREATE TABLE IF NOT EXISTS esp_brand_applications (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'interested',
                    note TEXT NOT NULL DEFAULT '',
                    deliverable_status TEXT NOT NULL DEFAULT 'not_started',
                    content_deadline TEXT NOT NULL DEFAULT '',
                    tracking_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(opportunity_id,user_id),
                    FOREIGN KEY(opportunity_id) REFERENCES esp_brand_opportunities(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS esp_commercial_activity (
                    id TEXT PRIMARY KEY,
                    actor_user_id TEXT NOT NULL,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_commercial_activity_subject
                    ON esp_commercial_activity(subject_type,subject_id,created_at DESC);
                """
            )

    def _activity(self, con: sqlite3.Connection, actor: str, subject_type: str, subject_id: str, action: str, metadata: dict | None = None) -> None:
        con.execute(
            "INSERT INTO esp_commercial_activity(id,actor_user_id,subject_type,subject_id,action,metadata_json,created_at) VALUES (?,?,?,?,?,?,?)",
            (uuid4().hex, actor, subject_type[:60], subject_id, action[:100], json.dumps(metadata or {}, sort_keys=True), _now()),
        )

    @staticmethod
    def _decode_profile(row, user_id: str) -> dict:
        if not row:
            return {"user_id": user_id, "shop_opt_in": False, "brand_opt_in": False, "region": "", "niches": [], "media_kit_url": "", "rate_notes": "", "disclosure_acknowledged": False}
        item = dict(row)
        try:
            item["niches"] = json.loads(item.pop("niches_json") or "[]")
        except Exception:
            item["niches"] = []
        for key in ("shop_opt_in", "brand_opt_in", "disclosure_acknowledged"):
            item[key] = bool(item[key])
        return item

    def profile(self, user_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_commercial_profiles WHERE user_id=?", (user_id,)).fetchone()
        return self._decode_profile(row, user_id)

    def save_profile(self, user_id: str, body: CommercialProfileUpdate) -> dict:
        media_kit = _safe_url(body.media_kit_url)
        now = _now()
        niches = _clean_list(body.niches)
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_commercial_profiles
                (user_id,shop_opt_in,brand_opt_in,region,niches_json,media_kit_url,rate_notes,disclosure_acknowledged,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET shop_opt_in=excluded.shop_opt_in,brand_opt_in=excluded.brand_opt_in,
                region=excluded.region,niches_json=excluded.niches_json,media_kit_url=excluded.media_kit_url,
                rate_notes=excluded.rate_notes,disclosure_acknowledged=excluded.disclosure_acknowledged,updated_at=excluded.updated_at""",
                (user_id, int(body.shop_opt_in), int(body.brand_opt_in), body.region.strip()[:80], json.dumps(niches), media_kit, body.rate_notes.strip()[:1200], int(body.disclosure_acknowledged), now),
            )
            self._activity(con, user_id, "commercial_profile", user_id, "profile_updated", {"shop_opt_in": body.shop_opt_in, "brand_opt_in": body.brand_opt_in})
        return self.profile(user_id)

    def _create_opportunity(self, table: str, actor: str, body: OpportunityCreate, *, brand_lead_id: str | None = None) -> dict:
        if body.status not in self.OPPORTUNITY_STATUSES:
            raise ValueError("Unsupported opportunity status")
        url = _safe_url(body.official_url)
        item_id, now = uuid4().hex, _now()
        with self._connect() as con:
            if table == "esp_brand_opportunities":
                con.execute(
                    """INSERT INTO esp_brand_opportunities
                    (id,brand_lead_id,title,region,niche,category,brief,deadline,official_url,compensation_note,status,created_by_user_id,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (item_id, brand_lead_id, body.title.strip(), body.region.strip() or "global", body.niche.strip() or "all", body.category.strip() or "brand_campaign", body.brief.strip(), body.deadline.strip(), url, body.compensation_note.strip(), body.status, actor, now, now),
                )
                subject = "brand_opportunity"
            else:
                con.execute(
                    """INSERT INTO esp_shop_opportunities
                    (id,title,region,niche,category,brief,deadline,official_url,compensation_note,status,created_by_user_id,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (item_id, body.title.strip(), body.region.strip() or "global", body.niche.strip() or "all", body.category.strip() or "general", body.brief.strip(), body.deadline.strip(), url, body.compensation_note.strip(), body.status, actor, now, now),
                )
                subject = "shop_opportunity"
            self._activity(con, actor, subject, item_id, "created", {"status": body.status})
            row = con.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
        return dict(row)

    def create_shop(self, actor: str, body: OpportunityCreate) -> dict:
        return self._create_opportunity("esp_shop_opportunities", actor, body)

    def create_brand_opportunity(self, actor: str, body: OpportunityCreate, brand_lead_id: str | None = None, *, owner: bool = False) -> dict:
        if brand_lead_id:
            with self._connect() as con:
                lead = con.execute("SELECT id,owner_user_id FROM esp_brand_leads WHERE id=?", (brand_lead_id,)).fetchone()
            if not lead:
                raise KeyError("Brand lead not found")
            if not owner and lead["owner_user_id"] != actor:
                raise PermissionError("Brand lead belongs to another ESP agent")
        return self._create_opportunity("esp_brand_opportunities", actor, body, brand_lead_id=brand_lead_id)

    def list_opportunities(self, kind: str, *, member_user_id: str, management: bool, owner: bool = False) -> list[dict]:
        table = "esp_brand_opportunities" if kind == "brand" else "esp_shop_opportunities"
        app_table = "esp_brand_applications" if kind == "brand" else "esp_shop_applications"
        with self._connect() as con:
            if owner:
                rows = con.execute(f"SELECT * FROM {table} ORDER BY created_at DESC").fetchall()
            elif management:
                rows = con.execute(f"SELECT * FROM {table} WHERE created_by_user_id=? ORDER BY created_at DESC", (member_user_id,)).fetchall()
            else:
                rows = con.execute(f"SELECT * FROM {table} WHERE status='open' ORDER BY deadline,created_at DESC").fetchall()
            result = [dict(row) for row in rows]
            mine = {row["opportunity_id"]: dict(row) for row in con.execute(f"SELECT * FROM {app_table} WHERE user_id=?", (member_user_id,)).fetchall()}
        for row in result:
            row["my_application"] = mine.get(row["id"])
        return result

    def apply(self, kind: str, opportunity_id: str, user_id: str, note: str) -> dict:
        opp_table = "esp_brand_opportunities" if kind == "brand" else "esp_shop_opportunities"
        app_table = "esp_brand_applications" if kind == "brand" else "esp_shop_applications"
        profile = self.profile(user_id)
        required_opt_in = profile["brand_opt_in"] if kind == "brand" else profile["shop_opt_in"]
        if not required_opt_in:
            raise PermissionError(f"Enable {kind} opportunity opt-in in your commercial profile first")
        if not profile["disclosure_acknowledged"]:
            raise PermissionError("Commercial disclosure guidance must be acknowledged before applying")
        now, app_id = _now(), uuid4().hex
        with self._connect() as con:
            opp = con.execute(f"SELECT * FROM {opp_table} WHERE id=?", (opportunity_id,)).fetchone()
            if not opp:
                raise KeyError("Opportunity not found")
            if opp["status"] != "open":
                raise PermissionError("Opportunity is not open for applications")
            if kind == "brand":
                con.execute(
                    """INSERT INTO esp_brand_applications
                    (id,opportunity_id,user_id,status,note,deliverable_status,content_deadline,tracking_note,created_at,updated_at)
                    VALUES (?,?,?,'applied',?,'not_started','','',?,?)""",
                    (app_id, opportunity_id, user_id, note.strip()[:2000], now, now),
                )
            else:
                con.execute(
                    """INSERT INTO esp_shop_applications
                    (id,opportunity_id,user_id,status,note,sample_status,deliverable_status,content_deadline,tracking_note,created_at,updated_at)
                    VALUES (?,?,?,'applied',?,'not_applicable','not_started','','',?,?)""",
                    (app_id, opportunity_id, user_id, note.strip()[:2000], now, now),
                )
            self._activity(con, user_id, f"{kind}_application", app_id, "applied", {"opportunity_id": opportunity_id})
            row = con.execute(f"SELECT * FROM {app_table} WHERE id=?", (app_id,)).fetchone()
        return dict(row)

    def update_application(self, kind: str, app_id: str, actor: str, body: ApplicationUpdate, *, owner: bool) -> dict:
        table = "esp_brand_applications" if kind == "brand" else "esp_shop_applications"
        opp_table = "esp_brand_opportunities" if kind == "brand" else "esp_shop_opportunities"
        if body.status not in self.APP_STATUSES or body.deliverable_status not in self.DELIVERABLE_STATUSES:
            raise ValueError("Unsupported application/deliverable status")
        if kind == "shop" and body.sample_status not in self.SAMPLE_STATUSES:
            raise ValueError("Unsupported sample status")
        with self._connect() as con:
            row = con.execute(f"SELECT * FROM {table} WHERE id=?", (app_id,)).fetchone()
            if not row:
                raise KeyError("Application not found")
            opp = con.execute(f"SELECT * FROM {opp_table} WHERE id=?", (row["opportunity_id"],)).fetchone()
            if not owner and (not opp or opp["created_by_user_id"] != actor):
                raise PermissionError("Only the opportunity owner or ESP ownership can update this application")
            if kind == "shop":
                con.execute(
                    f"UPDATE {table} SET status=?,sample_status=?,deliverable_status=?,content_deadline=?,tracking_note=?,updated_at=? WHERE id=?",
                    (body.status, body.sample_status, body.deliverable_status, body.content_deadline.strip(), body.tracking_note.strip()[:2500], _now(), app_id),
                )
            else:
                con.execute(
                    f"UPDATE {table} SET status=?,deliverable_status=?,content_deadline=?,tracking_note=?,updated_at=? WHERE id=?",
                    (body.status, body.deliverable_status, body.content_deadline.strip(), body.tracking_note.strip()[:2500], _now(), app_id),
                )
            self._activity(con, actor, f"{kind}_application", app_id, "application_updated", {"status": body.status, "deliverable_status": body.deliverable_status})
            updated = con.execute(f"SELECT * FROM {table} WHERE id=?", (app_id,)).fetchone()
        return dict(updated)

    def create_brand_lead(self, owner_user_id: str, body: BrandLeadCreate) -> dict:
        if body.stage not in self.BRAND_STAGES:
            raise ValueError("Unsupported brand lead stage")
        lead_id, now = uuid4().hex, _now()
        source_url = _safe_url(body.source_url)
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_brand_leads
                (id,owner_user_id,brand_name,source_url,contact_channel,region,niche,stage,next_followup_at,notes,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (lead_id, owner_user_id, body.brand_name.strip(), source_url, body.contact_channel.strip(), body.region.strip() or "global", body.niche.strip() or "all", body.stage, body.next_followup_at.strip(), body.notes.strip(), now, now),
            )
            self._activity(con, owner_user_id, "brand_lead", lead_id, "lead_created", {"stage": body.stage})
            row = con.execute("SELECT * FROM esp_brand_leads WHERE id=?", (lead_id,)).fetchone()
        return dict(row)

    def list_brand_leads(self, actor: str, *, owner: bool) -> list[dict]:
        with self._connect() as con:
            if owner:
                rows = con.execute("SELECT * FROM esp_brand_leads ORDER BY next_followup_at,updated_at DESC").fetchall()
            else:
                rows = con.execute("SELECT * FROM esp_brand_leads WHERE owner_user_id=? ORDER BY next_followup_at,updated_at DESC", (actor,)).fetchall()
        return [dict(row) for row in rows]

    def update_brand_lead(self, lead_id: str, actor: str, body: BrandLeadUpdate, *, owner: bool) -> dict:
        if body.stage not in self.BRAND_STAGES:
            raise ValueError("Unsupported brand lead stage")
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_brand_leads WHERE id=?", (lead_id,)).fetchone()
            if not row:
                raise KeyError("Brand lead not found")
            if not owner and row["owner_user_id"] != actor:
                raise PermissionError("Brand lead belongs to another ESP agent")
            con.execute("UPDATE esp_brand_leads SET stage=?,next_followup_at=?,notes=?,updated_at=? WHERE id=?", (body.stage, body.next_followup_at.strip(), body.notes.strip(), _now(), lead_id))
            self._activity(con, actor, "brand_lead", lead_id, "lead_updated", {"stage": body.stage})
            updated = con.execute("SELECT * FROM esp_brand_leads WHERE id=?", (lead_id,)).fetchone()
        return dict(updated)

    def applications_for_management(self, kind: str, actor: str, *, owner: bool) -> list[dict]:
        app_table = "esp_brand_applications" if kind == "brand" else "esp_shop_applications"
        opp_table = "esp_brand_opportunities" if kind == "brand" else "esp_shop_opportunities"
        with self._connect() as con:
            sql = f"""SELECT a.*,o.title AS opportunity_title,u.display_name,u.email
                      FROM {app_table} a JOIN {opp_table} o ON o.id=a.opportunity_id
                      JOIN users u ON u.id=a.user_id"""
            params: tuple = ()
            if not owner:
                sql += " WHERE o.created_by_user_id=?"
                params = (actor,)
            sql += " ORDER BY a.updated_at DESC"
            rows = con.execute(sql, params).fetchall()
        return [dict(row) for row in rows]


growth = CommercialGrowthStore()


def _member(request: Request):
    return require_esp_hub_member(request)


@router.get("/command-center/api/commercial/profile")
def commercial_profile(request: Request):
    member, membership = _member(request)
    _require_creator(membership)
    return {"profile": growth.profile(member.user_id), "private_esp_only": True}


@router.put("/command-center/api/commercial/profile")
def save_commercial_profile(body: CommercialProfileUpdate, request: Request):
    member, membership = _member(request)
    _require_creator(membership)
    try:
        return {"profile": growth.save_profile(member.user_id, body)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/command-center/api/commerce/opportunities")
def shop_opportunities(request: Request):
    member, membership = _member(request)
    roles = _roles(membership)
    return {"opportunities": growth.list_opportunities("shop", member_user_id=member.user_id, management=("agent" in roles), owner=_is_owner(membership))}


@router.post("/command-center/api/commerce/opportunities")
def create_shop_opportunity(body: OpportunityCreate, request: Request):
    member, membership = _member(request)
    _require_agent(membership)
    try:
        return {"opportunity": growth.create_shop(member.user_id, body)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/command-center/api/commerce/opportunities/{opportunity_id}/apply")
def apply_shop(opportunity_id: str, body: ApplyRequest, request: Request):
    member, membership = _member(request)
    _require_creator(membership)
    try:
        return {"application": growth.apply("shop", opportunity_id, member.user_id, body.note)}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "You already have an application for this opportunity") from exc


@router.get("/command-center/api/commerce/applications")
def manage_shop_applications(request: Request):
    member, membership = _member(request)
    _require_agent(membership)
    return {"applications": growth.applications_for_management("shop", member.user_id, owner=_is_owner(membership))}


@router.put("/command-center/api/commerce/applications/{application_id}")
def update_shop_application(application_id: str, body: ApplicationUpdate, request: Request):
    member, membership = _member(request)
    _require_agent(membership)
    try:
        return {"application": growth.update_application("shop", application_id, member.user_id, body, owner=_is_owner(membership))}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/command-center/api/brands/opportunities")
def brand_opportunities(request: Request):
    member, membership = _member(request)
    roles = _roles(membership)
    return {"opportunities": growth.list_opportunities("brand", member_user_id=member.user_id, management=("agent" in roles), owner=_is_owner(membership))}


@router.post("/command-center/api/brands/opportunities")
def create_brand_opportunity(body: OpportunityCreate, request: Request, brand_lead_id: str | None = None):
    member, membership = _member(request)
    _require_agent(membership)
    try:
        return {"opportunity": growth.create_brand_opportunity(member.user_id, body, brand_lead_id, owner=_is_owner(membership))}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/command-center/api/brands/opportunities/{opportunity_id}/apply")
def apply_brand(opportunity_id: str, body: ApplyRequest, request: Request):
    member, membership = _member(request)
    _require_creator(membership)
    try:
        return {"application": growth.apply("brand", opportunity_id, member.user_id, body.note)}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "You already have an application for this opportunity") from exc


@router.get("/command-center/api/brands/applications")
def manage_brand_applications(request: Request):
    member, membership = _member(request)
    _require_agent(membership)
    return {"applications": growth.applications_for_management("brand", member.user_id, owner=_is_owner(membership))}


@router.put("/command-center/api/brands/applications/{application_id}")
def update_brand_application(application_id: str, body: ApplicationUpdate, request: Request):
    member, membership = _member(request)
    _require_agent(membership)
    try:
        return {"application": growth.update_application("brand", application_id, member.user_id, body, owner=_is_owner(membership))}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/command-center/api/brands/leads")
def brand_leads(request: Request):
    member, membership = _member(request)
    _require_agent(membership)
    return {"leads": growth.list_brand_leads(member.user_id, owner=_is_owner(membership)), "external_messages_sent_automatically": False}


@router.post("/command-center/api/brands/leads")
def create_brand_lead(body: BrandLeadCreate, request: Request):
    member, membership = _member(request)
    _require_agent(membership)
    try:
        return {"lead": growth.create_brand_lead(member.user_id, body)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/command-center/api/brands/leads/{lead_id}")
def update_brand_lead(lead_id: str, body: BrandLeadUpdate, request: Request):
    member, membership = _member(request)
    _require_agent(membership)
    try:
        return {"lead": growth.update_brand_lead(lead_id, member.user_id, body, owner=_is_owner(membership))}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _portal(kind: str, request: Request) -> HTMLResponse:
    member, membership = _member(request)
    roles = _roles(membership)
    creator = "creator" in roles or _is_owner(membership)
    manager = "agent" in roles or _is_owner(membership)
    owner = _is_owner(membership)
    title = "Commerce & TikTok Shop Centre" if kind == "shop" else "Brands & Commercial Opportunities"
    path = "commerce" if kind == "shop" else "brands"
    opportunities = growth.list_opportunities(kind, member_user_id=member.user_id, management=("agent" in roles), owner=owner)
    rows = "".join(
        "<article class='card'>"
        f"<div class='top'><span class='pill'>{escape(str(item['status']).upper())}</span><span>{escape(str(item.get('deadline') or 'No deadline recorded'))}</span></div>"
        f"<h3>{escape(str(item['title']))}</h3><p>{escape(str(item.get('brief') or ''))}</p>"
        f"<small>{escape(str(item.get('region') or 'global'))} · {escape(str(item.get('niche') or 'all'))} · {escape(str(item.get('category') or ''))}</small>"
        + (f"<p><a class='btn' rel='noopener noreferrer' target='_blank' href='{escape(str(item['official_url']), quote=True)}'>Official workflow</a></p>" if item.get("official_url") else "")
        + ("<p class='good'>Application recorded.</p>" if item.get("my_application") else "")
        + "</article>"
        for item in opportunities
    ) or "<article class='card'><p class='muted'>No opportunities are currently available in this view.</p></article>"
    profile = growth.profile(member.user_id) if creator else None
    manager_note = "<article class='card guard'><b>Agent / Owner workspace</b><p>Use the private API to create opportunities you manage, review their applications and track your brand leads. Mary/Kev ownership can review the network-wide commercial pipeline. This CRM never sends messages or creates provider campaigns automatically.</p></article>" if manager else ""
    profile_note = ""
    if profile:
        profile_note = f"<article class='card'><b>Your commercial profile</b><p>Shop opt-in: <strong>{'Yes' if profile['shop_opt_in'] else 'No'}</strong> · Brand opt-in: <strong>{'Yes' if profile['brand_opt_in'] else 'No'}</strong> · Disclosure guidance acknowledged: <strong>{'Yes' if profile['disclosure_acknowledged'] else 'No'}</strong></p><p class='muted'>Applications are blocked until the relevant opt-in and disclosure acknowledgement are recorded.</p></article>"
    other_path = "brands" if path == "commerce" else "commerce"
    other_label = "Brands" if path == "commerce" else "Commerce"
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>{escape(title)}</title><style>
    :root{{--line:#ffffff1e;--gold:#f2c86f;--violet:#9f70ff;--muted:#c6bfd0;--good:#79dfa6}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 8% 0,#47185e,transparent 30%),radial-gradient(circle at 92% 0,#123e58,transparent 28%),#06050b;color:#fff;font-family:Inter,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.wrap{{width:min(1280px,calc(100% - 28px));margin:auto;padding:38px 0 70px}}.top{{display:flex;justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap}}.eyebrow{{color:var(--gold);font-size:.7rem;text-transform:uppercase;letter-spacing:.15em;font-weight:950}}h1{{font-size:clamp(2.8rem,7vw,5.4rem);letter-spacing:-.06em;line-height:.94;margin:.13em 0}}p,.muted,small{{color:var(--muted);line-height:1.55}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.card{{border:1px solid var(--line);border-radius:17px;padding:14px;background:#13101ceb;margin:10px 0}}.btn,.pill{{display:inline-block;border:1px solid var(--line);border-radius:9px;padding:7px 9px;background:#ffffff08;font-weight:850}}.pill{{font-size:.66rem;border-radius:999px}}.guard{{border-left:4px solid var(--violet)}}.good{{color:var(--good)}}@media(max-width:850px){{.grid{{grid-template-columns:1fr 1fr}}}}@media(max-width:600px){{.grid{{grid-template-columns:1fr}}}}
    </style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Private Commercial Growth</div><h1>{escape(title)}</h1><p>One Pulsar-Frequency House account, role-gated ESP workflow. Provider and brand actions remain human-verified.</p></div><div><a class='btn' href='/command-center/member-hub'>ESP Member Hub</a> <a class='btn' href='/command-center/{other_path}'>{other_label}</a></div></div>{profile_note}{manager_note}<section><div class='eyebrow'>Opportunities</div><div class='grid'>{rows}</div></section></main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@router.get("/command-center/commerce", response_class=HTMLResponse, include_in_schema=False)
def commerce_portal(request: Request):
    return _portal("shop", request)


@router.get("/command-center/brands", response_class=HTMLResponse, include_in_schema=False)
def brands_portal(request: Request):
    return _portal("brand", request)


__all__ = ["router", "growth", "CommercialGrowthStore"]
