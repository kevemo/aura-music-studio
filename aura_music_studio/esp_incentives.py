from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from html import escape
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_command_center import esp
from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["ESP Incentives & Rewards"])
ClaimStatus = Literal["tracking", "submitted", "review", "qualified", "not_qualified", "awarded", "paused"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


PROGRAMS: tuple[dict, ...] = (
    {
        "id": "monthly-recognition",
        "title": "Monthly Creator Recognition Rewards",
        "kind": "selection",
        "summary": "Monthly recognition for overall performance, short-form promotion, authentic growth, referrals, Discord participation and positive community contribution.",
        "reward": "Programme-specific LIVE Coin Gift reward after owner verification/selection.",
        "automatic": False,
        "source_label": "Official Creator Incentive Program (2026)",
    },
    {
        "id": "rank-up",
        "title": "Creator Rank-Up Incentive",
        "kind": "funding_dependent",
        "summary": "Tracks qualifying rank increases where TikTok activates the relevant network bonus and ESP actually receives it.",
        "reward": "12.5% of the qualifying rank-up bonus actually received by the network for that creator, subject to verification.",
        "automatic": False,
        "source_label": "Official Creator Incentive Program (2026)",
    },
    {
        "id": "maintenance",
        "title": "Maintenance Incentive — 5% / 10%",
        "kind": "checklist_funding_dependent",
        "summary": "Regional maintenance threshold plus LIVE/video/referral/participation requirements. 10% requires the full enhanced checklist; there is no in-between percentage.",
        "reward": "5% or 10% of maintenance funds actually received for that creator when TikTok activates the bonus and all applicable requirements are verified.",
        "automatic": False,
        "source_label": "Official Creator Incentive Program (2026)",
    },
    {
        "id": "referral-rookie",
        "title": "Creator Referral Incentive",
        "kind": "verified_milestone",
        "summary": "Direct verified creator referrals with the Rookie reaching the programme diamond target within the required time window.",
        "reward": "Current programme reward after direct-referral, join and performance verification.",
        "automatic": False,
        "source_label": "Creator Ambassador Referral Program / Official Creator Incentive Program (2026)",
    },
    {
        "id": "battle-league",
        "title": "ESP Battle League Incentives",
        "kind": "competition",
        "summary": "Placement-based ESP Battle League reward pathway using verified league results and programme rules.",
        "reward": "Placement reward recorded only after league result verification.",
        "automatic": False,
        "source_label": "ESP Battle League Incentive",
    },
    {
        "id": "high-tier",
        "title": "High-Tier Creator Performance Rewards",
        "kind": "verified_milestone",
        "summary": "High-diamond and long-term consistency reward pathways where the current programme remains active.",
        "reward": "Programme-defined gift after period/diamond verification and owner confirmation.",
        "automatic": False,
        "source_label": "Official Creator Incentive Program (2026)",
    },
    {
        "id": "consistency-train",
        "title": "ESP Creator Consistency Train",
        "kind": "multi_period_checklist",
        "summary": "Four-month consistency pathway combining authentic diamond milestones, structured LIVE activity, collaboration and professional-development participation.",
        "reward": "Current programme completion reward plus equipment-wheel eligibility after all requirements are verified.",
        "automatic": False,
        "source_label": "ESP New Creator Consistency Train / Equipment Care Package Spin Wheel",
    },
)


class ClaimRequest(BaseModel):
    program_id: str = Field(min_length=1, max_length=100)
    period_label: str = Field(default="", max_length=160)
    metrics: dict = Field(default_factory=dict)
    checklist: dict = Field(default_factory=dict)
    note: str = Field(default="", max_length=3000)


class ClaimUpdate(BaseModel):
    metrics: dict | None = None
    checklist: dict | None = None
    note: str | None = Field(default=None, max_length=3000)
    status: Literal["tracking", "submitted"] | None = None


class OwnerReview(BaseModel):
    status: Literal["review", "qualified", "not_qualified", "awarded", "paused"]
    owner_reason: str = Field(default="", max_length=3000)
    reward_record: str = Field(default="", max_length=1000)


def _program(program_id: str) -> dict:
    row = next((item for item in PROGRAMS if item["id"] == program_id), None)
    if row is None:
        raise KeyError(program_id)
    return row


def _roles(membership: dict) -> set[str]:
    if membership.get("status") == "owner":
        return {"creator", "agent", "owner"}
    role = str(membership.get("roles") or "").lower()
    return {"creator", "agent"} if role == "both" else {role}


def _clean_mapping(value: dict | None, limit: int = 80) -> dict:
    out: dict = {}
    for key, raw in (value or {}).items():
        name = str(key).strip().lower().replace(" ", "_")[:limit]
        if not name:
            continue
        if isinstance(raw, bool):
            out[name] = raw
        elif isinstance(raw, (int, float)):
            out[name] = raw
        else:
            out[name] = str(raw).strip()[:300]
    return out


def evaluate_maintenance(metrics: dict, checklist: dict) -> dict:
    region = str(metrics.get("region") or "").strip().lower()
    diamonds = float(metrics.get("diamonds") or 0)
    live_days = float(metrics.get("live_days") or 0)
    live_hours = float(metrics.get("live_hours") or 0)
    referrals = float(metrics.get("joined_referrals") or 0)
    threshold = 80_000 if region in {"latam", "spain", "latam/spain", "latam / spain"} else 200_000

    def flag(name: str) -> bool:
        return bool(checklist.get(name))

    base = {
        "maintenance_threshold": diamonds >= threshold,
        "maintained_previous_month": flag("maintained_previous_month"),
        "live_days_16": live_days >= 16,
        "live_hours_60": live_hours >= 60,
        "discord_active": flag("discord_active"),
        "two_daily_videos": flag("two_daily_videos"),
        "prelive_video": flag("prelive_video"),
        "one_joined_referral": referrals >= 1,
    }
    enhanced = {
        "maintenance_threshold": diamonds >= threshold,
        "maintained_previous_month": flag("maintained_previous_month"),
        "live_days_22": live_days >= 22,
        "live_hours_80": live_hours >= 80,
        "required_meetings": flag("required_meetings"),
        "analytics_training": flag("analytics_training"),
        "discord_active": flag("discord_active"),
        "campaign_participation": flag("campaign_participation"),
        "four_scheduled_battles": float(metrics.get("scheduled_battles") or 0) >= 4,
        "enhanced_video_strategy": flag("enhanced_video_strategy"),
        "professional_good_standing": flag("professional_good_standing"),
        "two_joined_referrals": referrals >= 2,
    }
    tiktok_funding = flag("tiktok_bonus_activated") and flag("network_funds_received")
    base_met, enhanced_met = all(base.values()), all(enhanced.values())
    percentage = 10 if enhanced_met and tiktok_funding else 5 if base_met and tiktok_funding else 0
    return {
        "regional_threshold": threshold,
        "base_requirements": base,
        "enhanced_requirements": enhanced,
        "base_requirements_met": base_met,
        "enhanced_requirements_met": enhanced_met,
        "tiktok_bonus_and_network_funds_verified": tiktok_funding,
        "provisional_percentage": percentage,
        "owner_review_required": True,
        "award_automatic": False,
        "note": "The checklist is an explainable aid. Programme activation, received funds, evidence, good standing and final eligibility require human verification.",
    }


class IncentiveStore:
    def __init__(self, db_path=None):
        self.db_path = db_path or esp.db_path
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self):
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS esp_incentive_claims (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    program_id TEXT NOT NULL,
                    period_label TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'tracking',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    checklist_json TEXT NOT NULL DEFAULT '{}',
                    note TEXT NOT NULL DEFAULT '',
                    owner_reason TEXT NOT NULL DEFAULT '',
                    reward_record TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    UNIQUE(user_id,program_id,period_label),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_incentive_user ON esp_incentive_claims(user_id,status,updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_incentive_review ON esp_incentive_claims(status,updated_at DESC);
                """
            )

    @staticmethod
    def _decode(row) -> dict:
        item = dict(row)
        for key in ("metrics", "checklist"):
            try:
                item[key] = json.loads(item.pop(key + "_json") or "{}")
            except Exception:
                item[key] = {}
        item["evaluation"] = evaluate_maintenance(item["metrics"], item["checklist"]) if item["program_id"] == "maintenance" else {"owner_review_required": True, "award_automatic": False}
        return item

    def create(self, user_id: str, body: ClaimRequest) -> dict:
        _program(body.program_id)
        now = _now()
        claim_id = uuid4().hex
        try:
            with self._connect() as con:
                con.execute(
                    """INSERT INTO esp_incentive_claims
                    (id,user_id,program_id,period_label,status,metrics_json,checklist_json,note,created_at,updated_at)
                    VALUES (?,?,?,?, 'tracking',?,?,?,?,?)""",
                    (claim_id, user_id, body.program_id, body.period_label.strip()[:160], json.dumps(_clean_mapping(body.metrics), sort_keys=True), json.dumps(_clean_mapping(body.checklist), sort_keys=True), " ".join(body.note.split())[:3000], now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise FileExistsError("A tracking record already exists for this programme and period") from exc
        return self.get(claim_id, user_id=user_id)

    def get(self, claim_id: str, *, user_id: str | None = None, owner: bool = False) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_incentive_claims WHERE id=?", (claim_id,)).fetchone()
        if row is None or (not owner and user_id != row["user_id"]):
            raise KeyError(claim_id)
        return self._decode(row)

    def list(self, user_id: str | None = None, *, owner: bool = False) -> list[dict]:
        with self._connect() as con:
            if owner:
                rows = con.execute("SELECT * FROM esp_incentive_claims ORDER BY updated_at DESC").fetchall()
            else:
                rows = con.execute("SELECT * FROM esp_incentive_claims WHERE user_id=? ORDER BY updated_at DESC", (user_id,)).fetchall()
        return [self._decode(row) for row in rows]

    def update(self, claim_id: str, user_id: str, body: ClaimUpdate) -> dict:
        row = self.get(claim_id, user_id=user_id)
        if row["status"] in {"qualified", "not_qualified", "awarded", "paused"}:
            raise PermissionError("This claim is under/after owner review and can no longer be edited by the creator")
        metrics = _clean_mapping(body.metrics) if body.metrics is not None else row["metrics"]
        checklist = _clean_mapping(body.checklist) if body.checklist is not None else row["checklist"]
        note = " ".join(body.note.split())[:3000] if body.note is not None else row["note"]
        status = body.status or row["status"]
        with self._connect() as con:
            con.execute(
                "UPDATE esp_incentive_claims SET metrics_json=?,checklist_json=?,note=?,status=?,updated_at=? WHERE id=?",
                (json.dumps(metrics, sort_keys=True), json.dumps(checklist, sort_keys=True), note, status, _now(), claim_id),
            )
        return self.get(claim_id, user_id=user_id)

    def review(self, claim_id: str, body: OwnerReview) -> dict:
        self.get(claim_id, owner=True)
        with self._connect() as con:
            con.execute(
                """UPDATE esp_incentive_claims SET status=?,owner_reason=?,reward_record=?,reviewed_at=?,updated_at=? WHERE id=?""",
                (body.status, " ".join(body.owner_reason.split())[:3000], " ".join(body.reward_record.split())[:1000], _now(), _now(), claim_id),
            )
        return self.get(claim_id, owner=True)


store = IncentiveStore()


def _context(request: Request):
    return require_esp_hub_member(request)


@router.get("/command-center/api/incentives/programs")
def incentive_programs(request: Request):
    _context(request)
    return {"programs": list(PROGRAMS), "automatic_awards": False, "source_private": True}


@router.get("/command-center/api/incentives/claims")
def incentive_claims(request: Request):
    member, membership = _context(request)
    owner = membership.get("status") == "owner"
    return {"claims": store.list(member.user_id, owner=owner), "owner_view": owner}


@router.post("/command-center/api/incentives/claims")
def create_incentive_claim(body: ClaimRequest, request: Request):
    member, membership = _context(request)
    if "creator" not in _roles(membership) and membership.get("status") != "owner":
        raise HTTPException(403, "Creator role required to start an incentive tracking record")
    try:
        return {"claim": store.create(member.user_id, body)}
    except KeyError as exc:
        raise HTTPException(404, "Incentive programme not found") from exc
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.patch("/command-center/api/incentives/claims/{claim_id}")
def update_incentive_claim(claim_id: str, body: ClaimUpdate, request: Request):
    member, _membership = _context(request)
    try:
        return {"claim": store.update(claim_id, member.user_id, body)}
    except KeyError as exc:
        raise HTTPException(404, "Incentive tracking record not found") from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.patch("/command-center/api/incentives/claims/{claim_id}/owner-review")
def review_incentive_claim(claim_id: str, body: OwnerReview, request: Request):
    _member, membership = _context(request)
    if membership.get("status") != "owner":
        raise HTTPException(403, "ESP owner approval required")
    try:
        return {"claim": store.review(claim_id, body)}
    except KeyError as exc:
        raise HTTPException(404, "Incentive tracking record not found") from exc


@router.get("/command-center/incentives", response_class=HTMLResponse, include_in_schema=False)
def incentives_page(request: Request):
    member, membership = _context(request)
    owner = membership.get("status") == "owner"
    claims = store.list(member.user_id, owner=owner)
    program_html = "".join(
        f"<article class='card'><span class='pill'>{escape(str(p['kind']).replace('_',' ').title())}</span><h3>{escape(p['title'])}</h3><p>{escape(p['summary'])}</p><p><b>Reward:</b> {escape(p['reward'])}</p><small>{escape(p['source_label'])}</small></article>"
        for p in PROGRAMS
    )
    claim_html = "".join(
        f"<tr><td>{escape(str(c['program_id']))}</td><td>{escape(str(c['period_label'] or 'Current'))}</td><td>{escape(str(c['status']).replace('_',' ').title())}</td><td>{'Owner review required' if c['evaluation'].get('owner_review_required') else '—'}</td></tr>"
        for c in claims
    ) or "<tr><td colspan='4' class='muted'>No incentive tracking records yet.</td></tr>"
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Incentives</title><style>:root{{--line:#ffffff1e;--gold:#f1c86f;--muted:#c3bfd0}}*{{box-sizing:border-box}}body{{margin:0;background:#07050c;color:#fff;font-family:Inter,system-ui,sans-serif}}a{{color:inherit}}.wrap{{width:min(1250px,calc(100% - 28px));margin:auto;padding:35px 0 70px}}.eyebrow{{color:var(--gold);font-weight:950;text-transform:uppercase;letter-spacing:.14em;font-size:.7rem}}h1{{font-size:clamp(2.7rem,7vw,5.2rem);letter-spacing:-.06em;margin:.15em 0}}p,.muted,small{{color:var(--muted);line-height:1.55}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}}.card{{border:1px solid var(--line);border-radius:16px;padding:14px;background:#15101e}}.pill,.btn{{border:1px solid var(--line);border-radius:999px;padding:5px 8px;font-size:.7rem}}.btn{{border-radius:9px;text-decoration:none;color:#fff}}table{{width:100%;border-collapse:collapse}}th,td{{padding:9px;border-bottom:1px solid var(--line);text-align:left}}@media(max-width:850px){{.grid{{grid-template-columns:1fr}}}}</style></head><body><main class='wrap'><div class='eyebrow'>Elevate Souls Productions · Verified Rewards</div><h1>Incentives & Eligibility</h1><p>Track progress against current ESP programmes. Nothing on this page automatically guarantees or issues a reward; funding activation, evidence, programme availability and final eligibility remain subject to verification.</p><p><a class='btn' href='/command-center/member-hub'>Member Hub</a></p><section class='grid'>{program_html}</section><section class='card'><h2>{'All claims' if owner else 'My tracking records'}</h2><table><thead><tr><th>Programme</th><th>Period</th><th>Status</th><th>Review</th></tr></thead><tbody>{claim_html}</tbody></table></section></main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


__all__ = ["router", "PROGRAMS", "IncentiveStore", "evaluate_maintenance"]
