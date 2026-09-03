from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .esp_command_center import EspStore, esp
from .esp_commercial_growth import BrandLeadCreate, CommercialGrowthStore
from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["ESP Brand Revenue OS"])

Segment = Literal["A", "B", "C", "partner_sponsor", "seller_commerce"]
Forecast = Literal["none", "pipeline", "best_case", "commit"]

PIPELINE_STAGES = (
    "target_identified",
    "researched",
    "contacted",
    "engaged",
    "discovery_booked",
    "qualified",
    "solution_designed",
    "proposal_sent",
    "negotiation",
    "verbal_procurement",
    "contracted",
    "in_delivery",
    "completed",
    "invoice_collection",
    "qbr_renewal",
    "closed_won",
    "closed_lost",
    "no_fit",
)
ACTIVE_STAGES = set(PIPELINE_STAGES) - {"closed_won", "closed_lost", "no_fit"}
QUALIFIED_OR_LATER = set(PIPELINE_STAGES[5:]) - {"closed_lost", "no_fit"}
FORECAST_ELIGIBLE = set(PIPELINE_STAGES[5:]) - {"closed_lost", "no_fit", "completed", "invoice_collection", "qbr_renewal", "closed_won"}
LOSS_REASONS = {
    "no_budget", "timing", "competitor", "creator_fit", "price", "rights_exclusivity",
    "procurement", "geography", "no_response", "campaign_cancelled", "risk_compliance", "capacity", "other",
}
ACTIVITY_TYPES = {"research", "outreach", "follow_up", "meeting", "proposal", "negotiation", "delivery", "invoice", "qbr", "note"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _roles(membership: dict) -> set[str]:
    if membership.get("status") == "owner":
        return {"creator", "agent", "owner"}
    role = str(membership.get("roles") or "").lower()
    return {"creator", "agent"} if role == "both" else ({role} if role else set())


def _is_owner(membership: dict) -> bool:
    return membership.get("status") == "owner" or "owner" in _roles(membership)


def _require_sales(membership: dict) -> None:
    if "agent" not in _roles(membership) and not _is_owner(membership):
        raise HTTPException(403, "ESP Agent or Owner access is required")


def _https(value: str, *, allow_empty: bool = True) -> str:
    text = (value or "").strip()
    if not text and allow_empty:
        return ""
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("CRM website/source links must use HTTPS")
    return text[:2000]


def _list(values: list[str] | None, *, limit: int = 30, item_limit: int = 160) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        value = " ".join(str(raw).split())[:item_limit]
        key = value.casefold()
        if value and key not in seen:
            result.append(value)
            seen.add(key)
        if len(result) >= limit:
            break
    return result


class Qualification(BaseModel):
    need: str = Field(default="", max_length=1000)
    authority: str = Field(default="", max_length=1000)
    budget: str = Field(default="", max_length=1000)
    timing: str = Field(default="", max_length=1000)
    fit: str = Field(default="", max_length=1000)
    risk: str = Field(default="", max_length=1000)

    def complete(self) -> bool:
        return all(bool(getattr(self, field).strip()) for field in ("need", "authority", "budget", "timing", "fit", "risk"))


class RevenueAccountCreate(BaseModel):
    brand_name: str = Field(min_length=2, max_length=180)
    company_type: str = Field(default="brand", max_length=80)
    sector: str = Field(default="", max_length=120)
    website: str = Field(default="", max_length=2000)
    source_url: str = Field(default="", max_length=2000)
    regions: list[str] = Field(default_factory=list)
    contact_people: list[str] = Field(default_factory=list)
    verified_contact_method: str = Field(default="", max_length=300)
    source: str = Field(default="manual_research", max_length=120)
    segment: Segment = "C"
    creator_niches: list[str] = Field(default_factory=list)
    opportunity_type: str = Field(default="", max_length=160)
    stage: str = Field(default="target_identified", max_length=80)
    next_action: str = Field(default="", max_length=500)
    next_action_at: str = Field(default="", max_length=80)
    expected_value: float | None = Field(default=None, ge=0, le=1_000_000_000)
    probability: float | None = Field(default=None, ge=0, le=1)
    target_close_period: str = Field(default="", max_length=80)
    risk_notes: str = Field(default="", max_length=2000)
    do_not_contact: bool = False
    qualification: Qualification = Field(default_factory=Qualification)
    forecast: Forecast = "none"
    notes: str = Field(default="", max_length=3000)


class RevenueAccountUpdate(BaseModel):
    company_type: str = Field(default="brand", max_length=80)
    sector: str = Field(default="", max_length=120)
    website: str = Field(default="", max_length=2000)
    regions: list[str] = Field(default_factory=list)
    contact_people: list[str] = Field(default_factory=list)
    verified_contact_method: str = Field(default="", max_length=300)
    source: str = Field(default="manual_research", max_length=120)
    segment: Segment = "C"
    creator_niches: list[str] = Field(default_factory=list)
    opportunity_type: str = Field(default="", max_length=160)
    stage: str = Field(default="target_identified", max_length=80)
    next_action: str = Field(default="", max_length=500)
    next_action_at: str = Field(default="", max_length=80)
    expected_value: float | None = Field(default=None, ge=0, le=1_000_000_000)
    probability: float | None = Field(default=None, ge=0, le=1)
    target_close_period: str = Field(default="", max_length=80)
    risk_notes: str = Field(default="", max_length=2000)
    do_not_contact: bool = False
    qualification: Qualification = Field(default_factory=Qualification)
    forecast: Forecast = "none"
    lost_reason: str = Field(default="", max_length=80)
    renewal_at: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=3000)


