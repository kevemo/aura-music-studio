from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from html import escape
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_command_center import esp
from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["ESP Brand and Commercial Opportunities"])

CampaignStatus = Literal["draft", "applications_open", "applications_closed", "active", "completed", "cancelled"]
ApplicationStatus = Literal["submitted", "agent_review", "shortlisted", "approved", "rejected", "withdrawn"]
DeliverableStatus = Literal["assigned", "submitted", "revision_requested", "esp_reviewed", "published"]
PaymentStatus = Literal["not_applicable", "pending", "invoiced", "due", "paid", "overdue"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _role(membership: dict) -> str:
    return "owner" if membership.get("status") == "owner" else (membership.get("roles") or "").strip().lower()


def _clean(value: str, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


def _evidence_ref(value: str) -> str:
    clean = (value or "").strip()[:2000]
    if not clean:
        return ""
    if clean.startswith("artifact://"):
        tail = clean.removeprefix("artifact://")
        if not tail or ".." in tail or "\\" in tail:
            raise ValueError("Invalid Creative Library artifact reference")
        return clean
    parsed = urlparse(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Evidence must be an https/http URL or artifact:// reference")
    return clean


def _json_list(values: list[str] | None, *, limit: int = 50, item_limit: int = 240) -> str:
    cleaned: list[str] = []
    for value in values or []:
        item = _clean(str(value), item_limit)
        if item and item not in cleaned:
            cleaned.append(item)
        if len(cleaned) >= limit:
            break
    return json.dumps(cleaned, ensure_ascii=False)


def _decode(row: sqlite3.Row | dict | None) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    object_keys = {"metrics_json", "metadata_json"}
    for key in (
        "niches_json", "regions_json", "platforms_json", "deliverables_json",
        "requirements_json", "portfolio_refs_json", "metrics_json", "metadata_json",
    ):
        if key not in item:
            continue
        fallback = "{}" if key in object_keys else "[]"
        raw = item.pop(key) or fallback
        try:
            item[key.removesuffix("_json")] = json.loads(raw)
        except Exception:
            item[key.removesuffix("_json")] = {} if key in object_keys else []
    return item


class CampaignCreate(BaseModel):
    brand_name: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=3, max_length=240)
    brief: str = Field(min_length=10, max_length=12000)
    niches: list[str] = Field(default_factory=list, max_length=30)
    regions: list[str] = Field(default_factory=list, max_length=40)
    platforms: list[str] = Field(default_factory=lambda: ["TikTok"], max_length=20)
    deliverables: list[str] = Field(default_factory=list, max_length=50)
    requirements: list[str] = Field(default_factory=list, max_length=50)
    compensation_summary: str = Field(default="", max_length=1200)
    currency: str = Field(default="GBP", max_length=8)
    usage_terms: str = Field(default="", max_length=5000)
    exclusivity_terms: str = Field(default="", max_length=3000)
    disclosure_requirements: str = Field(default="", max_length=3000)
    application_deadline: str | None = Field(default=None, max_length=80)
    campaign_start: str | None = Field(default=None, max_length=80)
    campaign_end: str | None = Field(default=None, max_length=80)
    status: CampaignStatus = "draft"


class CampaignStatusUpdate(BaseModel):
    status: CampaignStatus


class ApplicationCreate(BaseModel):
    creator_user_id: str | None = Field(default=None, max_length=128)
    concept: str = Field(min_length=3, max_length=6000)
    rate_quote: str = Field(default="", max_length=800)
    portfolio_refs: list[str] = Field(default_factory=list, max_length=20)
    creator_opt_in_confirmed: bool = False
    eligibility_attested: bool = True


class ApplicationStatusUpdate(BaseModel):
    status: ApplicationStatus
    decision_note: str = Field(default="", max_length=3000)


class DeliverableCreate(BaseModel):
    label: str = Field(min_length=1, max_length=240)
    due_at: str | None = Field(default=None, max_length=80)


class DeliverableUpdate(BaseModel):
    status: DeliverableStatus | None = None
    submission_ref: str = Field(default="", max_length=2000)
    review_note: str = Field(default="", max_length=3000)
    published_url: str = Field(default="", max_length=2000)
    metrics: dict = Field(default_factory=dict)


class PaymentUpdate(BaseModel):
    status: PaymentStatus
    amount_minor: int | None = Field(default=None, ge=0, le=2_000_000_000)
    currency: str = Field(default="GBP", max_length=8)
    due_at: str | None = Field(default=None, max_length=80)
    invoice_ref: str = Field(default="", max_length=240)
    note: str = Field(default="", max_length=2000)


class CommercialOpportunityStore:
    """Auditable ESP brand/campaign workflow; never a payment processor or contract executor."""

    def __init__(self, db_path: str | None = None):
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
                CREATE TABLE IF NOT EXISTS esp_brand_campaigns (
                    id TEXT PRIMARY KEY, created_by TEXT NOT NULL, brand_name TEXT NOT NULL,
                    title TEXT NOT NULL, brief TEXT NOT NULL, niches_json TEXT NOT NULL DEFAULT '[]',
                    regions_json TEXT NOT NULL DEFAULT '[]', platforms_json TEXT NOT NULL DEFAULT '[]',
                    deliverables_json TEXT NOT NULL DEFAULT '[]', requirements_json TEXT NOT NULL DEFAULT '[]',
                    compensation_summary TEXT NOT NULL DEFAULT '', currency TEXT NOT NULL DEFAULT 'GBP',
                    usage_terms TEXT NOT NULL DEFAULT '', exclusivity_terms TEXT NOT NULL DEFAULT '',
                    disclosure_requirements TEXT NOT NULL DEFAULT '', application_deadline TEXT,
                    campaign_start TEXT, campaign_end TEXT, status TEXT NOT NULL DEFAULT 'draft',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_brand_campaign_status ON esp_brand_campaigns(status,updated_at DESC);
                CREATE TABLE IF NOT EXISTS esp_brand_applications (
                    id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, creator_user_id TEXT NOT NULL,
                    agent_user_id TEXT, concept TEXT NOT NULL, rate_quote TEXT NOT NULL DEFAULT '',
                    portfolio_refs_json TEXT NOT NULL DEFAULT '[]', creator_opt_in_confirmed INTEGER NOT NULL DEFAULT 0,
                    eligibility_attested INTEGER NOT NULL DEFAULT 1, eligibility_snapshot TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'submitted', decision_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(campaign_id,creator_user_id),
                    FOREIGN KEY(campaign_id) REFERENCES esp_brand_campaigns(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_brand_app_creator ON esp_brand_applications(creator_user_id,status,updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_brand_app_agent ON esp_brand_applications(agent_user_id,status,updated_at DESC);
                CREATE TABLE IF NOT EXISTS esp_brand_deliverables (
                    id TEXT PRIMARY KEY, application_id TEXT NOT NULL, label TEXT NOT NULL, due_at TEXT,
                    status TEXT NOT NULL DEFAULT 'assigned', submission_ref TEXT NOT NULL DEFAULT '',
                    review_note TEXT NOT NULL DEFAULT '', published_url TEXT NOT NULL DEFAULT '',
                    metrics_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(application_id) REFERENCES esp_brand_applications(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_brand_deliverable_app ON esp_brand_deliverables(application_id,status,due_at);
                CREATE TABLE IF NOT EXISTS esp_brand_payments (
                    application_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending', amount_minor INTEGER,
                    currency TEXT NOT NULL DEFAULT 'GBP', due_at TEXT, invoice_ref TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '', paid_at TEXT, updated_at TEXT NOT NULL,
                    FOREIGN KEY(application_id) REFERENCES esp_brand_applications(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS esp_brand_activity (
                    id TEXT PRIMARY KEY, campaign_id TEXT, application_id TEXT, actor_user_id TEXT NOT NULL,
                    action TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
                    FOREIGN KEY(campaign_id) REFERENCES esp_brand_campaigns(id) ON DELETE CASCADE,
                    FOREIGN KEY(application_id) REFERENCES esp_brand_applications(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_brand_activity_campaign ON esp_brand_activity(campaign_id,created_at);
                CREATE INDEX IF NOT EXISTS idx_brand_activity_application ON esp_brand_activity(application_id,created_at);
                """
            )

    def _activity(self, con: sqlite3.Connection, *, actor: str, action: str, campaign_id: str | None = None, application_id: str | None = None, metadata: dict | None = None) -> None:
        con.execute(
            "INSERT INTO esp_brand_activity(id,campaign_id,application_id,actor_user_id,action,metadata_json,created_at) VALUES (?,?,?,?,?,?,?)",
            (uuid4().hex, campaign_id, application_id, actor[:128], action[:120], json.dumps(metadata or {}, sort_keys=True), _now()),
        )

    def _assigned(self, con: sqlite3.Connection, agent_user_id: str, creator_user_id: str) -> bool:
        try:
            row = con.execute(
                "SELECT 1 FROM esp_agent_creator_assignments WHERE agent_user_id=? AND creator_user_id=? AND status='active'",
                (agent_user_id, creator_user_id),
            ).fetchone()
        except sqlite3.OperationalError:
            return False
        return row is not None

    def creator_context(self, creator_user_id: str) -> dict:
        with self._connect() as con:
            try:
                membership = con.execute("SELECT tiktok_handle,region,status,roles FROM esp_memberships WHERE user_id=?", (creator_user_id,)).fetchone()
            except sqlite3.OperationalError:
                membership = None
            try:
                niche = con.execute("SELECT niche,sub_niche FROM esp_niche_profiles WHERE user_id=?", (creator_user_id,)).fetchone()
            except sqlite3.OperationalError:
                niche = None
        return {
            "creator_user_id": creator_user_id,
            "tiktok_handle": membership["tiktok_handle"] if membership else "",
            "region": (membership["region"] if membership else "") or "",
            "esp_status": membership["status"] if membership else "",
            "esp_role": membership["roles"] if membership else "",
            "niche": niche["niche"] if niche else "",
            "sub_niche": niche["sub_niche"] if niche else "",
        }

    def create_campaign(self, actor: str, body: CampaignCreate) -> dict:
        now = _now()
        row = {
            "id": uuid4().hex, "created_by": actor, "brand_name": _clean(body.brand_name, 160),
            "title": _clean(body.title, 240), "brief": body.brief.strip()[:12000],
            "niches_json": _json_list(body.niches, limit=30, item_limit=100),
            "regions_json": _json_list(body.regions, limit=40, item_limit=100),
            "platforms_json": _json_list(body.platforms, limit=20, item_limit=80),
            "deliverables_json": _json_list(body.deliverables, limit=50),
            "requirements_json": _json_list(body.requirements, limit=50),
            "compensation_summary": body.compensation_summary.strip()[:1200],
            "currency": _clean(body.currency.upper(), 8) or "GBP", "usage_terms": body.usage_terms.strip()[:5000],
            "exclusivity_terms": body.exclusivity_terms.strip()[:3000],
            "disclosure_requirements": body.disclosure_requirements.strip()[:3000],
            "application_deadline": body.application_deadline, "campaign_start": body.campaign_start,
            "campaign_end": body.campaign_end, "status": body.status, "created_at": now, "updated_at": now,
        }
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_brand_campaigns(id,created_by,brand_name,title,brief,niches_json,regions_json,platforms_json,deliverables_json,requirements_json,compensation_summary,currency,usage_terms,exclusivity_terms,disclosure_requirements,application_deadline,campaign_start,campaign_end,status,created_at,updated_at)
                VALUES (:id,:created_by,:brand_name,:title,:brief,:niches_json,:regions_json,:platforms_json,:deliverables_json,:requirements_json,:compensation_summary,:currency,:usage_terms,:exclusivity_terms,:disclosure_requirements,:application_deadline,:campaign_start,:campaign_end,:status,:created_at,:updated_at)""",
                row,
            )
            self._activity(con, actor=actor, action="campaign_created", campaign_id=row["id"], metadata={"status": body.status})
        return self.campaign(row["id"])

    def campaign(self, campaign_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_brand_campaigns WHERE id=?", (campaign_id,)).fetchone()
        if not row:
            raise KeyError(campaign_id)
        return _decode(row) or {}

    def set_campaign_status(self, campaign_id: str, status: str, *, actor: str) -> dict:
        with self._connect() as con:
            if not con.execute("SELECT 1 FROM esp_brand_campaigns WHERE id=?", (campaign_id,)).fetchone():
                raise KeyError(campaign_id)
            con.execute("UPDATE esp_brand_campaigns SET status=?,updated_at=? WHERE id=?", (status, _now(), campaign_id))
            self._activity(con, actor=actor, action="campaign_status_changed", campaign_id=campaign_id, metadata={"status": status})
        return self.campaign(campaign_id)

    def eligibility(self, campaign: dict, creator_user_id: str) -> dict:
        context = self.creator_context(creator_user_id)
        reasons: list[str] = []
        if context["esp_status"] not in {"active", "owner"}:
            reasons.append("Creator does not have active ESP membership")
        niches = {str(v).strip().lower() for v in campaign.get("niches", []) if str(v).strip()}
        regions = {str(v).strip().lower() for v in campaign.get("regions", []) if str(v).strip()}
        if niches and context["niche"].lower() not in niches:
            reasons.append("Creator niche does not match this opportunity")
        if regions and context["region"].lower() not in regions:
            reasons.append("Creator region does not match this opportunity")
        if campaign.get("status") != "applications_open":
            reasons.append("Applications are not currently open")
        return {"eligible": not reasons, "reasons": reasons, "context": context}

    def list_campaigns(self, *, creator_user_id: str | None = None, owner: bool = False) -> list[dict]:
        with self._connect() as con:
            sql = "SELECT * FROM esp_brand_campaigns ORDER BY updated_at DESC" if owner else "SELECT * FROM esp_brand_campaigns WHERE status IN ('applications_open','active') ORDER BY updated_at DESC"
            rows = con.execute(sql).fetchall()
        result = [_decode(row) or {} for row in rows]
        if creator_user_id:
            for item in result:
                item["eligibility"] = self.eligibility(item, creator_user_id)
        return result

    def apply(self, campaign_id: str, *, actor_user_id: str, actor_role: str, body: ApplicationCreate) -> dict:
        creator_user_id = body.creator_user_id or actor_user_id
        delegated = creator_user_id != actor_user_id
        if actor_role == "creator" and delegated:
            raise PermissionError("Creators may submit only their own opportunity applications")
        if delegated and actor_role == "owner" and not body.creator_opt_in_confirmed:
            raise PermissionError("Owner-assisted application requires recorded creator opt-in")
        with self._connect() as con:
            if delegated and actor_role in {"agent", "both"}:
                if not body.creator_opt_in_confirmed:
                    raise PermissionError("Agent submission requires recorded creator opt-in")
                if not self._assigned(con, actor_user_id, creator_user_id):
                    raise PermissionError("Creator is not actively assigned to this agent")
            if delegated and actor_role not in {"agent", "both", "owner"}:
                raise PermissionError("This ESP role cannot submit for another creator")
            campaign_row = con.execute("SELECT * FROM esp_brand_campaigns WHERE id=?", (campaign_id,)).fetchone()
        if not campaign_row:
            raise KeyError(campaign_id)
        campaign = _decode(campaign_row) or {}
        check = self.eligibility(campaign, creator_user_id)
        if not check["eligible"]:
            raise ValueError("; ".join(check["reasons"]))
        if not body.eligibility_attested:
            raise ValueError("Creator eligibility attestation is required")
        now, app_id = _now(), uuid4().hex
        acting_agent = delegated and actor_role in {"agent", "both"}
        with self._connect() as con:
            try:
                con.execute(
                    """INSERT INTO esp_brand_applications(id,campaign_id,creator_user_id,agent_user_id,concept,rate_quote,portfolio_refs_json,creator_opt_in_confirmed,eligibility_attested,eligibility_snapshot,status,decision_note,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,'submitted','',?,?)""",
                    (app_id, campaign_id, creator_user_id, actor_user_id if acting_agent else None,
                     body.concept.strip()[:6000], body.rate_quote.strip()[:800],
                     _json_list(body.portfolio_refs, limit=20, item_limit=2000),
                     1 if body.creator_opt_in_confirmed or not delegated else 0, 1,
                     json.dumps(check["context"], sort_keys=True), now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("This creator already has an application for this campaign") from exc
            self._activity(con, actor=actor_user_id, action="application_submitted", campaign_id=campaign_id,
                           application_id=app_id, metadata={"creator_user_id": creator_user_id, "delegated": delegated})
        return self.application(app_id, requester_user_id=actor_user_id if actor_role != "owner" else creator_user_id,
                                requester_role=actor_role if actor_role != "owner" else "creator")

    def _app_row(self, con: sqlite3.Connection, application_id: str):
        return con.execute("SELECT * FROM esp_brand_applications WHERE id=?", (application_id,)).fetchone()

    def _authorize_app(self, con: sqlite3.Connection, row: sqlite3.Row | None, *, requester_user_id: str, requester_role: str) -> None:
        if row is None:
            raise KeyError("Application not found")
        if requester_role == "owner" or row["creator_user_id"] == requester_user_id:
            return
        if requester_role in {"agent", "both"} and self._assigned(con, requester_user_id, row["creator_user_id"]):
            return
        raise PermissionError("Application is outside this ESP role boundary")

    def application(self, application_id: str, *, requester_user_id: str, requester_role: str) -> dict:
        with self._connect() as con:
            row = self._app_row(con, application_id)
            self._authorize_app(con, row, requester_user_id=requester_user_id, requester_role=requester_role)
            deliverables = con.execute("SELECT * FROM esp_brand_deliverables WHERE application_id=? ORDER BY due_at,created_at", (application_id,)).fetchall()
            payment = con.execute("SELECT * FROM esp_brand_payments WHERE application_id=?", (application_id,)).fetchone()
            campaign = con.execute("SELECT brand_name,title,status FROM esp_brand_campaigns WHERE id=?", (row["campaign_id"],)).fetchone()
            activity = con.execute("SELECT * FROM esp_brand_activity WHERE application_id=? ORDER BY created_at", (application_id,)).fetchall()
        item = _decode(row) or {}
        try:
            item["eligibility_snapshot"] = json.loads(item.get("eligibility_snapshot") or "{}")
        except Exception:
            item["eligibility_snapshot"] = {}
        item["campaign"] = dict(campaign) if campaign else {}
        item["deliverables"] = [_decode(value) or {} for value in deliverables]
        item["payment"] = dict(payment) if payment else None
        item["activity"] = [_decode(value) or {} for value in activity]
        return item

    def list_applications(self, *, requester_user_id: str, requester_role: str) -> list[dict]:
        with self._connect() as con:
            if requester_role == "owner":
                rows = con.execute("SELECT id FROM esp_brand_applications ORDER BY updated_at DESC").fetchall()
            elif requester_role == "agent":
                rows = con.execute(
                    """SELECT DISTINCT a.id FROM esp_brand_applications a JOIN esp_agent_creator_assignments x ON x.creator_user_id=a.creator_user_id
                    WHERE x.agent_user_id=? AND x.status='active' ORDER BY a.updated_at DESC""", (requester_user_id,)).fetchall()
            elif requester_role == "both":
                rows = con.execute(
                    """SELECT DISTINCT a.id FROM esp_brand_applications a LEFT JOIN esp_agent_creator_assignments x ON x.creator_user_id=a.creator_user_id AND x.status='active'
                    WHERE a.creator_user_id=? OR x.agent_user_id=? ORDER BY a.updated_at DESC""", (requester_user_id, requester_user_id)).fetchall()
            else:
                rows = con.execute("SELECT id FROM esp_brand_applications WHERE creator_user_id=? ORDER BY updated_at DESC", (requester_user_id,)).fetchall()
        return [self.application(row["id"], requester_user_id=requester_user_id, requester_role=requester_role) for row in rows]

    def set_application_status(self, application_id: str, *, requester_user_id: str, requester_role: str, status: str, note: str = "") -> dict:
        with self._connect() as con:
            row = self._app_row(con, application_id)
            self._authorize_app(con, row, requester_user_id=requester_user_id, requester_role=requester_role)
            creator_self = row["creator_user_id"] == requester_user_id
            if requester_role == "owner":
                pass
            elif creator_self:
                if status != "withdrawn":
                    raise PermissionError("Creators may only withdraw their own application status")
            elif requester_role in {"agent", "both"} and status not in {"agent_review", "shortlisted"}:
                raise PermissionError("Agents may review or shortlist; final approve/reject is owner-controlled")
            con.execute("UPDATE esp_brand_applications SET status=?,decision_note=?,updated_at=? WHERE id=?", (status, note.strip()[:3000], _now(), application_id))
            self._activity(con, actor=requester_user_id, action="application_status_changed", campaign_id=row["campaign_id"], application_id=application_id, metadata={"status": status})
        return self.application(application_id, requester_user_id=requester_user_id, requester_role=requester_role)

    def add_deliverable(self, application_id: str, *, actor: str, label: str, due_at: str | None = None) -> dict:
        clean_label = _clean(label, 240)
        if not clean_label:
            raise ValueError("Deliverable label is required")
        now, deliverable_id = _now(), uuid4().hex
        with self._connect() as con:
            row = self._app_row(con, application_id)
            if not row:
                raise KeyError(application_id)
            con.execute("INSERT INTO esp_brand_deliverables(id,application_id,label,due_at,status,created_at,updated_at) VALUES (?,?,?,?,'assigned',?,?)", (deliverable_id, application_id, clean_label, due_at, now, now))
            self._activity(con, actor=actor, action="deliverable_added", campaign_id=row["campaign_id"], application_id=application_id, metadata={"deliverable_id": deliverable_id})
        return {"id": deliverable_id, "application_id": application_id, "label": clean_label, "due_at": due_at, "status": "assigned"}

    def update_deliverable(self, deliverable_id: str, *, requester_user_id: str, requester_role: str, body: DeliverableUpdate) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT d.*,a.creator_user_id,a.campaign_id FROM esp_brand_deliverables d JOIN esp_brand_applications a ON a.id=d.application_id WHERE d.id=?", (deliverable_id,)).fetchone()
            if not row:
                raise KeyError(deliverable_id)
            self._authorize_app(con, self._app_row(con, row["application_id"]), requester_user_id=requester_user_id, requester_role=requester_role)
            creator_self = row["creator_user_id"] == requester_user_id
            next_status = body.status or row["status"]
            if creator_self and requester_role != "owner" and next_status not in {"assigned", "submitted", "published"}:
                raise PermissionError("Creators submit/publish evidence; ESP reviewers control review states")
            if requester_role in {"agent", "both"} and not creator_self and next_status not in {"submitted", "revision_requested", "esp_reviewed", "published"}:
                raise PermissionError("Agent deliverable review state is not permitted")
            submission_ref = _evidence_ref(body.submission_ref) if body.submission_ref else row["submission_ref"]
            published_url = _evidence_ref(body.published_url) if body.published_url else row["published_url"]
            metrics = body.metrics if body.metrics else json.loads(row["metrics_json"] or "{}")
            con.execute(
                "UPDATE esp_brand_deliverables SET status=?,submission_ref=?,review_note=?,published_url=?,metrics_json=?,updated_at=? WHERE id=?",
                (next_status, submission_ref, body.review_note.strip()[:3000] if body.review_note else row["review_note"], published_url,
                 json.dumps(metrics, sort_keys=True)[:12000], _now(), deliverable_id),
            )
            self._activity(con, actor=requester_user_id, action="deliverable_updated", campaign_id=row["campaign_id"], application_id=row["application_id"], metadata={"deliverable_id": deliverable_id, "status": next_status})
            updated = con.execute("SELECT * FROM esp_brand_deliverables WHERE id=?", (deliverable_id,)).fetchone()
        return _decode(updated) or {}

    def set_payment(self, application_id: str, *, actor: str, body: PaymentUpdate) -> dict:
        now, paid_at = _now(), None
        if body.status == "paid":
            paid_at = now
        with self._connect() as con:
            row = self._app_row(con, application_id)
            if not row:
                raise KeyError(application_id)
            con.execute(
                """INSERT INTO esp_brand_payments(application_id,status,amount_minor,currency,due_at,invoice_ref,note,paid_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(application_id) DO UPDATE SET status=excluded.status,
                amount_minor=excluded.amount_minor,currency=excluded.currency,due_at=excluded.due_at,
                invoice_ref=excluded.invoice_ref,note=excluded.note,paid_at=excluded.paid_at,updated_at=excluded.updated_at""",
                (application_id, body.status, body.amount_minor, _clean(body.currency.upper(), 8) or "GBP", body.due_at,
                 _clean(body.invoice_ref, 240), body.note.strip()[:2000], paid_at, now),
            )
            self._activity(con, actor=actor, action="payment_state_changed", campaign_id=row["campaign_id"], application_id=application_id, metadata={"status": body.status, "workflow_only": True})
            payment = con.execute("SELECT * FROM esp_brand_payments WHERE application_id=?", (application_id,)).fetchone()
        return dict(payment) if payment else {}


commercial = CommercialOpportunityStore()


def _access(request: Request):
    member, membership = require_esp_hub_member(request)
    role = _role(membership)
    if role not in {"creator", "agent", "both", "owner"}:
        raise HTTPException(403, "ESP Creator, Agent or Owner access is required")
    return member, membership, role


@router.get("/command-center/api/commercial/campaigns")
def campaigns_api(request: Request):
    member, _membership, role = _access(request)
    creator_id = member.user_id if role in {"creator", "both"} else None
    return {"campaigns": commercial.list_campaigns(creator_user_id=creator_id, owner=role == "owner"), "payment_boundary": "tracking_only_no_money_transfer"}


@router.post("/command-center/api/commercial/campaigns")
def create_campaign_api(body: CampaignCreate, request: Request):
    member, _membership, role = _access(request)
    if role != "owner":
        raise HTTPException(403, "Only ESP ownership can create network commercial opportunities")
    return {"campaign": commercial.create_campaign(member.user_id, body)}


@router.patch("/command-center/api/commercial/campaigns/{campaign_id}/status")
def campaign_status_api(campaign_id: str, body: CampaignStatusUpdate, request: Request):
    member, _membership, role = _access(request)
    if role != "owner":
        raise HTTPException(403, "Only ESP ownership can change campaign lifecycle status")
    try:
        return {"campaign": commercial.set_campaign_status(campaign_id, body.status, actor=member.user_id)}
    except KeyError as exc:
        raise HTTPException(404, "Campaign not found") from exc


@router.post("/command-center/api/commercial/campaigns/{campaign_id}/applications")
def apply_api(campaign_id: str, body: ApplicationCreate, request: Request):
    member, _membership, role = _access(request)
    if role == "owner" and not body.creator_user_id:
        raise HTTPException(400, "Owner-assisted application requires creator_user_id")
    try:
        return {"application": commercial.apply(campaign_id, actor_user_id=member.user_id, actor_role=role, body=body)}
    except KeyError as exc:
        raise HTTPException(404, "Campaign not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/command-center/api/commercial/applications")
def applications_api(request: Request):
    member, _membership, role = _access(request)
    return {"applications": commercial.list_applications(requester_user_id=member.user_id, requester_role=role)}


@router.patch("/command-center/api/commercial/applications/{application_id}/status")
def application_status_api(application_id: str, body: ApplicationStatusUpdate, request: Request):
    member, _membership, role = _access(request)
    try:
        return {"application": commercial.set_application_status(application_id, requester_user_id=member.user_id, requester_role=role, status=body.status, note=body.decision_note)}
    except KeyError as exc:
        raise HTTPException(404, "Application not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/command-center/api/commercial/applications/{application_id}/deliverables")
def deliverable_create_api(application_id: str, body: DeliverableCreate, request: Request):
    member, _membership, role = _access(request)
    if role != "owner":
        raise HTTPException(403, "Only ESP ownership can assign contractual campaign deliverables")
    try:
        return {"deliverable": commercial.add_deliverable(application_id, actor=member.user_id, label=body.label, due_at=body.due_at)}
    except KeyError as exc:
        raise HTTPException(404, "Application not found") from exc


@router.patch("/command-center/api/commercial/deliverables/{deliverable_id}")
def deliverable_update_api(deliverable_id: str, body: DeliverableUpdate, request: Request):
    member, _membership, role = _access(request)
    try:
        return {"deliverable": commercial.update_deliverable(deliverable_id, requester_user_id=member.user_id, requester_role=role, body=body)}
    except KeyError as exc:
        raise HTTPException(404, "Deliverable not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/command-center/api/commercial/applications/{application_id}/payment")
def payment_update_api(application_id: str, body: PaymentUpdate, request: Request):
    member, _membership, role = _access(request)
    if role != "owner":
        raise HTTPException(403, "Only ESP ownership can update commercial payment records")
    try:
        return {"payment": commercial.set_payment(application_id, actor=member.user_id, body=body), "money_transferred": False}
    except KeyError as exc:
        raise HTTPException(404, "Application not found") from exc


CSS = r"""
:root{--line:#ffffff1d;--muted:#c8c3d5;--gold:#efc86f;--violet:#a56cff;--green:#73dda6}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 85% 0,#4b175f,transparent 32%),#06050c;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1320px,calc(100% - 28px));margin:auto;padding:34px 0 70px}a{color:inherit;text-decoration:none}.top,.row{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.eyebrow{font-size:.72rem;letter-spacing:.15em;text-transform:uppercase;color:var(--gold);font-weight:950}h1{font-size:clamp(2.7rem,6vw,5.6rem);line-height:.92;letter-spacing:-.055em;margin:.15em 0}.lead,.muted{color:var(--muted);line-height:1.6}.btn{border:1px solid var(--line);border-radius:11px;padding:9px 12px;background:#ffffff09;color:#fff;font-weight:850;cursor:pointer}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.card{border:1px solid var(--line);border-radius:18px;background:#14101dea;padding:16px;margin:10px 0}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:5px 8px;font-size:.75rem;margin:2px}.good{color:var(--green)}.bad{color:#ff9caf}.notice{display:none;border:1px solid var(--line);border-radius:10px;padding:10px}.notice.show{display:block}@media(max-width:820px){.grid{grid-template-columns:1fr}}
"""

SCRIPT = r"""
const API='/command-center/api/commercial',$=id=>document.getElementById(id);let campaigns=[],apps=[];function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}async function req(url){const r=await fetch(url,{credentials:'same-origin'});let d={};try{d=await r.json()}catch(_){}if(!r.ok)throw new Error(d.detail||`Request failed (${r.status})`);return d}function note(m){const n=$('notice');n.textContent=m;n.className='notice show'}function draw(){$('campaigns').innerHTML=campaigns.length?campaigns.map(c=>`<article class="card"><div class="row"><div><div class="eyebrow">${esc(c.brand_name)}</div><h2>${esc(c.title)}</h2></div><span class="pill">${esc(c.status)}</span></div><p class="muted">${esc(c.brief)}</p><div>${(c.platforms||[]).map(x=>`<span class="pill">${esc(x)}</span>`).join('')}</div><p class="muted"><b>Compensation:</b> ${esc(c.compensation_summary||'See campaign brief')}<br><b>Usage:</b> ${esc(c.usage_terms||'To be confirmed before approval')}<br><b>Disclosure:</b> ${esc(c.disclosure_requirements||'Follow applicable branded-content disclosure rules')}</p>${c.eligibility?`<p class="${c.eligibility.eligible?'good':'bad'}">${c.eligibility.eligible?'Eligible to apply':esc(c.eligibility.reasons.join(' · '))}</p>`:''}</article>`).join(''):'<div class="card muted">No active commercial opportunities currently available.</div>';$('apps').innerHTML=apps.length?apps.map(a=>`<article class="card"><div class="row"><div><b>${esc(a.campaign?.brand_name||'Campaign')}</b><div>${esc(a.campaign?.title||'')}</div></div><span class="pill">${esc(a.status)}</span></div><p class="muted">${esc(a.concept)}</p><div>${(a.deliverables||[]).map(d=>`<span class="pill">${esc(d.label)} · ${esc(d.status)}</span>`).join('')}</div><p class="muted">Payment workflow: ${esc(a.payment?.status||'not recorded')} — tracking only; no money transfer occurs here.</p></article>`).join(''):'<div class="card muted">No applications visible to this ESP role yet.</div>'}async function load(){try{[campaigns,apps]=[(await req(API+'/campaigns')).campaigns||[],(await req(API+'/applications')).applications||[]];draw()}catch(e){note(e.message)}}load();
"""


@router.get("/command-center/commercial-opportunities", response_class=HTMLResponse, include_in_schema=False)
def commercial_portal(request: Request):
    _member, _membership, role = _access(request)
    return HTMLResponse(f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Brand & Commercial Opportunities</title><style>{CSS}</style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Commercial Growth</div><h1>Brand & Commercial Opportunities</h1><p class='lead'>Private ESP-native campaign workflow for creator opportunities, briefs, eligibility, applications, deliverables, usage rights, reviews and payment-state tracking. Role: {escape(role)}.</p></div><a class='btn' href='/command-center/level-up'>Level Up Hub</a></div><div class='card'><b>Commercial integrity boundary</b><p class='muted'>This workspace records opportunity workflow and evidence. It does not execute contracts, move money, guarantee selection or claim a brand/platform approval unless genuine external evidence is recorded.</p></div><div id='notice' class='notice'></div><section><div class='eyebrow'>Available campaigns</div><div id='campaigns' class='grid'><div class='card muted'>Loading…</div></div></section><section><div class='eyebrow'>Applications & delivery</div><div id='apps' class='grid'><div class='card muted'>Loading…</div></div></section></main><script>{SCRIPT}</script></body></html>""")


__all__ = ["router", "CommercialOpportunityStore", "commercial"]
