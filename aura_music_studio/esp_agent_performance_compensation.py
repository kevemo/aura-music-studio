from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator

from .esp_command_center import esp
from .esp_niche import require_esp_hub_member
from .owner_identity import owner_session_authorized, request_owner_persona

router = APIRouter(tags=["ESP Agent Performance & Compensation"])
_PERIOD = re.compile(r"^20\d{2}-(0[1-9]|1[0-2])$")
_METRIC = re.compile(r"^[a-z][a-z0-9_]{0,79}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: str | None, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _period(value: str) -> str:
    clean = str(value or "").strip()
    if not _PERIOD.fullmatch(clean):
        raise ValueError("Period must use YYYY-MM")
    return clean


def _metric_key(value: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")[:80]
    if not _METRIC.fullmatch(clean):
        raise ValueError("Metric names must begin with a letter and use letters, numbers or underscores")
    return clean


def _percent(value) -> float:
    return round(float(value or 0), 2)


def _money_minor(base_minor: int, percent: float) -> int:
    amount = Decimal(int(base_minor)) * Decimal(str(percent)) / Decimal("100")
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _agent_context(request: Request):
    member, membership = require_esp_hub_member(request)
    role = "owner" if membership.get("status") == "owner" else (membership.get("roles") or "").lower()
    if role not in {"agent", "both", "owner"}:
        raise HTTPException(403, "ESP Agent access is required")
    return member, membership, role == "owner"


def _owner_actor(request: Request) -> str:
    if not owner_session_authorized(request):
        raise HTTPException(403, "ESP Owner access required")
    return request_owner_persona(request) or "ESP Owner"


class BonusRule(BaseModel):
    metric: str = Field(min_length=1, max_length=80)
    threshold: float = Field(gt=0)
    bonus_percent: float = Field(ge=0, le=100)
    label: str = Field(default="", max_length=180)

    @field_validator("metric")
    @classmethod
    def validate_metric(cls, value: str) -> str:
        return _metric_key(value)


class RuleSetCreate(BaseModel):
    name: str = Field(min_length=3, max_length=180)
    region: str = Field(default="global", max_length=80)
    effective_from: str = Field(min_length=7, max_length=7)
    effective_to: str | None = Field(default=None, min_length=7, max_length=7)
    metric_targets: dict[str, float] = Field(min_length=1, max_length=30)
    minimum_completion_percent: float = Field(ge=0, le=100)
    base_commission_percent: float = Field(ge=0, le=100)
    below_minimum_commission_percent: float = Field(ge=0, le=100)
    bonus_rules: list[BonusRule] = Field(default_factory=list, max_length=30)
    max_commission_percent: float = Field(ge=0, le=100)
    currency: str = Field(min_length=3, max_length=3)
    notes: str = Field(default="", max_length=3000)

    @field_validator("effective_from")
    @classmethod
    def validate_from(cls, value: str) -> str:
        return _period(value)

    @field_validator("effective_to")
    @classmethod
    def validate_to(cls, value: str | None) -> str | None:
        return _period(value) if value else None

    @field_validator("metric_targets")
    @classmethod
    def validate_targets(cls, value: dict[str, float]) -> dict[str, float]:
        clean: dict[str, float] = {}
        for key, target in value.items():
            metric = _metric_key(key)
            number = float(target)
            if number <= 0:
                raise ValueError("Every performance target must be greater than zero")
            clean[metric] = round(number, 4)
        if not clean:
            raise ValueError("At least one performance target is required")
        return clean


class PerformanceSubmission(BaseModel):
    metrics: dict[str, float] = Field(default_factory=dict, max_length=60)
    evidence_note: str = Field(default="", max_length=3000)
    submit_for_review: bool = False

    @field_validator("metrics")
    @classmethod
    def validate_metrics(cls, value: dict[str, float]) -> dict[str, float]:
        clean: dict[str, float] = {}
        for key, actual in value.items():
            metric = _metric_key(key)
            if isinstance(actual, bool):
                continue
            number = float(actual)
            if number < 0:
                raise ValueError("Performance metrics cannot be negative")
            clean[metric] = round(number, 4)
        return clean


class CompensationReviewRequest(BaseModel):
    agent_user_id: str = Field(min_length=1, max_length=128)
    period: str = Field(min_length=7, max_length=7)
    verified_eligible_base_minor: int = Field(ge=0, le=2_000_000_000)
    currency: str = Field(min_length=3, max_length=3)
    note: str = Field(default="", max_length=3000)

    @field_validator("period")
    @classmethod
    def validate_period(cls, value: str) -> str:
        return _period(value)


class CompensationStatusRequest(BaseModel):
    status: str = Field(pattern=r"^(approved|paid|void)$")
    note: str = Field(default="", max_length=3000)
    payment_reference: str = Field(default="", max_length=240)


class AgentPerformanceStore:
    """Explainable, owner-configurable Agent performance and compensation review.

    No performance target, commission rate or deduction is hard-coded into runtime policy.
    Rules are effective-dated owner records. Calculations are previews until an ESP Owner
    explicitly approves the statement; this module never transfers money or applies penalties.
    """

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
                CREATE TABLE IF NOT EXISTS esp_agent_performance_rulesets (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    region TEXT NOT NULL DEFAULT 'global',
                    effective_from TEXT NOT NULL,
                    effective_to TEXT,
                    metric_targets_json TEXT NOT NULL,
                    minimum_completion_percent REAL NOT NULL,
                    base_commission_percent REAL NOT NULL,
                    below_minimum_commission_percent REAL NOT NULL,
                    bonus_rules_json TEXT NOT NULL DEFAULT '[]',
                    max_commission_percent REAL NOT NULL,
                    currency TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_rules_effective
                    ON esp_agent_performance_rulesets(status,region,effective_from,effective_to);

                CREATE TABLE IF NOT EXISTS esp_agent_performance_periods (
                    id TEXT PRIMARY KEY,
                    agent_user_id TEXT NOT NULL,
                    period TEXT NOT NULL,
                    region TEXT NOT NULL DEFAULT '',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    evidence_note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'draft',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    submitted_at TEXT,
                    UNIQUE(agent_user_id,period),
                    FOREIGN KEY(agent_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_agent_performance_period_status
                    ON esp_agent_performance_periods(period,status,updated_at DESC);

                CREATE TABLE IF NOT EXISTS esp_agent_compensation_reviews (
                    id TEXT PRIMARY KEY,
                    performance_period_id TEXT NOT NULL,
                    agent_user_id TEXT NOT NULL,
                    period TEXT NOT NULL,
                    ruleset_id TEXT NOT NULL,
                    verified_eligible_base_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    calculated_percent REAL NOT NULL,
                    calculated_amount_minor INTEGER NOT NULL,
                    calculation_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'approved',
                    owner_note TEXT NOT NULL DEFAULT '',
                    payment_reference TEXT NOT NULL DEFAULT '',
                    reviewed_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    paid_at TEXT,
                    voided_at TEXT,
                    FOREIGN KEY(performance_period_id) REFERENCES esp_agent_performance_periods(id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(ruleset_id) REFERENCES esp_agent_performance_rulesets(id) ON DELETE RESTRICT
                );
                CREATE INDEX IF NOT EXISTS idx_agent_comp_reviews_agent
                    ON esp_agent_compensation_reviews(agent_user_id,period,created_at DESC);
                """
            )

    @staticmethod
    def _decode_rules(row) -> dict:
        item = dict(row)
        item["metric_targets"] = json.loads(item.pop("metric_targets_json") or "{}")
        item["bonus_rules"] = json.loads(item.pop("bonus_rules_json") or "[]")
        return item

    @staticmethod
    def _decode_period(row) -> dict:
        item = dict(row)
        item["metrics"] = json.loads(item.pop("metrics_json") or "{}")
        return item

    @staticmethod
    def _decode_review(row) -> dict:
        item = dict(row)
        item["calculation"] = json.loads(item.pop("calculation_json") or "{}")
        return item

    def create_ruleset(self, actor: str, body: RuleSetCreate) -> dict:
        if body.effective_to and body.effective_to < body.effective_from:
            raise ValueError("Ruleset effective_to cannot be earlier than effective_from")
        if body.max_commission_percent < max(body.base_commission_percent, body.below_minimum_commission_percent):
            raise ValueError("Maximum commission percent cannot be below a base commission rate")
        row_id, now = uuid4().hex, _now()
        region = _clean(body.region, 80) or "global"
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_agent_performance_rulesets
                   (id,name,region,effective_from,effective_to,metric_targets_json,minimum_completion_percent,
                    base_commission_percent,below_minimum_commission_percent,bonus_rules_json,max_commission_percent,
                    currency,notes,status,created_by,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?,?)""",
                (
                    row_id, _clean(body.name, 180), region, body.effective_from, body.effective_to,
                    json.dumps(body.metric_targets, sort_keys=True), body.minimum_completion_percent,
                    body.base_commission_percent, body.below_minimum_commission_percent,
                    json.dumps([rule.model_dump() for rule in body.bonus_rules], sort_keys=True),
                    body.max_commission_percent, body.currency.upper(), _clean(body.notes, 3000), _clean(actor, 160), now,
                ),
            )
            row = con.execute("SELECT * FROM esp_agent_performance_rulesets WHERE id=?", (row_id,)).fetchone()
        return self._decode_rules(row)

    def archive_ruleset(self, ruleset_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_agent_performance_rulesets WHERE id=?", (ruleset_id,)).fetchone()
            if not row:
                raise KeyError(ruleset_id)
            con.execute("UPDATE esp_agent_performance_rulesets SET status='archived' WHERE id=?", (ruleset_id,))
            updated = con.execute("SELECT * FROM esp_agent_performance_rulesets WHERE id=?", (ruleset_id,)).fetchone()
        return self._decode_rules(updated)

    def rulesets(self, *, include_archived: bool = False) -> list[dict]:
        with self._connect() as con:
            sql = "SELECT * FROM esp_agent_performance_rulesets"
            if not include_archived:
                sql += " WHERE status='active'"
            sql += " ORDER BY effective_from DESC,created_at DESC"
            rows = con.execute(sql).fetchall()
        return [self._decode_rules(row) for row in rows]

    def _agent_region(self, agent_user_id: str) -> str:
        with self._connect() as con:
            row = con.execute(
                "SELECT region,status,roles FROM esp_memberships WHERE user_id=?",
                (agent_user_id,),
            ).fetchone()
        if not row or row["status"] not in {"active", "owner"}:
            raise ValueError("Agent does not have active ESP access")
        if row["status"] != "owner" and (row["roles"] or "").lower() not in {"agent", "both"}:
            raise ValueError("User does not have an ESP Agent role")
        return str(row["region"] or "")

    def active_ruleset(self, agent_user_id: str, period: str) -> dict | None:
        period = _period(period)
        region = self._agent_region(agent_user_id)
        with self._connect() as con:
            rows = con.execute(
                """SELECT * FROM esp_agent_performance_rulesets
                   WHERE status='active' AND effective_from<=?
                     AND (effective_to IS NULL OR effective_to>=?)
                     AND (LOWER(region)='global' OR LOWER(region)=LOWER(?))
                   ORDER BY CASE WHEN LOWER(region)=LOWER(?) THEN 0 ELSE 1 END,effective_from DESC,created_at DESC""",
                (period, period, region, region),
            ).fetchall()
        return self._decode_rules(rows[0]) if rows else None

    def save_period(self, agent_user_id: str, period: str, body: PerformanceSubmission) -> dict:
        period = _period(period)
        region = self._agent_region(agent_user_id)
        now = _now()
        status = "submitted" if body.submit_for_review else "draft"
        note = _clean(body.evidence_note, 3000)
        if status == "submitted" and not note:
            raise ValueError("Add an evidence note before submitting performance for owner review")
        with self._connect() as con:
            existing = con.execute(
                "SELECT status FROM esp_agent_performance_periods WHERE agent_user_id=? AND period=?",
                (agent_user_id, period),
            ).fetchone()
            if existing and existing["status"] == "owner_reviewed":
                raise ValueError("Owner-reviewed performance is locked; contact an ESP Owner for a correction")
            con.execute(
                """INSERT INTO esp_agent_performance_periods
                   (id,agent_user_id,period,region,metrics_json,evidence_note,status,created_at,updated_at,submitted_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(agent_user_id,period) DO UPDATE SET region=excluded.region,metrics_json=excluded.metrics_json,
                     evidence_note=excluded.evidence_note,status=excluded.status,updated_at=excluded.updated_at,
                     submitted_at=excluded.submitted_at""",
                (
                    uuid4().hex, agent_user_id, period, region, json.dumps(body.metrics, sort_keys=True), note,
                    status, now, now, now if status == "submitted" else None,
                ),
            )
            row = con.execute(
                "SELECT * FROM esp_agent_performance_periods WHERE agent_user_id=? AND period=?",
                (agent_user_id, period),
            ).fetchone()
        return self._decode_period(row)

    def performance_period(self, agent_user_id: str, period: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM esp_agent_performance_periods WHERE agent_user_id=? AND period=?",
                (agent_user_id, _period(period)),
            ).fetchone()
        return self._decode_period(row) if row else None

    @staticmethod
    def calculate(metrics: dict, ruleset: dict, eligible_base_minor: int = 0) -> dict:
        targets = ruleset.get("metric_targets") or {}
        components = []
        completion_values = []
        for metric, target in targets.items():
            actual = float(metrics.get(metric) or 0)
            target = float(target)
            completion = min(100.0, (actual / target * 100.0) if target > 0 else 0.0)
            completion_values.append(completion)
            components.append({
                "metric": metric,
                "actual": round(actual, 4),
                "target": round(target, 4),
                "completion_percent": round(completion, 2),
                "met": actual >= target,
            })
        overall = round(sum(completion_values) / len(completion_values), 2) if completion_values else 0.0
        minimum = float(ruleset.get("minimum_completion_percent") or 0)
        in_good_standing = overall >= minimum
        starting = float(
            ruleset.get("base_commission_percent") if in_good_standing
            else ruleset.get("below_minimum_commission_percent")
        )
        bonuses = []
        bonus_total = 0.0
        for raw in ruleset.get("bonus_rules") or []:
            metric = str(raw.get("metric") or "")
            actual = float(metrics.get(metric) or 0)
            threshold = float(raw.get("threshold") or 0)
            earned = actual >= threshold > 0
            bonus = float(raw.get("bonus_percent") or 0) if earned else 0.0
            bonus_total += bonus
            bonuses.append({
                "label": raw.get("label") or metric,
                "metric": metric,
                "actual": round(actual, 4),
                "threshold": threshold,
                "bonus_percent": float(raw.get("bonus_percent") or 0),
                "earned": earned,
            })
        cap = float(ruleset.get("max_commission_percent") or 0)
        calculated_percent = round(min(cap, starting + bonus_total), 2)
        amount_minor = _money_minor(eligible_base_minor, calculated_percent)
        return {
            "overall_completion_percent": overall,
            "minimum_completion_percent": minimum,
            "meets_configured_minimum": in_good_standing,
            "metric_components": components,
            "starting_commission_percent": round(starting, 2),
            "bonus_rules": bonuses,
            "earned_bonus_percent_before_cap": round(bonus_total, 2),
            "max_commission_percent": round(cap, 2),
            "calculated_commission_percent": calculated_percent,
            "verified_eligible_base_minor": int(eligible_base_minor),
            "calculated_amount_minor": amount_minor,
            "currency": ruleset.get("currency"),
            "automatic_penalty": False,
            "automatic_payout": False,
            "owner_approval_required": True,
        }

    def preview(self, agent_user_id: str, period: str, eligible_base_minor: int = 0) -> dict:
        performance = self.performance_period(agent_user_id, period)
        ruleset = self.active_ruleset(agent_user_id, period)
        if not ruleset:
            return {
                "performance": performance,
                "ruleset": None,
                "calculation": None,
                "message": "No active owner-configured performance ruleset applies to this period.",
            }
        metrics = (performance or {}).get("metrics") or {}
        return {
            "performance": performance,
            "ruleset": ruleset,
            "calculation": self.calculate(metrics, ruleset, eligible_base_minor),
        }

    def approve_review(self, actor: str, body: CompensationReviewRequest) -> dict:
        performance = self.performance_period(body.agent_user_id, body.period)
        if not performance or performance["status"] != "submitted":
            raise ValueError("Agent performance must be submitted before owner compensation review")
        ruleset = self.active_ruleset(body.agent_user_id, body.period)
        if not ruleset:
            raise ValueError("No active owner-configured ruleset applies to this Agent period")
        if ruleset["currency"].upper() != body.currency.upper():
            raise ValueError("Review currency must match the active ruleset currency")
        calculation = self.calculate(performance["metrics"], ruleset, body.verified_eligible_base_minor)
        review_id, now = uuid4().hex, _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_agent_compensation_reviews
                   (id,performance_period_id,agent_user_id,period,ruleset_id,verified_eligible_base_minor,currency,
                    calculated_percent,calculated_amount_minor,calculation_json,status,owner_note,payment_reference,
                    reviewed_by,created_at,updated_at,paid_at,voided_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,'approved',?,'',?,?,?,NULL,NULL)""",
                (
                    review_id, performance["id"], body.agent_user_id, body.period, ruleset["id"],
                    body.verified_eligible_base_minor, body.currency.upper(), calculation["calculated_commission_percent"],
                    calculation["calculated_amount_minor"], json.dumps(calculation, sort_keys=True),
                    _clean(body.note, 3000), _clean(actor, 160), now, now,
                ),
            )
            con.execute(
                "UPDATE esp_agent_performance_periods SET status='owner_reviewed',updated_at=? WHERE id=?",
                (now, performance["id"]),
            )
            row = con.execute("SELECT * FROM esp_agent_compensation_reviews WHERE id=?", (review_id,)).fetchone()
        return self._decode_review(row)

    def set_review_status(self, actor: str, review_id: str, body: CompensationStatusRequest) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_agent_compensation_reviews WHERE id=?", (review_id,)).fetchone()
            if not row:
                raise KeyError(review_id)
            current = row["status"]
            if current == "void":
                raise ValueError("A void compensation review cannot be changed")
            if body.status == "paid" and current != "approved":
                raise ValueError("Only an approved compensation review can be marked paid")
            now = _now()
            note = _clean(body.note, 3000) or row["owner_note"]
            con.execute(
                """UPDATE esp_agent_compensation_reviews SET status=?,owner_note=?,payment_reference=?,updated_at=?,
                   paid_at=?,voided_at=?,reviewed_by=? WHERE id=?""",
                (
                    body.status, note, _clean(body.payment_reference, 240), now,
                    now if body.status == "paid" else row["paid_at"],
                    now if body.status == "void" else row["voided_at"], _clean(actor, 160), review_id,
                ),
            )
            updated = con.execute("SELECT * FROM esp_agent_compensation_reviews WHERE id=?", (review_id,)).fetchone()
        return self._decode_review(updated)

    def reviews_for_agent(self, agent_user_id: str, limit: int = 24) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT * FROM esp_agent_compensation_reviews WHERE agent_user_id=?
                   ORDER BY period DESC,created_at DESC LIMIT ?""",
                (agent_user_id, max(1, min(100, int(limit)))),
            ).fetchall()
        return [self._decode_review(row) for row in rows]

    def agent_dashboard(self, agent_user_id: str) -> dict:
        region = self._agent_region(agent_user_id)
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM esp_agent_performance_periods WHERE agent_user_id=? ORDER BY period DESC LIMIT 24",
                (agent_user_id,),
            ).fetchall()
        periods = [self._decode_period(row) for row in rows]
        enriched = []
        for item in periods:
            preview = self.preview(agent_user_id, item["period"])
            enriched.append(item | {"ruleset": preview["ruleset"], "calculation": preview["calculation"]})
        return {
            "agent_user_id": agent_user_id,
            "region": region,
            "periods": enriched,
            "reviews": self.reviews_for_agent(agent_user_id),
            "rules_configured_by_owner": True,
            "automatic_penalties": False,
            "automatic_payouts": False,
        }

    def owner_dashboard(self) -> dict:
        with self._connect() as con:
            periods = con.execute(
                """SELECT p.*,u.display_name,u.email FROM esp_agent_performance_periods p
                   JOIN users u ON u.id=p.agent_user_id ORDER BY p.period DESC,p.updated_at DESC LIMIT 500"""
            ).fetchall()
            reviews = con.execute(
                """SELECT r.*,u.display_name,u.email FROM esp_agent_compensation_reviews r
                   JOIN users u ON u.id=r.agent_user_id ORDER BY r.period DESC,r.created_at DESC LIMIT 500"""
            ).fetchall()
        decoded_periods = []
        for row in periods:
            item = dict(row)
            item["metrics"] = json.loads(item.pop("metrics_json") or "{}")
            decoded_periods.append(item)
        decoded_reviews = []
        for row in reviews:
            item = dict(row)
            item["calculation"] = json.loads(item.pop("calculation_json") or "{}")
            decoded_reviews.append(item)
        return {
            "rulesets": self.rulesets(include_archived=True),
            "periods": decoded_periods,
            "reviews": decoded_reviews,
            "pending_owner_review": sum(1 for row in decoded_periods if row["status"] == "submitted"),
            "money_transfer_performed": False,
            "automatic_penalties": False,
        }


performance = AgentPerformanceStore()


@router.get("/command-center/api/agent/performance")
def agent_performance_api(request: Request):
    member, _membership, _owner = _agent_context(request)
    return performance.agent_dashboard(member.user_id)


@router.put("/command-center/api/agent/performance/{period}")
def save_agent_performance_api(period: str, body: PerformanceSubmission, request: Request):
    member, _membership, _owner = _agent_context(request)
    try:
        return {"performance": performance.save_period(member.user_id, period, body)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/owner/api/esp-agent-performance")
def owner_agent_performance_api(request: Request):
    _owner_actor(request)
    return performance.owner_dashboard()


@router.post("/owner/api/esp-agent-performance/rulesets")
def owner_create_ruleset_api(body: RuleSetCreate, request: Request):
    actor = _owner_actor(request)
    try:
        return {"ruleset": performance.create_ruleset(actor, body)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/owner/api/esp-agent-performance/reviews")
def owner_approve_compensation_api(body: CompensationReviewRequest, request: Request):
    actor = _owner_actor(request)
    try:
        return {"review": performance.approve_review(actor, body), "money_transfer_performed": False}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/owner/api/esp-agent-performance/reviews/{review_id}")
def owner_review_status_api(review_id: str, body: CompensationStatusRequest, request: Request):
    actor = _owner_actor(request)
    try:
        return {"review": performance.set_review_status(actor, review_id, body), "money_transfer_performed": False}
    except KeyError as exc:
        raise HTTPException(404, "Compensation review not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


CSS = """
:root{--line:#ffffff20;--muted:#c8bfd2;--gold:#efc66b;--violet:#a26dff;--green:#78dda7}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 90% 0,#42185d,transparent 31%),#07050d;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1220px,calc(100% - 28px));margin:auto;padding:34px 0 60px}.top,.row{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.eyebrow{color:var(--gold);font-weight:900;letter-spacing:.14em;text-transform:uppercase;font-size:.72rem}h1{font-size:clamp(2.5rem,7vw,5rem);letter-spacing:-.05em;margin:.15em 0}.muted{color:var(--muted);line-height:1.5}.card{border:1px solid var(--line);border-radius:18px;padding:16px;background:#14101deb;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:9px}.metric{border:1px solid var(--line);border-radius:12px;padding:10px;background:#ffffff06}.metric b{display:block;font-size:1.25rem}.btn{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:9px 12px;background:#ffffff09;color:#fff;text-decoration:none;font-weight:800}.pill{border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:.72rem}@media(max-width:760px){.grid{grid-template-columns:1fr 1fr}}@media(max-width:480px){.grid{grid-template-columns:1fr}}
"""


@router.get("/command-center/agent/performance", response_class=HTMLResponse, include_in_schema=False)
def agent_performance_page(request: Request):
    member, _membership, _owner = _agent_context(request)
    data = performance.agent_dashboard(member.user_id)
    cards = "".join(
        "<article class='card'><div class='row'><div>"
        f"<span class='pill'>{escape(row['status'].upper())}</span><h2>{escape(row['period'])}</h2>"
        f"<p class='muted'>{escape(str(len(row.get('metrics') or {})))} recorded metrics</p></div>"
        + (
            f"<div class='metric'><span class='muted'>Configured completion</span><b>{row['calculation']['overall_completion_percent']}%</b>"
            f"<small class='muted'>Preview rate {row['calculation']['calculated_commission_percent']}%</small></div>"
            if row.get("calculation") else
            "<div class='metric'><span class='muted'>Rules</span><b>Not configured</b></div>"
        ) + "</div></article>"
        for row in data["periods"]
    ) or "<div class='card muted'>No Agent performance periods recorded yet.</div>"
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<meta name='robots' content='noindex,nofollow'><title>ESP Agent Performance</title><style>{CSS}</style></head>"
        "<body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Agent OS</div>"
        "<h1>Performance & Compensation Review</h1><p class='muted'>Track your configured Agent targets and owner-approved statements without hard-coding changing ESP requirements.</p></div>"
        "<a class='btn' href='/command-center/dashboard'>Agent Dashboard</a></div>"
        f"{cards}<section class='card'><b>Important boundary</b><p class='muted'>Percentages shown before owner review are calculations from the currently active owner-configured ruleset. No payout is transferred here and no penalty, role change or disciplinary action is automatic.</p></section>"
        "</main></body></html>",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/owner/esp-agent-performance", response_class=HTMLResponse, include_in_schema=False)
def owner_agent_performance_page(request: Request):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    data = performance.owner_dashboard()
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<meta name='robots' content='noindex,nofollow'><title>ESP Agent Performance Admin</title><style>{CSS}</style></head>"
        "<body><main class='wrap'><div class='top'><div><div class='eyebrow'>ESP Owner Control</div>"
        "<h1>Agent Performance & Compensation</h1><p class='muted'>Effective-dated targets, submitted evidence and human-approved compensation statements.</p></div>"
        "<a class='btn' href='/owner/esp-intelligence'>ESP Intelligence</a></div>"
        f"<section class='grid'><div class='metric'><span class='muted'>Rulesets</span><b>{len(data['rulesets'])}</b></div>"
        f"<div class='metric'><span class='muted'>Performance periods</span><b>{len(data['periods'])}</b></div>"
        f"<div class='metric'><span class='muted'>Pending review</span><b>{data['pending_owner_review']}</b></div>"
        f"<div class='metric'><span class='muted'>Approved/paid statements</span><b>{len(data['reviews'])}</b></div></section>"
        "<section class='card'><b>Owner control</b><p class='muted'>Targets and commission rates come only from owner-created effective-dated rulesets. The system calculates transparently but does not pay anyone, apply penalties or change ESP roles.</p></section>"
        "</main></body></html>",
        headers={"Cache-Control": "no-store"},
    )


__all__ = [
    "router", "AgentPerformanceStore", "performance", "RuleSetCreate", "PerformanceSubmission",
    "CompensationReviewRequest", "CompensationStatusRequest", "BonusRule",
]