class RevenueActivityCreate(BaseModel):
    activity_type: str = Field(default="note", max_length=80)
    summary: str = Field(min_length=2, max_length=3000)
    occurred_at: str = Field(default="", max_length=80)
    external_reference: str = Field(default="", max_length=1000)


class BrandRevenueStore:
    """Full-funnel commercial sidecar over the existing ESP brand-lead records.

    It does not send outreach. It records research, qualification, governed follow-up and
    forecast evidence. Do-not-contact is a hard guard on outbound activity records.
    """

    def __init__(self, esp_store: EspStore | None = None):
        self.esp = esp_store or esp
        self.growth = CommercialGrowthStore(self.esp.db_path)
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
                CREATE TABLE IF NOT EXISTS esp_brand_revenue_accounts (
                    lead_id TEXT PRIMARY KEY,
                    company_type TEXT NOT NULL DEFAULT 'brand',
                    sector TEXT NOT NULL DEFAULT '',
                    website TEXT NOT NULL DEFAULT '',
                    regions_json TEXT NOT NULL DEFAULT '[]',
                    contact_people_json TEXT NOT NULL DEFAULT '[]',
                    verified_contact_method TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'manual_research',
                    segment TEXT NOT NULL DEFAULT 'C',
                    creator_niches_json TEXT NOT NULL DEFAULT '[]',
                    opportunity_type TEXT NOT NULL DEFAULT '',
                    canonical_stage TEXT NOT NULL DEFAULT 'target_identified',
                    last_interaction_at TEXT NOT NULL DEFAULT '',
                    next_action TEXT NOT NULL DEFAULT '',
                    next_action_at TEXT NOT NULL DEFAULT '',
                    expected_value REAL,
                    probability REAL,
                    target_close_period TEXT NOT NULL DEFAULT '',
                    risk_notes TEXT NOT NULL DEFAULT '',
                    do_not_contact INTEGER NOT NULL DEFAULT 0,
                    qualification_json TEXT NOT NULL DEFAULT '{}',
                    forecast_category TEXT NOT NULL DEFAULT 'none',
                    lost_reason TEXT NOT NULL DEFAULT '',
                    renewal_at TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    updated_by TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(lead_id) REFERENCES esp_brand_leads(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_brand_revenue_stage
                    ON esp_brand_revenue_accounts(canonical_stage,next_action_at,updated_at DESC);
                CREATE TABLE IF NOT EXISTS esp_brand_revenue_activity (
                    id TEXT PRIMARY KEY,
                    lead_id TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    activity_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    external_reference TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(lead_id) REFERENCES esp_brand_leads(id) ON DELETE CASCADE,
                    FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_brand_revenue_activity_lead
                    ON esp_brand_revenue_activity(lead_id,occurred_at DESC,created_at DESC);
                """
            )

    def _lead(self, con: sqlite3.Connection, lead_id: str):
        return con.execute("SELECT * FROM esp_brand_leads WHERE id=?", (lead_id,)).fetchone()

    @staticmethod
    def _authorize(lead, actor: str, owner: bool) -> None:
        if not lead:
            raise KeyError("Brand account not found")
        if not owner and lead["owner_user_id"] != actor:
            raise PermissionError("Brand account belongs to another ESP sales owner")

    @staticmethod
    def _validate_stage(stage: str) -> str:
        if stage not in PIPELINE_STAGES:
            raise ValueError("Unsupported commercial pipeline stage")
        return stage

    @staticmethod
    def _validate_business_rules(body: RevenueAccountUpdate | RevenueAccountCreate) -> None:
        stage = BrandRevenueStore._validate_stage(body.stage)
        if stage in ACTIVE_STAGES and not (body.next_action.strip() and body.next_action_at.strip()):
            raise ValueError("Every active commercial account requires one dated next action")
        if stage in QUALIFIED_OR_LATER and not body.qualification.complete():
            raise ValueError("Qualified and later stages require Need, Authority, Budget, Timing, Fit and Risk qualification")
        if body.forecast != "none" and stage not in FORECAST_ELIGIBLE:
            raise ValueError("Only qualified active opportunities may enter revenue forecast")
        if body.forecast == "commit" and stage not in {"contracted", "in_delivery"}:
            raise ValueError("Commit forecast requires contracted or in-delivery evidence")
        if body.probability is not None and stage not in QUALIFIED_OR_LATER:
            raise ValueError("Probability is recorded only after qualification")
        lost_reason = getattr(body, "lost_reason", "")
        if stage == "closed_lost" and lost_reason not in LOSS_REASONS:
            raise ValueError("Closed-lost accounts require a standard lost-deal reason")
        if stage != "closed_lost" and lost_reason:
            raise ValueError("Lost-deal reason is valid only for closed-lost accounts")

    def create(self, owner_user_id: str, body: RevenueAccountCreate) -> dict:
        self._validate_business_rules(body)
        website = _https(body.website) if body.website else ""
        source_url = _https(body.source_url) if body.source_url else website
        lead = self.growth.create_brand_lead(
            owner_user_id,
            BrandLeadCreate(
                brand_name=body.brand_name,
                source_url=source_url,
                contact_channel=body.verified_contact_method,
                region=(body.regions[0] if body.regions else "global"),
                niche=(body.creator_niches[0] if body.creator_niches else "all"),
                stage="qualified" if body.stage in QUALIFIED_OR_LATER else "research",
                next_followup_at=body.next_action_at,
                notes=body.notes,
            ),
        )
        self._write_details(lead["id"], body, actor=owner_user_id, creating=True)
        return self.get(lead["id"], actor=owner_user_id, owner=False)

    def _write_details(self, lead_id: str, body: RevenueAccountUpdate | RevenueAccountCreate, *, actor: str, creating: bool = False) -> None:
        self._validate_business_rules(body)
        website = _https(body.website) if body.website else ""
        now = _now()
        values = (
            body.company_type.strip()[:80],
            body.sector.strip()[:120],
            website,
            json.dumps(_list(body.regions), sort_keys=True),
            json.dumps(_list(body.contact_people), sort_keys=True),
            body.verified_contact_method.strip()[:300],
            body.source.strip()[:120],
            body.segment,
            json.dumps(_list(body.creator_niches), sort_keys=True),
            body.opportunity_type.strip()[:160],
            body.stage,
            body.next_action.strip()[:500],
            body.next_action_at.strip()[:80],
            body.expected_value,
            body.probability,
            body.target_close_period.strip()[:80],
            body.risk_notes.strip()[:2000],
            int(body.do_not_contact),
            json.dumps(body.qualification.model_dump(), sort_keys=True),
            body.forecast,
            getattr(body, "lost_reason", "")[:80],
            getattr(body, "renewal_at", "")[:80],
            body.notes.strip()[:3000],
            actor[:160],
            now,
        )
        with self._connect() as con:
            if creating:
                con.execute(
                    """INSERT INTO esp_brand_revenue_accounts
                    (lead_id,company_type,sector,website,regions_json,contact_people_json,verified_contact_method,source,segment,creator_niches_json,
                     opportunity_type,canonical_stage,next_action,next_action_at,expected_value,probability,target_close_period,risk_notes,do_not_contact,
                     qualification_json,forecast_category,lost_reason,renewal_at,notes,updated_by,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (lead_id, *values),
                )
            else:
                con.execute(
                    """UPDATE esp_brand_revenue_accounts SET company_type=?,sector=?,website=?,regions_json=?,contact_people_json=?,verified_contact_method=?,
                    source=?,segment=?,creator_niches_json=?,opportunity_type=?,canonical_stage=?,next_action=?,next_action_at=?,expected_value=?,probability=?,
                    target_close_period=?,risk_notes=?,do_not_contact=?,qualification_json=?,forecast_category=?,lost_reason=?,renewal_at=?,notes=?,updated_by=?,updated_at=?
                    WHERE lead_id=?""",
                    (*values, lead_id),
                )
            con.execute(
                "UPDATE esp_brand_leads SET stage=?,next_followup_at=?,notes=?,updated_at=? WHERE id=?",
                (
                    "won" if body.stage == "closed_won" else "lost" if body.stage in {"closed_lost", "no_fit"} else "proposal" if body.stage in {"proposal_sent", "negotiation", "verbal_procurement"} else "qualified" if body.stage in QUALIFIED_OR_LATER else "research",
                    body.next_action_at.strip()[:80],
                    body.notes.strip()[:3000],
                    now,
                    lead_id,
                ),
            )

    def update(self, lead_id: str, actor: str, body: RevenueAccountUpdate, *, owner: bool) -> dict:
        with self._connect() as con:
            lead = self._lead(con, lead_id)
            self._authorize(lead, actor, owner)
            details = con.execute("SELECT lead_id FROM esp_brand_revenue_accounts WHERE lead_id=?", (lead_id,)).fetchone()
            if not details:
                raise KeyError("Brand revenue account has not been initialized")
        self._write_details(lead_id, body, actor=actor)
        return self.get(lead_id, actor=actor, owner=owner)

    def add_activity(self, lead_id: str, actor: str, body: RevenueActivityCreate, *, owner: bool) -> dict:
        if body.activity_type not in ACTIVITY_TYPES:
            raise ValueError("Unsupported brand revenue activity type")
        with self._connect() as con:
            lead = self._lead(con, lead_id)
            self._authorize(lead, actor, owner)
            details = con.execute("SELECT * FROM esp_brand_revenue_accounts WHERE lead_id=?", (lead_id,)).fetchone()
            if not details:
                raise KeyError("Brand revenue account has not been initialized")
            if bool(details["do_not_contact"]) and body.activity_type in {"outreach", "follow_up"}:
                raise PermissionError("This account is marked do-not-contact; outbound activity is blocked")
            occurred_at = body.occurred_at.strip()[:80] or _now()
            activity_id = uuid4().hex
            con.execute(
                """INSERT INTO esp_brand_revenue_activity
                (id,lead_id,actor_user_id,activity_type,summary,occurred_at,external_reference,created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (
                    activity_id,
                    lead_id,
                    actor,
                    body.activity_type,
                    body.summary.strip()[:3000],
                    occurred_at,
                    body.external_reference.strip()[:1000],
                    _now(),
                ),
            )
            con.execute("UPDATE esp_brand_revenue_accounts SET last_interaction_at=?,updated_by=?,updated_at=? WHERE lead_id=?", (occurred_at, actor[:160], _now(), lead_id))
        return self.get(lead_id, actor=actor, owner=owner)

    def get(self, lead_id: str, *, actor: str, owner: bool) -> dict:
        with self._connect() as con:
            lead = self._lead(con, lead_id)
            self._authorize(lead, actor, owner)
            details = con.execute("SELECT * FROM esp_brand_revenue_accounts WHERE lead_id=?", (lead_id,)).fetchone()
            if not details:
                raise KeyError("Brand revenue account has not been initialized")
            activities = con.execute(
                "SELECT * FROM esp_brand_revenue_activity WHERE lead_id=? ORDER BY occurred_at DESC,created_at DESC",
                (lead_id,),
            ).fetchall()
        item = {"lead": dict(lead), "crm": dict(details), "activities": [dict(row) for row in activities]}
        for key in ("regions_json", "contact_people_json", "creator_niches_json", "qualification_json"):
            try:
                decoded = json.loads(item["crm"].pop(key) or ("{}" if key == "qualification_json" else "[]"))
            except Exception:
                decoded = {} if key == "qualification_json" else []
            item["crm"][key.removesuffix("_json")] = decoded
        item["crm"]["do_not_contact"] = bool(item["crm"]["do_not_contact"])
        return item

    def list(self, actor: str, *, owner: bool) -> list[dict]:
        with self._connect() as con:
            if owner:
                rows = con.execute("SELECT lead_id FROM esp_brand_revenue_accounts ORDER BY next_action_at,updated_at DESC").fetchall()
            else:
                rows = con.execute(
                    """SELECT d.lead_id FROM esp_brand_revenue_accounts d JOIN esp_brand_leads l ON l.id=d.lead_id
                    WHERE l.owner_user_id=? ORDER BY d.next_action_at,d.updated_at DESC""",
                    (actor,),
                ).fetchall()
        return [self.get(row["lead_id"], actor=actor, owner=owner) for row in rows]

    def metrics(self, actor: str, *, owner: bool) -> dict:
        rows = self.list(actor, owner=owner)
        by_stage: dict[str, int] = {stage: 0 for stage in PIPELINE_STAGES}
        forecast = {"pipeline": 0.0, "best_case": 0.0, "commit": 0.0}
        expected_total = 0.0
        qualified = 0
        wins = 0
        losses = 0
        dnc = 0
        for row in rows:
            crm = row["crm"]
            by_stage[crm["canonical_stage"]] += 1
            if crm["canonical_stage"] in QUALIFIED_OR_LATER:
                qualified += 1
            if crm["canonical_stage"] == "closed_won":
                wins += 1
            if crm["canonical_stage"] in {"closed_lost", "no_fit"}:
                losses += 1
            if crm["do_not_contact"]:
                dnc += 1
            value = float(crm["expected_value"] or 0)
            expected_total += value
            if crm["forecast_category"] in forecast:
                forecast[crm["forecast_category"]] += value
        decided = wins + losses
        return {
            "accounts": len(rows),
            "by_stage": by_stage,
            "qualified_accounts": qualified,
            "expected_value": round(expected_total, 2),
            "forecast_value": {key: round(value, 2) for key, value in forecast.items()},
            "wins": wins,
            "losses_or_no_fit": losses,
            "win_rate_on_decided": round(wins / decided, 4) if decided else None,
            "do_not_contact_accounts": dnc,
            "unqualified_outreach_counted_as_forecast": False,
            "automated_outreach_enabled": False,
        }


brand_revenue = BrandRevenueStore()


@router.get("/command-center/api/brand-revenue/accounts")
def brand_revenue_accounts(request: Request):
    member, membership = require_esp_hub_member(request)
    _require_sales(membership)
    owner = _is_owner(membership)
    return {"accounts": brand_revenue.list(member.user_id, owner=owner), "metrics": brand_revenue.metrics(member.user_id, owner=owner), "automated_outreach_enabled": False}


@router.post("/command-center/api/brand-revenue/accounts")
def create_brand_revenue_account(body: RevenueAccountCreate, request: Request):
    member, membership = require_esp_hub_member(request)
    _require_sales(membership)
    try:
        return {"account": brand_revenue.create(member.user_id, body)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/command-center/api/brand-revenue/accounts/{lead_id}")
def brand_revenue_account(lead_id: str, request: Request):
    member, membership = require_esp_hub_member(request)
    _require_sales(membership)
    try:
        return {"account": brand_revenue.get(lead_id, actor=member.user_id, owner=_is_owner(membership))}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.put("/command-center/api/brand-revenue/accounts/{lead_id}")
def update_brand_revenue_account(lead_id: str, body: RevenueAccountUpdate, request: Request):
    member, membership = require_esp_hub_member(request)
    _require_sales(membership)
    try:
        return {"account": brand_revenue.update(lead_id, member.user_id, body, owner=_is_owner(membership))}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/command-center/api/brand-revenue/accounts/{lead_id}/activity")
def add_brand_revenue_activity(lead_id: str, body: RevenueActivityCreate, request: Request):
    member, membership = require_esp_hub_member(request)
    _require_sales(membership)
    try:
        return {"account": brand_revenue.add_activity(lead_id, member.user_id, body, owner=_is_owner(membership))}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


__all__ = ["router", "brand_revenue", "BrandRevenueStore", "PIPELINE_STAGES", "LOSS_REASONS"]
