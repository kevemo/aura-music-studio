from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_command_center import EspStore, esp
from .esp_creator_plan import CreatorPlanStore, _require_creator, plans
from .esp_niche import EspNicheStore
from .esp_progress import EspProgressStore

router = APIRouter(tags=["ESP Creator 30 60 90 Reviews"])
ReviewPoint = Literal[30, 60, 90]
SNAPSHOT_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: dict) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class ReviewRequest(BaseModel):
    review_day: ReviewPoint
    wins: str = Field(default="", max_length=3000)
    challenges: str = Field(default="", max_length=3000)
    next_focus: str = Field(default="", max_length=2000)


class CreatorReviewStore:
    """Append-only 30/60/90 creator milestone snapshots.

    There are intentionally no update/delete methods or routes. Each milestone can be captured
    once for a creator and includes a hash of the stored snapshot so accidental or manual data
    changes can be detected when it is read back.
    """

    def __init__(self, esp_store: EspStore | None = None, plan_store: CreatorPlanStore | None = None):
        self.esp = esp_store or esp
        self.db_path = self.esp.db_path
        self.plans = plan_store or plans
        self.niches = EspNicheStore(self.esp)
        self.progress = EspProgressStore(self.esp)
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
                CREATE TABLE IF NOT EXISTS esp_creator_reviews (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    review_day INTEGER NOT NULL CHECK(review_day IN (30,60,90)),
                    wins TEXT NOT NULL DEFAULT '',
                    challenges TEXT NOT NULL DEFAULT '',
                    next_focus TEXT NOT NULL DEFAULT '',
                    snapshot_version INTEGER NOT NULL DEFAULT 1,
                    snapshot_json TEXT NOT NULL,
                    snapshot_sha256 TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    UNIQUE(user_id,review_day)
                );
                CREATE INDEX IF NOT EXISTS idx_creator_reviews_user
                    ON esp_creator_reviews(user_id,review_day,created_at);
                """
            )

    def snapshot(self, user_id: str) -> dict:
        plan = self.plans.ensure(user_id)
        profile = self.niches.get(user_id)
        progress = self.progress.summary(user_id)
        training = self.esp.progress(user_id)
        actions = list(plan.get("actions") or [])
        action_evidence = [
            {
                "day_number": row.get("day_number"),
                "category": row.get("category"),
                "title": row.get("title"),
                "status": row.get("status"),
                "evidence_note": row.get("evidence_note") or "",
                "completed_at": row.get("completed_at"),
            }
            for row in actions
        ]
        return {
            "snapshot_version": SNAPSHOT_VERSION,
            "plan": {
                "phase": plan.get("phase"),
                "monthly_objective": plan.get("monthly_objective"),
                "live_days_target": plan.get("live_days_target"),
                "live_hours_target": plan.get("live_hours_target"),
                "videos_per_week": plan.get("videos_per_week"),
                "engagement_minutes_daily": plan.get("engagement_minutes_daily"),
                "completion": plan.get("completion"),
                "action_evidence": action_evidence,
            },
            "niche": {
                "niche": (profile or {}).get("niche"),
                "sub_niche": (profile or {}).get("sub_niche"),
                "audience": (profile or {}).get("audience"),
                "goals": list((profile or {}).get("goals") or []),
            },
            "progress": progress,
            "training": dict(training or {}),
            "captured_at": _now(),
        }

    def create(
        self,
        user_id: str,
        *,
        review_day: int,
        wins: str = "",
        challenges: str = "",
        next_focus: str = "",
        actor: str = "ESP Creator",
    ) -> dict:
        if int(review_day) not in {30, 60, 90}:
            raise ValueError("Review point must be 30, 60 or 90 days")
        snapshot = self.snapshot(user_id)
        canonical = _canonical_json(snapshot)
        row = {
            "id": uuid4().hex,
            "user_id": user_id,
            "review_day": int(review_day),
            "wins": (wins or "").strip()[:3000],
            "challenges": (challenges or "").strip()[:3000],
            "next_focus": (next_focus or "").strip()[:2000],
            "snapshot_version": SNAPSHOT_VERSION,
            "snapshot_json": canonical,
            "snapshot_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "created_by": (actor or "ESP Creator")[:120],
            "created_at": _now(),
        }
        try:
            with self._connect() as con:
                con.execute(
                    """INSERT INTO esp_creator_reviews
                       (id,user_id,review_day,wins,challenges,next_focus,snapshot_version,snapshot_json,snapshot_sha256,created_by,created_at)
                       VALUES (:id,:user_id,:review_day,:wins,:challenges,:next_focus,:snapshot_version,:snapshot_json,:snapshot_sha256,:created_by,:created_at)""",
                    row,
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                raise FileExistsError(f"The {review_day}-day review has already been captured") from exc
            raise
        return self.get(row["id"], user_id)

    def get(self, review_id: str, user_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM esp_creator_reviews WHERE id=? AND user_id=?",
                (review_id, user_id),
            ).fetchone()
        if row is None:
            raise KeyError(review_id)
        item = dict(row)
        raw = item.pop("snapshot_json") or "{}"
        try:
            snapshot = json.loads(raw)
        except Exception:
            snapshot = {}
        item["snapshot"] = snapshot
        item["snapshot_integrity"] = _digest(snapshot) == item.get("snapshot_sha256")
        return item

    def list(self, user_id: str) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT id FROM esp_creator_reviews WHERE user_id=? ORDER BY review_day,created_at",
                (user_id,),
            ).fetchall()
        return [self.get(row["id"], user_id) for row in rows]

    def comparison(self, user_id: str) -> dict:
        timeline: list[dict] = []
        previous: dict | None = None
        for row in self.list(user_id):
            snapshot = row.get("snapshot") or {}
            plan = snapshot.get("plan") or {}
            progress = snapshot.get("progress") or {}
            training = snapshot.get("training") or {}
            avg_training = (
                round(sum(float(value or 0) for value in training.values()) / len(training), 1)
                if training
                else 0.0
            )
            point = {
                "id": row["id"],
                "review_day": row["review_day"],
                "created_at": row["created_at"],
                "plan_completion": float((plan.get("completion") or {}).get("percent") or 0),
                "progress_submissions": int(progress.get("total") or 0),
                "training_average": avg_training,
                "next_focus": row.get("next_focus") or "",
                "snapshot_integrity": bool(row.get("snapshot_integrity")),
                "delta": None,
            }
            if previous is not None:
                point["delta"] = {
                    "plan_completion": round(point["plan_completion"] - previous["plan_completion"], 1),
                    "progress_submissions": point["progress_submissions"] - previous["progress_submissions"],
                    "training_average": round(point["training_average"] - previous["training_average"], 1),
                }
            timeline.append(point)
            previous = point
        captured = {row["review_day"] for row in timeline}
        return {
            "timeline": timeline,
            "review_count": len(timeline),
            "captured_milestones": sorted(captured),
            "remaining_milestones": [day for day in (30, 60, 90) if day not in captured],
            "historical_snapshots_immutable": True,
            "integrity_verified": all(row["snapshot_integrity"] for row in timeline),
        }


reviews = CreatorReviewStore()


@router.get("/command-center/api/my-plan/reviews")
def review_list_api(request: Request):
    member, _membership = _require_creator(request)
    return {"reviews": reviews.list(member.user_id), "comparison": reviews.comparison(member.user_id)}


@router.post("/command-center/api/my-plan/reviews")
def create_review_api(body: ReviewRequest, request: Request):
    member, _membership = _require_creator(request)
    try:
        review = reviews.create(
            member.user_id,
            review_day=body.review_day,
            wins=body.wins,
            challenges=body.challenges,
            next_focus=body.next_focus,
            actor=member.user_id,
        )
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"review": review, "comparison": reviews.comparison(member.user_id)}


PAGE = r"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP 30/60/90 Reviews</title><style>
:root{--line:#ffffff20;--gold:#efc86f;--muted:#c3bfd2;--violet:#9d70ff;--good:#74dda5;--bad:#ff91a6}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 85% 0,#43175c,transparent 31%),#07050d;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1080px,calc(100% - 28px));margin:auto;padding:35px 0}.card{border:1px solid var(--line);border-radius:16px;background:#14101d;padding:15px;margin:10px 0}.field,.btn{width:100%;padding:10px;border-radius:10px;border:1px solid var(--line);background:#09070f;color:#fff;margin:5px 0;font:inherit}.btn{width:auto;cursor:pointer;font-weight:850;text-decoration:none}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160d1d}.gold{color:var(--gold)}.muted{color:var(--muted);line-height:1.5}.grid{display:grid;grid-template-columns:.8fr 1.2fr;gap:12px}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.metric{border:1px solid var(--line);border-radius:11px;padding:8px}.metric b{display:block;font-size:1.2rem}.good{color:var(--good)}.bad{color:var(--bad)}.notice{display:none;border:1px solid var(--line);border-radius:10px;padding:10px}.notice.show{display:block}@media(max-width:800px){.grid,.metrics{grid-template-columns:1fr}}</style></head><body><main class='wrap'>
<p class='gold'><b>ELEVATE SOULS PRODUCTIONS · CREATOR OS</b></p><h1>30 / 60 / 90 Reviews</h1><p class='muted'>Each milestone captures a read-only snapshot of My Plan, action evidence, niche, training and Creator Progress. Snapshot integrity is verified whenever the history is loaded.</p><p><a class='btn' href='/command-center/my-plan'>My Plan</a> <a class='btn' href='/command-center/progress'>Progress</a> <a class='btn' href='/command-center/level-up'>Level Up Hub</a></p><div id='notice' class='notice'></div>
<section class='grid'><div class='card'><select id='day' class='field'><option value='30'>30-day review</option><option value='60'>60-day review</option><option value='90'>90-day review</option></select><textarea id='wins' class='field' placeholder='Wins'></textarea><textarea id='challenges' class='field' placeholder='Challenges'></textarea><textarea id='focus' class='field' placeholder='Single next focus'></textarea><button id='save' class='btn primary'>Capture immutable review</button><p id='remaining' class='muted'></p></div><div id='history'><div class='card muted'>Loading…</div></div></section>
</main><script>
const API='/command-center/api/my-plan/reviews',$=id=>document.getElementById(id);function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function note(m,b=false){const n=$('notice');n.textContent=m;n.className='notice show';n.style.borderColor=b?'var(--bad)':''}async function api(o={}){const r=await fetch(API,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...o});let d={};try{d=await r.json()}catch(_){}if(!r.ok)throw new Error(d.detail||`Request failed (${r.status})`);return d}
function render(d){const comp=d.comparison||{};$('remaining').textContent='Remaining milestones: '+((comp.remaining_milestones||[]).join(' / ')||'All captured');$('history').innerHTML=(d.reviews||[]).map(r=>{const p=r.snapshot?.plan||{},pr=r.snapshot?.progress||{};const point=(comp.timeline||[]).find(x=>x.id===r.id)||{};const delta=point.delta;return `<article class="card"><b>${r.review_day}-day · ${esc((r.created_at||'').slice(0,10))}</b> <span class="${r.snapshot_integrity?'good':'bad'}">${r.snapshot_integrity?'Integrity verified':'Integrity warning'}</span><div class="metrics"><div class="metric"><span class="muted">Plan</span><b>${p.completion?.percent||0}%</b>${delta?`<small>${delta.plan_completion>=0?'+':''}${delta.plan_completion}%</small>`:''}</div><div class="metric"><span class="muted">Progress uploads</span><b>${pr.total||0}</b>${delta?`<small>${delta.progress_submissions>=0?'+':''}${delta.progress_submissions}</small>`:''}</div><div class="metric"><span class="muted">Training</span><b>${point.training_average||0}%</b>${delta?`<small>${delta.training_average>=0?'+':''}${delta.training_average}%</small>`:''}</div></div><p><b>Wins:</b> ${esc(r.wins||'—')}</p><p><b>Challenges:</b> ${esc(r.challenges||'—')}</p><p><b>Next focus:</b> ${esc(r.next_focus||'—')}</p></article>`}).join('')||'<div class="card muted">No milestone reviews captured yet.</div>'}
async function load(){try{render(await api())}catch(e){note(e.message,true)}}$('save').onclick=async()=>{try{await api({method:'POST',body:JSON.stringify({review_day:Number($('day').value),wins:$('wins').value,challenges:$('challenges').value,next_focus:$('focus').value})});$('wins').value='';$('challenges').value='';$('focus').value='';note('Milestone snapshot captured.');await load()}catch(e){note(e.message,true)}};load();
</script></body></html>"""


@router.get("/command-center/my-plan/reviews", response_class=HTMLResponse, include_in_schema=False)
def reviews_page(request: Request):
    _require_creator(request)
    return HTMLResponse(PAGE, headers={"Cache-Control": "no-store"})


__all__ = ["router", "CreatorReviewStore", "reviews", "SNAPSHOT_VERSION"]
