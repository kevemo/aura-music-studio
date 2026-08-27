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
from .esp_niche import EspNicheStore, NICHE_CATALOG, require_esp_hub_member

router = APIRouter(tags=["ESP Creator Niche Discovery Lab"])
ExperimentStatus = Literal["planned", "completed", "archived"]

SCORE_WEIGHTS = {
    "interest": 15,
    "expertise": 15,
    "content_depth": 15,
    "audience_clarity": 15,
    "live_fit": 15,
    "repeatability": 15,
    "distinctiveness": 10,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: str | None, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _require_creator(request: Request):
    member, membership = require_esp_hub_member(request)
    role = "owner" if membership.get("status") == "owner" else (membership.get("roles") or "").lower()
    if role not in {"creator", "both", "owner"}:
        raise HTTPException(403, "ESP Creator access is required for Niche Discovery")
    return member


def _clean_metrics(values: dict | None) -> dict[str, int | float]:
    result: dict[str, int | float] = {}
    for key, value in list((values or {}).items())[:20]:
        name = _clean(key, 80).lower().replace(" ", "_")
        if not name or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number < 0:
            continue
        result[name] = int(number) if number.is_integer() else round(number, 3)
    return result


class CandidateCreate(BaseModel):
    niche_key: str = Field(min_length=1, max_length=80)
    sub_niche: str = Field(default="", max_length=240)
    audience: str = Field(default="", max_length=1000)
    promise: str = Field(default="", max_length=1000)
    interest: int = Field(ge=1, le=5)
    expertise: int = Field(ge=1, le=5)
    content_depth: int = Field(ge=1, le=5)
    audience_clarity: int = Field(ge=1, le=5)
    live_fit: int = Field(ge=1, le=5)
    repeatability: int = Field(ge=1, le=5)
    distinctiveness: int = Field(ge=1, le=5)


class ExperimentCreate(BaseModel):
    day_number: int = Field(default=1, ge=1, le=30)
    channel: str = Field(default="shortform", max_length=80)
    test_title: str = Field(min_length=3, max_length=240)
    hypothesis: str = Field(default="", max_length=1500)
    success_signal: str = Field(default="", max_length=800)


class ExperimentUpdate(BaseModel):
    status: ExperimentStatus
    result_note: str = Field(default="", max_length=2500)
    metrics: dict = Field(default_factory=dict)


class CreatorNicheDiscoveryStore:
    def __init__(self, db_path: str | None = None, niche_store: EspNicheStore | None = None):
        self.db_path = db_path or esp.db_path
        self.niches = niche_store or EspNicheStore(esp)
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
                CREATE TABLE IF NOT EXISTS esp_creator_niche_candidates (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    niche_key TEXT NOT NULL,
                    sub_niche TEXT NOT NULL DEFAULT '',
                    audience TEXT NOT NULL DEFAULT '',
                    promise TEXT NOT NULL DEFAULT '',
                    interest INTEGER NOT NULL,
                    expertise INTEGER NOT NULL,
                    content_depth INTEGER NOT NULL,
                    audience_clarity INTEGER NOT NULL,
                    live_fit INTEGER NOT NULL,
                    repeatability INTEGER NOT NULL,
                    distinctiveness INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'testing',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    selected_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_niche_candidate_user
                    ON esp_creator_niche_candidates(user_id,status,updated_at DESC);

                CREATE TABLE IF NOT EXISTS esp_creator_niche_experiments (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    day_number INTEGER NOT NULL,
                    channel TEXT NOT NULL,
                    test_title TEXT NOT NULL,
                    hypothesis TEXT NOT NULL DEFAULT '',
                    success_signal TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'planned',
                    result_note TEXT NOT NULL DEFAULT '',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(candidate_id) REFERENCES esp_creator_niche_candidates(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_niche_experiment_candidate
                    ON esp_creator_niche_experiments(candidate_id,day_number,created_at);
                """
            )

    @staticmethod
    def score(values: dict) -> dict:
        components = {}
        total = 0.0
        for key, weight in SCORE_WEIGHTS.items():
            value = max(1, min(5, int(values.get(key) or 1)))
            points = round((value / 5) * weight, 2)
            components[key] = {"rating": value, "weight": weight, "points": points}
            total += points
        score = round(total, 1)
        if score >= 80:
            band = "strong_test_candidate"
        elif score >= 65:
            band = "promising_candidate"
        elif score >= 50:
            band = "develop_clarity"
        else:
            band = "rework_before_testing"
        return {"score": score, "band": band, "components": components, "method": "transparent_weighted_self_assessment"}

    def _candidate_row(self, user_id: str, candidate_id: str):
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM esp_creator_niche_candidates WHERE id=? AND user_id=?",
                (candidate_id, user_id),
            ).fetchone()
        if not row:
            raise KeyError(candidate_id)
        return row

    def _decode_candidate(self, row) -> dict:
        item = dict(row)
        score_input = {key: item[key] for key in SCORE_WEIGHTS}
        item["scorecard"] = self.score(score_input)
        item["catalog"] = dict(NICHE_CATALOG.get(item["niche_key"], NICHE_CATALOG["other"]))
        with self._connect() as con:
            experiments = con.execute(
                "SELECT * FROM esp_creator_niche_experiments WHERE candidate_id=? AND user_id=? ORDER BY day_number,created_at",
                (item["id"], item["user_id"]),
            ).fetchall()
        decoded = []
        for exp in experiments:
            value = dict(exp)
            try:
                value["metrics"] = json.loads(value.pop("metrics_json") or "{}")
            except Exception:
                value["metrics"] = {}
            decoded.append(value)
        item["experiments"] = decoded
        completed = sum(1 for exp in decoded if exp["status"] == "completed")
        item["experiment_progress"] = {
            "completed": completed,
            "total": len(decoded),
            "percent": round(completed / len(decoded) * 100, 1) if decoded else 0.0,
        }
        item["trend_data_used_for_score"] = False
        item["automatic_niche_change"] = False
        return item

    def _default_experiments(self, candidate_id: str, user_id: str, niche_key: str, promise: str) -> list[dict]:
        title = NICHE_CATALOG.get(niche_key, NICHE_CATALOG["other"])["title"]
        now = _now()
        rows = (
            (1, "profile", "Clarify the audience promise", f"Present the {title} promise in one clear sentence and check whether a new viewer can understand what they will get.", "Can a neutral reviewer repeat the creator promise accurately?"),
            (2, "shortform", "Test two opening hooks", f"Create two different first-second hooks around the same {title} topic so the hook—not the whole idea—is the variable.", "Compare retention/view response using creator-supplied analytics."),
            (4, "live", "Test one repeatable LIVE format", f"Run one clearly structured {title} LIVE segment with an opening, interaction loop and payoff.", "Record watch/engagement observations and whether the format was sustainable."),
            (7, "community", "Review audience-fit signals", f"Review comments, questions and creator-supplied performance evidence against the promise: {promise or 'the candidate audience promise'}.", "Decide what to keep, change or reject before selecting the niche."),
        )
        return [
            {
                "id": uuid4().hex,
                "candidate_id": candidate_id,
                "user_id": user_id,
                "day_number": row[0],
                "channel": row[1],
                "test_title": row[2],
                "hypothesis": row[3],
                "success_signal": row[4],
                "status": "planned",
                "result_note": "",
                "metrics_json": "{}",
                "created_at": now,
                "updated_at": now,
                "completed_at": None,
            }
            for row in rows
        ]

    def create_candidate(self, user_id: str, body: CandidateCreate) -> dict:
        niche_key = _clean(body.niche_key, 80).lower()
        if niche_key not in NICHE_CATALOG:
            raise ValueError("Choose a supported ESP creator niche")
        candidate_id, now = uuid4().hex, _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_creator_niche_candidates
                   (id,user_id,niche_key,sub_niche,audience,promise,interest,expertise,content_depth,
                    audience_clarity,live_fit,repeatability,distinctiveness,status,created_at,updated_at,selected_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'testing',?,?,NULL)""",
                (
                    candidate_id, user_id, niche_key, _clean(body.sub_niche, 240), _clean(body.audience, 1000),
                    _clean(body.promise, 1000), body.interest, body.expertise, body.content_depth,
                    body.audience_clarity, body.live_fit, body.repeatability, body.distinctiveness, now, now,
                ),
            )
            for exp in self._default_experiments(candidate_id, user_id, niche_key, _clean(body.promise, 1000)):
                con.execute(
                    """INSERT INTO esp_creator_niche_experiments
                       (id,candidate_id,user_id,day_number,channel,test_title,hypothesis,success_signal,status,
                        result_note,metrics_json,created_at,updated_at,completed_at)
                       VALUES (:id,:candidate_id,:user_id,:day_number,:channel,:test_title,:hypothesis,:success_signal,
                        :status,:result_note,:metrics_json,:created_at,:updated_at,:completed_at)""",
                    exp,
                )
        return self.get_candidate(user_id, candidate_id)

    def get_candidate(self, user_id: str, candidate_id: str) -> dict:
        return self._decode_candidate(self._candidate_row(user_id, candidate_id))

    def list_candidates(self, user_id: str) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM esp_creator_niche_candidates WHERE user_id=? ORDER BY updated_at DESC LIMIT 50",
                (user_id,),
            ).fetchall()
        result = [self._decode_candidate(row) for row in rows]
        result.sort(key=lambda item: (-float(item["scorecard"]["score"]), item["created_at"]))
        return result

    def add_experiment(self, user_id: str, candidate_id: str, body: ExperimentCreate) -> dict:
        self._candidate_row(user_id, candidate_id)
        now = _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_creator_niche_experiments
                   (id,candidate_id,user_id,day_number,channel,test_title,hypothesis,success_signal,status,
                    result_note,metrics_json,created_at,updated_at,completed_at)
                   VALUES (?,?,?,?,?,?,?,?,'planned','','{}',?,?,NULL)""",
                (
                    uuid4().hex, candidate_id, user_id, body.day_number, _clean(body.channel, 80) or "shortform",
                    _clean(body.test_title, 240), _clean(body.hypothesis, 1500), _clean(body.success_signal, 800), now, now,
                ),
            )
            con.execute("UPDATE esp_creator_niche_candidates SET updated_at=? WHERE id=?", (now, candidate_id))
        return self.get_candidate(user_id, candidate_id)

    def update_experiment(self, user_id: str, experiment_id: str, body: ExperimentUpdate) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM esp_creator_niche_experiments WHERE id=? AND user_id=?",
                (experiment_id, user_id),
            ).fetchone()
        if not row:
            raise KeyError(experiment_id)
        note = _clean(body.result_note, 2500)
        if body.status == "completed" and not note:
            raise ValueError("Add a result note before completing a niche experiment")
        now = _now()
        metrics = _clean_metrics(body.metrics)
        with self._connect() as con:
            con.execute(
                """UPDATE esp_creator_niche_experiments SET status=?,result_note=?,metrics_json=?,updated_at=?,completed_at=?
                   WHERE id=? AND user_id=?""",
                (
                    body.status, note, json.dumps(metrics, sort_keys=True), now,
                    now if body.status == "completed" else None, experiment_id, user_id,
                ),
            )
            con.execute("UPDATE esp_creator_niche_candidates SET updated_at=? WHERE id=?", (now, row["candidate_id"]))
        return self.get_candidate(user_id, row["candidate_id"])

    def activate_candidate(self, user_id: str, candidate_id: str) -> dict:
        candidate = self.get_candidate(user_id, candidate_id)
        existing = self.niches.get(user_id)
        network_status = (existing or {}).get("network_status") or "unsure"
        old_goals = list((existing or {}).get("goals") or [])
        goals = []
        if candidate.get("promise"):
            goals.append(candidate["promise"])
        goals.extend(old_goals)
        deduped = list(dict.fromkeys(value for value in goals if value))[:20]
        profile = self.niches.set(
            user_id,
            niche=candidate["niche_key"],
            sub_niche=candidate.get("sub_niche") or "",
            audience=candidate.get("audience") or "",
            goals=deduped,
            network_status=network_status,
        )
        now = _now()
        with self._connect() as con:
            con.execute(
                "UPDATE esp_creator_niche_candidates SET status='testing' WHERE user_id=? AND status='selected' AND id<>?",
                (user_id, candidate_id),
            )
            con.execute(
                "UPDATE esp_creator_niche_candidates SET status='selected',selected_at=?,updated_at=? WHERE id=? AND user_id=?",
                (now, now, candidate_id, user_id),
            )
        return {"candidate": self.get_candidate(user_id, candidate_id), "active_profile": profile, "explicit_creator_selection": True}


niche_lab = CreatorNicheDiscoveryStore()


@router.get("/command-center/api/niche-lab")
def niche_lab_api(request: Request):
    member = _require_creator(request)
    return {
        "active_profile": niche_lab.niches.get(member.user_id),
        "candidates": niche_lab.list_candidates(member.user_id),
        "score_weights": SCORE_WEIGHTS,
        "trend_data_used_for_score": False,
        "automatic_niche_change": False,
    }


@router.post("/command-center/api/niche-lab/candidates")
def create_candidate_api(body: CandidateCreate, request: Request):
    member = _require_creator(request)
    try:
        return {"candidate": niche_lab.create_candidate(member.user_id, body)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/command-center/api/niche-lab/candidates/{candidate_id}/experiments")
def add_experiment_api(candidate_id: str, body: ExperimentCreate, request: Request):
    member = _require_creator(request)
    try:
        return {"candidate": niche_lab.add_experiment(member.user_id, candidate_id, body)}
    except KeyError as exc:
        raise HTTPException(404, "Niche candidate not found") from exc


@router.patch("/command-center/api/niche-lab/experiments/{experiment_id}")
def update_experiment_api(experiment_id: str, body: ExperimentUpdate, request: Request):
    member = _require_creator(request)
    try:
        return {"candidate": niche_lab.update_experiment(member.user_id, experiment_id, body)}
    except KeyError as exc:
        raise HTTPException(404, "Niche experiment not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/command-center/api/niche-lab/candidates/{candidate_id}/activate")
def activate_candidate_api(candidate_id: str, request: Request):
    member = _require_creator(request)
    try:
        return niche_lab.activate_candidate(member.user_id, candidate_id)
    except KeyError as exc:
        raise HTTPException(404, "Niche candidate not found") from exc


CSS = """
:root{--line:#ffffff20;--muted:#c8bfd2;--gold:#efc66b;--violet:#a26dff;--green:#78dda7}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 88% 0,#42185d,transparent 31%),#07050d;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1180px,calc(100% - 28px));margin:auto;padding:34px 0 60px}.top,.row{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.eyebrow{color:var(--gold);font-weight:900;letter-spacing:.14em;text-transform:uppercase;font-size:.72rem}h1{font-size:clamp(2.5rem,7vw,5rem);letter-spacing:-.05em;margin:.15em 0}.muted{color:var(--muted);line-height:1.5}.card{border:1px solid var(--line);border-radius:18px;padding:16px;background:#14101deb;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}.metric{border:1px solid var(--line);border-radius:12px;padding:10px;background:#ffffff06}.metric b{display:block;font-size:1.25rem}.btn,button{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:9px 12px;background:#ffffff09;color:#fff;text-decoration:none;font-weight:800;cursor:pointer}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160d1d}.pill{border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:.72rem}input,select,textarea{width:100%;border:1px solid var(--line);border-radius:10px;padding:10px;background:#080610;color:#fff;margin:5px 0}textarea{min-height:70px}.scores{display:grid;grid-template-columns:repeat(4,1fr);gap:7px}@media(max-width:760px){.grid,.scores{grid-template-columns:1fr 1fr}}@media(max-width:480px){.grid,.scores{grid-template-columns:1fr}}
"""

SCRIPT = """
async function api(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...opt});let d={};try{d=await r.json()}catch(_){ }if(!r.ok)throw new Error(d.detail||`Request failed (${r.status})`);return d}
async function createCandidate(){const g=id=>document.getElementById(id);const body={niche_key:g('niche').value,sub_niche:g('sub').value,audience:g('audience').value,promise:g('promise').value};for(const k of ['interest','expertise','content_depth','audience_clarity','live_fit','repeatability','distinctiveness'])body[k]=Number(g(k).value);try{await api('/command-center/api/niche-lab/candidates',{method:'POST',body:JSON.stringify(body)});location.reload()}catch(e){alert(e.message)}}
async function activate(id){if(!confirm('Make this your active ESP creator niche profile?'))return;try{await api('/command-center/api/niche-lab/candidates/'+encodeURIComponent(id)+'/activate',{method:'POST'});location.reload()}catch(e){alert(e.message)}}
"""


@router.get("/command-center/niche-lab", response_class=HTMLResponse, include_in_schema=False)
def niche_lab_page(request: Request):
    member = _require_creator(request)
    candidates = niche_lab.list_candidates(member.user_id)
    options = "".join(
        f"<option value='{escape(key, quote=True)}'>{escape(str(value.get('icon') or ''))} {escape(str(value.get('title') or key.title()))}</option>"
        for key, value in NICHE_CATALOG.items()
    )
    cards = "".join(
        "<article class='card'><div class='row'><div>"
        f"<span class='pill'>{escape(candidate['status'].upper())}</span><h2>{escape(candidate['catalog']['title'])}</h2>"
        f"<p class='muted'>{escape(candidate.get('sub_niche') or 'No sub-niche yet')} · audience: {escape(candidate.get('audience') or 'Not defined')}</p>"
        f"<p>{escape(candidate.get('promise') or 'Add a clear audience promise during testing.')}</p></div>"
        f"<div class='metric'><span class='muted'>Transparent score</span><b>{candidate['scorecard']['score']}/100</b><small class='muted'>{escape(candidate['scorecard']['band'].replace('_',' ').title())}</small></div></div>"
        f"<p class='muted'>Experiments complete: {candidate['experiment_progress']['completed']}/{candidate['experiment_progress']['total']}</p>"
        f"<button class='primary' onclick=\"activate('{escape(candidate['id'], quote=True)}')\">Select this niche</button></article>"
        for candidate in candidates
    ) or "<div class='card muted'>No candidate niches yet. Score one below and run the generated seven-day tests.</div>"
    score_fields = "".join(
        f"<label>{escape(label)} (1–5)</label><select id='{key}'>" + "".join(f"<option value='{n}' {'selected' if n==3 else ''}>{n}</option>" for n in range(1,6)) + "</select>"
        for key, label in (
            ("interest", "Interest / energy"), ("expertise", "Experience / credibility"),
            ("content_depth", "Depth of content ideas"), ("audience_clarity", "Audience clarity"),
            ("live_fit", "LIVE suitability"), ("repeatability", "Repeatability"),
            ("distinctiveness", "Distinctive angle"),
        )
    )
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<meta name='robots' content='noindex,nofollow'><title>ESP Niche Discovery Lab</title><style>{CSS}</style></head>"
        "<body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Creator OS</div>"
        "<h1>Niche Discovery Lab</h1><p class='muted'>Compare creator-fit candidates with transparent scoring, then test them in real content and LIVE formats before choosing.</p></div>"
        "<a class='btn' href='/command-center/dashboard'>Creator Dashboard</a></div>"
        f"{cards}<section class='card'><h2>Score a niche candidate</h2><label>Niche</label><select id='niche'>{options}</select>"
        "<label>Sub-niche / angle</label><input id='sub' placeholder='e.g. acoustic originals + audience requests'>"
        "<label>Audience</label><textarea id='audience' placeholder='Who should feel this content is specifically for them?'></textarea>"
        "<label>Creator promise</label><textarea id='promise' placeholder='What repeatable value, feeling or experience will viewers return for?'></textarea>"
        f"<div class='scores'>{score_fields}</div><button class='primary' onclick='createCandidate()'>Create candidate + seven-day tests</button></section>"
        "<section class='card'><b>How the score works</b><p class='muted'>The score is a weighted self-assessment, not a claim about current TikTok trends. Interest, expertise, content depth, audience clarity, LIVE fit and repeatability each carry 15 points; distinctiveness carries 10. Real creator-supplied evidence should decide what survives testing.</p></section>"
        f"<script>{SCRIPT}</script></main></body></html>",
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router", "CreatorNicheDiscoveryStore", "niche_lab", "SCORE_WEIGHTS"]
