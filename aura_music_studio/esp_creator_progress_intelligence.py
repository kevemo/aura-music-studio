from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from html import escape
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from .esp_command_center import esp
from .esp_niche import require_esp_hub_member
from .esp_progress import EspProgressStore

router = APIRouter(tags=["ESP Creator Progress Intelligence"])
progress = EspProgressStore(esp)

ProgressKind = Literal["live", "video"]
ExperimentStatus = Literal["draft", "active", "completed", "cancelled"]
TargetDirection = Literal["up", "down", "hold"]

METRIC_META = {
    "views": {"label": "Views", "unit": "count"},
    "duration_minutes": {"label": "Duration", "unit": "minutes"},
    "avg_watch_seconds": {"label": "Average watch", "unit": "seconds"},
    "completion_rate": {"label": "Completion rate", "unit": "percent"},
    "peak_viewers": {"label": "Peak viewers", "unit": "count"},
    "new_followers": {"label": "New followers", "unit": "count"},
    "comments": {"label": "Comments", "unit": "count"},
    "shares": {"label": "Shares", "unit": "count"},
    "saves": {"label": "Saves", "unit": "count"},
    "diamonds": {"label": "Diamonds", "unit": "count"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: str, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _creator(request: Request):
    member, membership = require_esp_hub_member(request)
    role = "owner" if membership.get("status") == "owner" else (membership.get("roles") or "").lower()
    if role not in {"creator", "both", "owner"}:
        raise HTTPException(403, "ESP Creator or Owner access is required")
    return member


class ExperimentCreate(BaseModel):
    kind: ProgressKind
    focus_metric: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=3, max_length=180)
    hypothesis: str = Field(min_length=3, max_length=1200)
    action: str = Field(min_length=3, max_length=2000)
    success_measure: str = Field(min_length=3, max_length=1000)
    target_direction: TargetDirection = "up"
    duration_days: int = Field(default=7, ge=1, le=90)


class ExperimentUpdate(BaseModel):
    status: ExperimentStatus
    result_notes: str = Field(default="", max_length=3000)
    observed_value: float | None = Field(default=None, ge=0)


class ProgressIntelligenceStore:
    """Explainable progress comparisons plus creator-controlled improvement experiments."""

    def __init__(self, db_path: str | None = None, progress_store: EspProgressStore | None = None):
        self.db_path = str(db_path or esp.db_path)
        self.progress = progress_store or progress
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
                CREATE TABLE IF NOT EXISTS esp_creator_progress_experiments (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    focus_metric TEXT NOT NULL,
                    title TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    action_text TEXT NOT NULL,
                    success_measure TEXT NOT NULL,
                    target_direction TEXT NOT NULL,
                    duration_days INTEGER NOT NULL DEFAULT 7,
                    baseline_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'draft',
                    result_notes TEXT NOT NULL DEFAULT '',
                    observed_value REAL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_creator_progress_experiments_user
                    ON esp_creator_progress_experiments(user_id,status,created_at DESC);
                """
            )

    @staticmethod
    def _metric_values(rows: list[dict], metric: str) -> list[float]:
        values: list[float] = []
        for row in rows:
            value = _number((row.get("metrics") or {}).get(metric))
            if value is not None:
                values.append(value)
        return values

    def analyse(self, user_id: str, kind: str, *, max_window: int = 5) -> dict:
        if kind not in {"live", "video"}:
            raise ValueError("Progress kind must be live or video")
        all_rows = [row for row in self.progress.list_for_user(user_id, 200) if row.get("kind") == kind]
        # list_for_user is newest-first. Compare equally sized newest and immediately-prior windows.
        window = min(max(1, int(max_window)), len(all_rows) // 2) if len(all_rows) >= 2 else 0
        recent_rows = all_rows[:window] if window else []
        prior_rows = all_rows[window : window * 2] if window else []
        trends: list[dict] = []
        for metric, meta in METRIC_META.items():
            recent_values = self._metric_values(recent_rows, metric)
            prior_values = self._metric_values(prior_rows, metric)
            if not recent_values or not prior_values:
                continue
            recent_avg = sum(recent_values) / len(recent_values)
            prior_avg = sum(prior_values) / len(prior_values)
            delta = recent_avg - prior_avg
            scale = max(abs(recent_avg), abs(prior_avg), 1.0)
            if abs(delta) <= scale * 0.001:
                direction = "flat"
            else:
                direction = "up" if delta > 0 else "down"
            percent_change = None if prior_avg == 0 else (delta / prior_avg) * 100.0
            evidence_count = min(len(recent_values), len(prior_values))
            strength = "high" if evidence_count >= 3 else "medium" if evidence_count >= 2 else "low"
            trends.append(
                {
                    "metric": metric,
                    "label": meta["label"],
                    "unit": meta["unit"],
                    "recent_average": round(recent_avg, 3),
                    "prior_average": round(prior_avg, 3),
                    "delta": round(delta, 3),
                    "percent_change": None if percent_change is None else round(percent_change, 2),
                    "direction": direction,
                    "recent_observations": len(recent_values),
                    "prior_observations": len(prior_values),
                    "evidence_strength": strength,
                    "normative_judgement": False,
                }
            )
        result = {
            "kind": kind,
            "samples_available": len(all_rows),
            "comparison_window": window,
            "recent_submission_ids": [row.get("id") for row in recent_rows],
            "prior_submission_ids": [row.get("id") for row in prior_rows],
            "trends": trends,
            "latest": all_rows[0] if all_rows else None,
            "comparison_available": bool(window and trends),
            "trend_interpretation": "direction_only_not_automatic_good_or_bad",
        }
        result["recommendations"] = self.recommendations(result)
        return result

    @staticmethod
    def _trend_map(analysis: dict) -> dict[str, dict]:
        return {row["metric"]: row for row in analysis.get("trends") or []}

    @classmethod
    def recommendations(cls, analysis: dict) -> list[dict]:
        kind = analysis.get("kind")
        trends = cls._trend_map(analysis)
        latest_metrics = ((analysis.get("latest") or {}).get("metrics") or {})
        suggestions: list[dict] = []

        def add(metric: str, title: str, hypothesis: str, action: str, success: str, direction: str = "up"):
            if len(suggestions) >= 4:
                return
            suggestions.append(
                {
                    "focus_metric": metric,
                    "title": title,
                    "hypothesis": hypothesis,
                    "action": action,
                    "success_measure": success,
                    "target_direction": direction,
                    "duration_days": 7,
                    "source": "explainable_progress_rules",
                    "automatic_activation": False,
                }
            )

        if not analysis.get("comparison_available"):
            add(
                "views",
                "Build a comparison baseline",
                "A consistent measurement routine will make future strategy changes easier to judge.",
                "Submit at least two comparable performance updates of the same type using the same core metrics.",
                "Enough comparable observations exist to calculate a recent-versus-prior window.",
                "hold",
            )
            return suggestions

        if kind == "live":
            watch = trends.get("avg_watch_seconds")
            followers = trends.get("new_followers")
            shares = trends.get("shares")
            comments = trends.get("comments")
            peak = trends.get("peak_viewers")
            if watch and watch["direction"] == "down":
                add(
                    "avg_watch_seconds",
                    "Opening-minute retention test",
                    "A clearer immediate room promise and faster first interaction may improve early retention.",
                    "For the next comparable LIVE sessions, use one consistent 20-second opening promise, start an audience interaction inside the first minute, and use planned room resets without changing the rest of the format unnecessarily.",
                    "Compare average watch seconds with the prior window while recording the same metric consistently.",
                )
            if followers and followers["direction"] == "down":
                add(
                    "new_followers",
                    "Follow-conversion reason test",
                    "Connecting the follow request to a specific recurring value may convert more retained viewers.",
                    "Give viewers one natural reason to follow tied to the next recurring segment or future show, then keep the wording consistent across the test window.",
                    "Compare new followers per comparable LIVE and note any major format changes separately.",
                )
            if shares and shares["direction"] == "down":
                add(
                    "shares",
                    "Shareable-moment experiment",
                    "A planned useful, surprising or emotionally resonant segment may create more organic shares.",
                    "Place one clearly defined shareable segment in each test LIVE and note its approximate time so it can be reviewed later.",
                    "Compare shares across the test window and review what happened around the planned segment.",
                )
            if comments and comments["direction"] == "down":
                add(
                    "comments",
                    "Conversation prompt experiment",
                    "Specific low-friction questions may create more genuine conversation than generic engagement requests.",
                    "Use three prepared niche-relevant questions at planned points in each test LIVE and record which one produces the strongest conversation.",
                    "Compare comments plus creator notes about conversation quality across comparable sessions.",
                )
            if peak and peak["direction"] == "up" and watch and watch["direction"] == "down":
                add(
                    "avg_watch_seconds",
                    "Arrival-to-retention bridge",
                    "More people are arriving, but the room may need clearer resets to turn arrival spikes into retained viewers.",
                    "When viewer arrivals spike, reset the room with what is happening now, what is coming next and a direct conversational entry point.",
                    "Watch whether average watch rises while peak viewers remain stable or higher.",
                )
        else:
            completion = trends.get("completion_rate")
            shares = trends.get("shares")
            saves = trends.get("saves")
            views = trends.get("views")
            comments = trends.get("comments")
            latest_completion = _number(latest_metrics.get("completion_rate"))
            if (completion and completion["direction"] == "down") or (
                latest_completion is not None and latest_completion < 35
            ):
                add(
                    "completion_rate",
                    "Hook and pacing test",
                    "A faster payoff and less setup may improve the proportion of viewers reaching the end.",
                    "Create several videos around one comparable topic while testing a faster first-second hook and removing non-essential setup before the core payoff.",
                    "Compare completion rate across the test posts while keeping topic and approximate duration as comparable as practical.",
                )
            if shares and shares["direction"] == "down":
                add(
                    "shares",
                    "Share-value test",
                    "A clearer useful, surprising or emotionally resonant payoff may increase sharing.",
                    "Build one explicit share-worthy payoff into each test post without adding repetitive engagement bait.",
                    "Compare shares across comparable posts and note which payoff type was used.",
                )
            if saves and saves["direction"] == "down":
                add(
                    "saves",
                    "Save-worthy utility test",
                    "A concrete reusable takeaway may increase saves on educational or reference-style content.",
                    "Add one concise checklist, tip sequence, reference point or repeatable takeaway to each relevant test post.",
                    "Compare saves across comparable posts and record the takeaway format.",
                )
            if views and views["direction"] == "down":
                add(
                    "views",
                    "Packaging variation test",
                    "Testing the hook, cover and caption around the same core idea can separate packaging effects from topic effects.",
                    "Run controlled variations around one strong core idea rather than changing topic, hook, format and caption all at once.",
                    "Compare views alongside completion so reach changes are not interpreted without retention context.",
                )
            if comments and comments["direction"] == "down":
                add(
                    "comments",
                    "Conversation-ending prompt test",
                    "A specific question connected to the video's payoff may generate more substantive replies.",
                    "End test posts with one topic-specific question that can be answered quickly and genuinely.",
                    "Compare comment volume and creator-noted comment quality across the test posts.",
                )

        if not suggestions:
            add(
                "views",
                "Preserve the working pattern",
                "The currently measured metrics do not expose an obvious declining signal, so unnecessary simultaneous changes could hide what is working.",
                "Choose one element to test while holding the strongest repeatable parts of the current format steady.",
                "Use the next comparison window to determine whether the single change moved its intended metric.",
                "hold",
            )
        return suggestions

    def _baseline_for(self, user_id: str, kind: str, metric: str) -> dict:
        analysis = self.analyse(user_id, kind)
        trend = next((row for row in analysis["trends"] if row["metric"] == metric), None)
        latest = _number(((analysis.get("latest") or {}).get("metrics") or {}).get(metric))
        return {
            "captured_at": _now(),
            "latest_value": latest,
            "trend": trend,
            "samples_available": analysis["samples_available"],
            "comparison_window": analysis["comparison_window"],
        }

    def create_experiment(self, user_id: str, body: ExperimentCreate) -> dict:
        if body.focus_metric not in METRIC_META:
            raise ValueError("Unsupported progress focus metric")
        now = _now()
        row = {
            "id": uuid4().hex,
            "user_id": user_id,
            "kind": body.kind,
            "focus_metric": body.focus_metric,
            "title": _clean(body.title, 180),
            "hypothesis": _clean(body.hypothesis, 1200),
            "action_text": _clean(body.action, 2000),
            "success_measure": _clean(body.success_measure, 1000),
            "target_direction": body.target_direction,
            "duration_days": body.duration_days,
            "baseline_json": json.dumps(self._baseline_for(user_id, body.kind, body.focus_metric), sort_keys=True),
            "status": "draft",
            "result_notes": "",
            "observed_value": None,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "updated_at": now,
        }
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_creator_progress_experiments
                   (id,user_id,kind,focus_metric,title,hypothesis,action_text,success_measure,target_direction,
                    duration_days,baseline_json,status,result_notes,observed_value,created_at,started_at,completed_at,updated_at)
                   VALUES (:id,:user_id,:kind,:focus_metric,:title,:hypothesis,:action_text,:success_measure,:target_direction,
                    :duration_days,:baseline_json,:status,:result_notes,:observed_value,:created_at,:started_at,:completed_at,:updated_at)""",
                row,
            )
        return self.experiment(user_id, row["id"])

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        item = dict(row)
        try:
            item["baseline"] = json.loads(item.pop("baseline_json") or "{}")
        except Exception:
            item["baseline"] = {}
        return item

    def experiment(self, user_id: str, experiment_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM esp_creator_progress_experiments WHERE id=? AND user_id=?",
                (experiment_id, user_id),
            ).fetchone()
        item = self._decode(row)
        if item is None:
            raise KeyError(experiment_id)
        return item

    def experiments(self, user_id: str, limit: int = 100) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT * FROM esp_creator_progress_experiments WHERE user_id=?
                   ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END,created_at DESC LIMIT ?""",
                (user_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return [item for row in rows if (item := self._decode(row)) is not None]

    def update_experiment(self, user_id: str, experiment_id: str, body: ExperimentUpdate) -> dict:
        current = self.experiment(user_id, experiment_id)
        if current["status"] in {"completed", "cancelled"}:
            raise ValueError("Completed or cancelled experiments are locked")
        allowed = {
            "draft": {"draft", "active", "cancelled"},
            "active": {"active", "completed", "cancelled"},
        }
        if body.status not in allowed.get(current["status"], set()):
            raise ValueError("Invalid experiment status transition")
        if body.status == "completed" and not _clean(body.result_notes, 3000):
            raise ValueError("Completion requires human result notes")
        now = _now()
        started_at = current.get("started_at") or (now if body.status == "active" else None)
        completed_at = now if body.status in {"completed", "cancelled"} else None
        with self._connect() as con:
            con.execute(
                """UPDATE esp_creator_progress_experiments
                   SET status=?,result_notes=?,observed_value=?,started_at=?,completed_at=?,updated_at=?
                   WHERE id=? AND user_id=?""",
                (
                    body.status,
                    _clean(body.result_notes, 3000),
                    body.observed_value,
                    started_at,
                    completed_at,
                    now,
                    experiment_id,
                    user_id,
                ),
            )
        return self.experiment(user_id, experiment_id)

    def dashboard(self, user_id: str, kind: str) -> dict:
        return {
            "analysis": self.analyse(user_id, kind),
            "experiments": self.experiments(user_id),
            "automatic_strategy_changes": False,
            "automatic_experiment_activation": False,
            "source_of_truth": "creator_confirmed_progress_history",
        }


intelligence = ProgressIntelligenceStore()


@router.get("/command-center/api/progress/intelligence")
def progress_intelligence_api(request: Request, kind: ProgressKind = Query(default="live")):
    member = _creator(request)
    return intelligence.dashboard(member.user_id, kind)


@router.post("/command-center/api/progress/intelligence/experiments")
def create_progress_experiment_api(body: ExperimentCreate, request: Request):
    member = _creator(request)
    try:
        return {"experiment": intelligence.create_experiment(member.user_id, body)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/command-center/api/progress/intelligence/experiments/{experiment_id}")
def update_progress_experiment_api(experiment_id: str, body: ExperimentUpdate, request: Request):
    member = _creator(request)
    try:
        return {"experiment": intelligence.update_experiment(member.user_id, experiment_id, body)}
    except KeyError as exc:
        raise HTTPException(404, "Progress experiment not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


CSS = """
:root{--line:#ffffff20;--muted:#c9bfd3;--gold:#efc66b;--purple:#a26fff;--green:#7de0a2}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 88% 0,#40185d,transparent 30%),#07050d;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1160px,calc(100% - 28px));margin:auto;padding:32px 0 60px}.top,.row{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.eyebrow{color:var(--gold);font-size:.75rem;font-weight:900;letter-spacing:.13em;text-transform:uppercase}h1{font-size:clamp(2.4rem,6vw,4.8rem);letter-spacing:-.05em;margin:.15em 0}.muted{color:var(--muted);line-height:1.55}.card{border:1px solid var(--line);border-radius:18px;background:#14101deb;padding:16px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.metric{border:1px solid var(--line);border-radius:13px;padding:11px;background:#ffffff06}.metric b{display:block;font-size:1.2rem}.btn,button{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:9px 12px;background:#ffffff09;color:#fff;text-decoration:none;font-weight:850;cursor:pointer}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--purple));color:#160d1d}.good{color:var(--green)}.up,.down,.flat{font-weight:900}.up{color:#7de0a2}.down{color:#ffb3bd}.flat{color:#ddd}form{margin-top:10px}input,select,textarea{width:100%;border:1px solid var(--line);border-radius:10px;background:#080610;color:#fff;padding:9px;margin:4px 0 8px}textarea{min-height:90px}@media(max-width:760px){.grid{grid-template-columns:1fr}}
"""


def _format_value(value, unit: str) -> str:
    if value is None:
        return "—"
    number = float(value)
    text = f"{number:,.1f}" if not number.is_integer() else f"{int(number):,}"
    return f"{text}%" if unit == "percent" else text


def _trend_card(row: dict) -> str:
    pct = row.get("percent_change")
    pct_text = "n/a" if pct is None else f"{pct:+.1f}%"
    return (
        "<div class='metric'>"
        f"<span class='muted'>{escape(row['label'])}</span>"
        f"<b class='{escape(row['direction'])}'>{escape(row['direction'].upper())} · {escape(pct_text)}</b>"
        f"<small class='muted'>Recent avg {_format_value(row['recent_average'], row['unit'])} · prior {_format_value(row['prior_average'], row['unit'])}<br>Evidence: {escape(row['evidence_strength'])}</small>"
        "</div>"
    )


def _recommendation_card(item: dict, kind: str) -> str:
    return (
        "<div class='card'>"
        f"<div class='eyebrow'>Suggested experiment · {escape(METRIC_META[item['focus_metric']]['label'])}</div>"
        f"<h3>{escape(item['title'])}</h3><p>{escape(item['hypothesis'])}</p>"
        f"<p class='muted'><b>Test:</b> {escape(item['action'])}<br><b>Measure:</b> {escape(item['success_measure'])}</p>"
        f"<form method='post' action='/command-center/progress/intelligence/experiments/from-recommendation'>"
        f"<input type='hidden' name='kind' value='{escape(kind, quote=True)}'>"
        f"<input type='hidden' name='focus_metric' value='{escape(item['focus_metric'], quote=True)}'>"
        f"<input type='hidden' name='title' value='{escape(item['title'], quote=True)}'>"
        f"<input type='hidden' name='hypothesis' value='{escape(item['hypothesis'], quote=True)}'>"
        f"<input type='hidden' name='action' value='{escape(item['action'], quote=True)}'>"
        f"<input type='hidden' name='success_measure' value='{escape(item['success_measure'], quote=True)}'>"
        f"<input type='hidden' name='target_direction' value='{escape(item['target_direction'], quote=True)}'>"
        f"<input type='hidden' name='duration_days' value='{int(item['duration_days'])}'>"
        "<button class='primary' type='submit'>Save as my experiment</button></form></div>"
    )


@router.get("/command-center/progress/intelligence", response_class=HTMLResponse, include_in_schema=False)
def progress_intelligence_page(request: Request, kind: ProgressKind = Query(default="live")):
    member = _creator(request)
    data = intelligence.dashboard(member.user_id, kind)
    analysis = data["analysis"]
    trend_html = "".join(_trend_card(row) for row in analysis["trends"])
    if not trend_html:
        trend_html = "<div class='card'><p class='muted'>Add at least two comparable progress submissions with shared numeric metrics to unlock a trend comparison.</p></div>"
    rec_html = "".join(_recommendation_card(item, kind) for item in analysis["recommendations"])
    experiment_html = ""
    for item in data["experiments"][:12]:
        experiment_html += (
            "<div class='card'>"
            f"<div class='row'><div><div class='eyebrow'>{escape(item['kind'].upper())} · {escape(item['focus_metric'].replace('_',' ').title())}</div><h3>{escape(item['title'])}</h3></div><b>{escape(item['status'].upper())}</b></div>"
            f"<p>{escape(item['hypothesis'])}</p><p class='muted'>{escape(item['action_text'])}</p>"
            f"<small class='muted'>Target: {escape(item['target_direction'])} · {int(item['duration_days'])} days · created {escape(item['created_at'][:10])}</small>"
            "</div>"
        )
    experiment_html = experiment_html or "<div class='card'><p class='muted'>No saved progress experiments yet. Recommendations remain suggestions until you save one.</p></div>"
    other = "video" if kind == "live" else "live"
    html = (
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'>"
        f"<title>Creator Progress Intelligence</title><style>{CSS}</style></head><body><main class='wrap'>"
        "<div class='top'><div><div class='eyebrow'>ESP Creator OS · Explainable Analytics</div><h1>Progress Intelligence</h1>"
        f"<p class='muted'>Comparing your newest {analysis['comparison_window']} {escape(kind.upper())} submission(s) with the immediately prior {analysis['comparison_window']}. Directions describe the data only; they are not automatic judgements.</p></div>"
        f"<div><a class='btn' href='/command-center/progress'>Progress</a> <a class='btn' href='/command-center/progress/import'>Import Data</a> <a class='btn primary' href='/command-center/progress/intelligence?kind={other}'>View {other.title()}</a></div></div>"
        f"<section class='grid'>{trend_html}</section><h2>Aura-guided experiments</h2>{rec_html}<h2>My experiments</h2>{experiment_html}"
        "<section class='card'><b>Control boundary</b><p class='muted'>Aura can explain patterns and suggest tests. It does not automatically activate an experiment, change your niche, penalise performance or alter your strategy.</p></section>"
        "</main></body></html>"
    )
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@router.post("/command-center/progress/intelligence/experiments/from-recommendation", include_in_schema=False)
def create_recommended_experiment_page(
    request: Request,
    kind: ProgressKind = Form(...),
    focus_metric: str = Form(...),
    title: str = Form(...),
    hypothesis: str = Form(...),
    action: str = Form(...),
    success_measure: str = Form(...),
    target_direction: TargetDirection = Form("up"),
    duration_days: int = Form(7),
):
    member = _creator(request)
    try:
        intelligence.create_experiment(
            member.user_id,
            ExperimentCreate(
                kind=kind,
                focus_metric=focus_metric,
                title=title,
                hypothesis=hypothesis,
                action=action,
                success_measure=success_measure,
                target_direction=target_direction,
                duration_days=duration_days,
            ),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(f"/command-center/progress/intelligence?kind={kind}", status_code=303)


__all__ = ["router", "ProgressIntelligenceStore", "ExperimentCreate", "ExperimentUpdate", "METRIC_META"]
