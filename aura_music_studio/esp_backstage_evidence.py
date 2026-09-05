from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from .esp_command_center import EspStore, esp
from .esp_level_up import EspAgentAssignmentStore, assignments
from .esp_niche import require_esp_hub_member
from .esp_progress import EspProgressStore, save_progress_upload

router = APIRouter(tags=["ESP Agent Backstage Evidence"])
DIRECT_BACKSTAGE_ACCESS = False
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
STRUCTURED_SUFFIXES = {".csv", ".json", ".txt"}
VISUAL_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}

METRIC_ALIASES = {
    "views": {"views", "viewers", "total_views", "video_views"},
    "duration_minutes": {"duration_minutes", "live_minutes", "minutes", "duration_mins"},
    "avg_watch_seconds": {"avg_watch_seconds", "average_watch_seconds", "avg_watch_time", "average_watch_time"},
    "peak_viewers": {"peak_viewers", "peak_concurrent_viewers", "max_viewers"},
    "unique_viewers": {"unique_viewers", "unique_views", "unique_audience"},
    "new_followers": {"new_followers", "followers_gained", "follows", "new_follows"},
    "comments": {"comments", "comment_count"},
    "shares": {"shares", "share_count"},
    "likes": {"likes", "like_count"},
    "diamonds": {"diamonds", "diamond_count"},
    "gifters": {"gifters", "unique_gifters", "gift_users"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")[:120]


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text:
        return None
    try:
        number = float(text)
        return int(number) if number.is_integer() else number
    except ValueError:
        return None


def _canonical_metrics(values: dict | None) -> dict[str, int | float]:
    source = {_key(k): v for k, v in (values or {}).items()}
    result: dict[str, int | float] = {}
    for canonical, aliases in METRIC_ALIASES.items():
        for alias in aliases:
            if alias in source:
                value = _number(source[alias])
                if value is not None and value >= 0:
                    result[canonical] = value
                    break
    return result


def extract_structured_metrics(filename: str, content: bytes) -> tuple[dict, str]:
    """Parse transparent exports only; never claim screenshot/PDF OCR happened when it did not."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in STRUCTURED_SUFFIXES:
        return {}, "visual_review_required" if suffix in VISUAL_SUFFIXES else "manual_review_required"
    text = content.decode("utf-8-sig", errors="ignore")[:250_000]
    try:
        if suffix == ".json":
            payload = json.loads(text or "{}")
            if isinstance(payload, list):
                payload = next((row for row in payload if isinstance(row, dict)), {})
            if not isinstance(payload, dict):
                payload = {}
            values = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else payload
        elif suffix == ".csv":
            values = next(
                (row for row in csv.DictReader(io.StringIO(text)) if any(str(v or "").strip() for v in row.values())),
                {},
            )
        else:
            values = {}
            for line in text.splitlines():
                separator = ":" if ":" in line else "=" if "=" in line else None
                if separator:
                    key, value = line.split(separator, 1)
                    values[key.strip()] = value.strip()
        metrics = _canonical_metrics(values)
    except (ValueError, csv.Error):
        return {}, "structured_parse_failed"
    return metrics, "structured_extracted" if metrics else "structured_no_known_metrics"


def _freshness(captured_at: str | None, created_at: str | None = None) -> dict:
    raw = (captured_at or created_at or "").strip()
    if not raw:
        return {"status": "unknown", "age_days": None}
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = max(0, int((datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() // 86400))
    except ValueError:
        return {"status": "unknown", "age_days": None}
    return {
        "status": "current" if age <= 7 else "update_due" if age <= 14 else "stale",
        "age_days": age,
    }


def _trend(current: dict, previous: dict | None) -> dict:
    prior = (previous or {}).get("metrics") or {}
    trend = {}
    for key, value in current.items():
        now_value, before = _number(value), _number(prior.get(key))
        if now_value is not None and before is not None:
            trend[key] = {"current": now_value, "previous": before, "delta": round(now_value - before, 2)}
    return trend


class BackstageEvidenceStore:
    def __init__(
        self,
        esp_store: EspStore | None = None,
        assignment_store: EspAgentAssignmentStore | None = None,
        progress_store: EspProgressStore | None = None,
    ):
        self.esp = esp_store or esp
        self.assignments = assignment_store or assignments
        self.progress = progress_store or EspProgressStore(self.esp)
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
                CREATE TABLE IF NOT EXISTS esp_backstage_evidence (
                    id TEXT PRIMARY KEY,
                    creator_user_id TEXT NOT NULL,
                    uploaded_by_user_id TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_label TEXT NOT NULL DEFAULT '',
                    captured_at TEXT,
                    period_label TEXT NOT NULL DEFAULT '',
                    upload_name TEXT,
                    upload_path TEXT,
                    upload_content_type TEXT,
                    extraction_status TEXT NOT NULL DEFAULT 'manual_review_required',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    guidance_json TEXT NOT NULL DEFAULT '[]',
                    progress_submission_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(creator_user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(uploaded_by_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_backstage_creator_created
                    ON esp_backstage_evidence(creator_user_id,created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_backstage_agent_created
                    ON esp_backstage_evidence(uploaded_by_user_id,created_at DESC);
                """
            )

    def _active_assignment(self, agent_user_id: str, creator_user_id: str) -> bool:
        with self._connect() as con:
            row = con.execute(
                "SELECT 1 FROM esp_agent_creator_assignments WHERE agent_user_id=? AND creator_user_id=? AND status='active'",
                (agent_user_id, creator_user_id),
            ).fetchone()
        return row is not None

    def _active_creator(self, creator_user_id: str) -> bool:
        membership = self.esp.membership(creator_user_id)
        if not membership or membership.get("status") not in {"active", "owner"}:
            return False
        return membership.get("status") == "owner" or (membership.get("roles") or "").lower() in {"creator", "both"}

    def assert_authorized(self, actor_user_id: str, creator_user_id: str, *, owner: bool = False) -> None:
        if not self._active_creator(creator_user_id):
            raise ValueError("Creator does not have active ESP Creator access")
        if not owner and not self._active_assignment(actor_user_id, creator_user_id):
            raise PermissionError("Creator is not actively assigned to this agent")

    def record(
        self,
        actor_user_id: str,
        creator_user_id: str,
        *,
        owner: bool = False,
        source_kind: str,
        source_label: str = "",
        captured_at: str | None = None,
        period_label: str = "",
        metrics: dict | None = None,
        extraction_status: str = "manual_review_required",
        upload_name: str | None = None,
        upload_path: str | None = None,
        upload_content_type: str | None = None,
    ) -> dict:
        self.assert_authorized(actor_user_id, creator_user_id, owner=owner)
        kind = (source_kind or "").strip().lower()
        if kind not in {"screenshot", "export", "manual"}:
            raise ValueError("Evidence source must be screenshot, export or manual")
        clean_metrics = _canonical_metrics(metrics)
        guidance = self.progress.guidance(creator_user_id, "live", clean_metrics) if clean_metrics else []
        progress_id = None
        if clean_metrics:
            progress_row = self.progress.add(
                creator_user_id,
                kind="live",
                period_label=period_label or "Agent-uploaded TikTok LIVE evidence",
                metrics=clean_metrics,
                notes="Agent/owner supplied ESP mentoring evidence; not a direct TikTok LIVE Backstage connection.",
                upload_name=upload_name,
                upload_path=upload_path,
                upload_content_type=upload_content_type,
            )
            progress_id = progress_row.get("id")
        row_id, created_at = uuid4().hex, _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_backstage_evidence
                   (id,creator_user_id,uploaded_by_user_id,source_kind,source_label,captured_at,period_label,
                    upload_name,upload_path,upload_content_type,extraction_status,metrics_json,guidance_json,
                    progress_submission_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row_id, creator_user_id, actor_user_id, kind, (source_label or "")[:240],
                    (captured_at or "")[:80] or None, (period_label or "")[:160], upload_name, upload_path,
                    upload_content_type, (extraction_status or "manual_review_required")[:80],
                    json.dumps(clean_metrics, sort_keys=True), json.dumps(guidance), progress_id, created_at,
                ),
            )
        return self.get(row_id, actor_user_id=actor_user_id, owner=owner)

    def get(self, evidence_id: str, *, actor_user_id: str, owner: bool = False) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_backstage_evidence WHERE id=?", (evidence_id,)).fetchone()
        if not row:
            raise KeyError(evidence_id)
        item = self._decode(row)
        self.assert_authorized(actor_user_id, item["creator_user_id"], owner=owner)
        return item

    def list_for_creator(self, actor_user_id: str, creator_user_id: str, *, owner: bool = False, limit: int = 100) -> list[dict]:
        self.assert_authorized(actor_user_id, creator_user_id, owner=owner)
        with self._connect() as con:
            rows = con.execute(
                """SELECT e.*,u.display_name uploader_name FROM esp_backstage_evidence e
                   LEFT JOIN users u ON u.id=e.uploaded_by_user_id
                   WHERE e.creator_user_id=? ORDER BY e.created_at DESC LIMIT ?""",
                (creator_user_id, max(1, min(int(limit), 500))),
            ).fetchall()
        result = [self._decode(row) for row in rows]
        for index, item in enumerate(result):
            item["trend"] = _trend(item.get("metrics") or {}, result[index + 1] if index + 1 < len(result) else None)
        return result

    def queue(self, actor_user_id: str, *, owner: bool = False) -> list[dict]:
        if owner:
            with self._connect() as con:
                rows = con.execute(
                    "SELECT user_id FROM esp_memberships WHERE status IN ('active','owner') AND (roles IN ('creator','both') OR status='owner')"
                ).fetchall()
            creator_ids = [row["user_id"] for row in rows]
        else:
            creator_ids = [row["creator_user_id"] for row in self.assignments.for_agent(actor_user_id)]
        queue = []
        with self._connect() as con:
            for creator_id in creator_ids:
                user = con.execute(
                    """SELECT u.display_name,m.tiktok_handle,m.region FROM users u
                       JOIN esp_memberships m ON m.user_id=u.id WHERE u.id=?""",
                    (creator_id,),
                ).fetchone()
                latest = con.execute(
                    "SELECT * FROM esp_backstage_evidence WHERE creator_user_id=? ORDER BY created_at DESC LIMIT 1",
                    (creator_id,),
                ).fetchone()
                latest_item = self._decode(latest) if latest else None
                queue.append({
                    "creator_user_id": creator_id,
                    "display_name": user["display_name"] if user else "Creator",
                    "tiktok_handle": user["tiktok_handle"] if user else "",
                    "region": user["region"] if user else "",
                    "latest": latest_item,
                    "freshness": _freshness(
                        (latest_item or {}).get("captured_at"), (latest_item or {}).get("created_at")
                    ) if latest_item else {"status": "missing", "age_days": None},
                })
        rank = {"missing": 0, "stale": 1, "update_due": 2, "unknown": 3, "current": 4}
        queue.sort(key=lambda row: (rank.get(row["freshness"]["status"], 9), row["display_name"].lower()))
        return queue

    @staticmethod
    def _decode(row) -> dict:
        item = dict(row)
        try:
            item["metrics"] = json.loads(item.pop("metrics_json") or "{}")
        except Exception:
            item["metrics"] = {}
        try:
            item["guidance"] = json.loads(item.pop("guidance_json") or "[]")
        except Exception:
            item["guidance"] = []
        item["freshness"] = _freshness(item.get("captured_at"), item.get("created_at"))
        item["direct_backstage_access"] = False
        item.pop("upload_path", None)
        return item


backstage_evidence = BackstageEvidenceStore()


def _require_agent(request: Request):
    member, membership = require_esp_hub_member(request)
    role = "owner" if membership.get("status") == "owner" else (membership.get("roles") or "").lower()
    if role not in {"agent", "both", "owner"}:
        raise HTTPException(403, "ESP Agent access is required")
    return member, membership, role == "owner"


@router.get("/command-center/api/agent/backstage-evidence")
def backstage_queue_api(request: Request):
    member, _membership, owner = _require_agent(request)
    return {
        "direct_backstage_access": False,
        "data_source": "member_or_agent_supplied_exports_and_screenshots",
        "queue": backstage_evidence.queue(member.user_id, owner=owner),
    }


@router.get("/command-center/api/agent/backstage-evidence/{creator_user_id}")
def creator_backstage_history(creator_user_id: str, request: Request):
    member, _membership, owner = _require_agent(request)
    try:
        rows = backstage_evidence.list_for_creator(member.user_id, creator_user_id, owner=owner)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"creator_user_id": creator_user_id, "direct_backstage_access": False, "evidence": rows}


CSS = """
:root{--line:#ffffff1d;--muted:#c1bfd0;--gold:#efc86f;--violet:#9f70ff;--good:#74dda5;--warn:#ffd37a;--bad:#ff8fa3}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 88% 0,#42175b,transparent 30%),#06050c;color:#fff;font-family:Inter,ui-sans-serif,system-ui,sans-serif}.wrap{width:min(1200px,calc(100% - 28px));margin:auto;padding:36px 0 60px}a{color:inherit;text-decoration:none}.top,.row{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap}.eyebrow{font-size:.7rem;letter-spacing:.15em;text-transform:uppercase;color:var(--gold);font-weight:950}.card{border:1px solid var(--line);border-radius:17px;background:#14101ceb;padding:15px;margin:10px 0}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}.btn,button{border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#ffffff08;color:#fff;font-weight:850;cursor:pointer}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160d1d}.muted{color:var(--muted);line-height:1.55}.pill{border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:.78rem}.current{color:var(--good)}.update_due{color:var(--warn)}.stale,.missing{color:var(--bad)}input,select{width:100%;border:1px solid var(--line);border-radius:10px;background:#09070f;color:#fff;padding:9px;margin:5px 0 10px}label{font-weight:800;font-size:.86rem}@media(max-width:760px){.grid{grid-template-columns:1fr}}
"""


def _metric_fields() -> str:
    fields = [
        ("views", "Views"), ("duration_minutes", "Duration minutes"), ("avg_watch_seconds", "Avg watch seconds"),
        ("peak_viewers", "Peak viewers"), ("unique_viewers", "Unique viewers"), ("new_followers", "New followers"),
        ("comments", "Comments"), ("shares", "Shares"), ("likes", "Likes"), ("diamonds", "Diamonds"), ("gifters", "Gifters"),
    ]
    return "".join(f"<div><label>{escape(label)}</label><input type='number' min='0' step='.01' name='{name}'></div>" for name, label in fields)


@router.get("/command-center/agent/backstage-evidence", response_class=HTMLResponse, include_in_schema=False)
def backstage_evidence_page(request: Request):
    member, _membership, owner = _require_agent(request)
    queue = backstage_evidence.queue(member.user_id, owner=owner)
    options = "".join(
        f"<option value='{escape(row['creator_user_id'])}'>@{escape(row.get('tiktok_handle') or '')} — {escape(row.get('display_name') or 'Creator')}</option>"
        for row in queue
    )
    cards = "".join(
        f"<article class='card'><div class='row'><div><b>{escape(row.get('display_name') or 'Creator')}</b><div class='muted'>@{escape(row.get('tiktok_handle') or '')} · {escape(row.get('region') or '')}</div></div><span class='pill {escape(row['freshness']['status'])}'>{escape(row['freshness']['status'].replace('_',' ').title())}</span></div><p class='muted'>{'No Backstage evidence uploaded yet.' if not row.get('latest') else 'Latest evidence: ' + escape((row['latest'].get('captured_at') or row['latest'].get('created_at') or '')[:10])}</p><a class='btn' href='/command-center/api/agent/backstage-evidence/{escape(row['creator_user_id'])}'>JSON history</a></article>"
        for row in queue
    ) or "<div class='card muted'>No creators are currently assigned to this Agent account.</div>"
    return HTMLResponse(
        f"""<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Backstage Evidence</title><style>{CSS}</style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Agent OS</div><h1>Backstage Evidence & Creator Analysis</h1><p class='muted'>Pulsar-Frequency House does <b>not</b> have direct access to TikTok LIVE Backstage. Upload authorised Manage Creator/Backstage exports or screenshots and confirm the metrics used for mentoring.</p></div><a class='btn' href='/command-center/level-up'>Level Up Hub</a></div><section class='card'><h2>Add creator evidence</h2><form method='post' enctype='multipart/form-data'><label>Assigned creator</label><select name='creator_user_id' required>{options}</select><div class='grid'><div><label>Evidence type</label><select name='source_kind'><option value='export'>Export</option><option value='screenshot'>Screenshot/PDF</option><option value='manual'>Manual entry</option></select></div><div><label>Data captured date/time</label><input type='datetime-local' name='captured_at'></div><div><label>Period label</label><input name='period_label' placeholder='Example: 26 Aug evening LIVE'></div></div><label>Source label</label><input name='source_label' placeholder='Example: Manage Creator — LIVE overview'><label>Upload export/screenshot</label><input type='file' name='analysis_file' accept='.csv,.json,.txt,.pdf,.png,.jpg,.jpeg,.webp'><p class='muted'>CSV/JSON/TXT can be parsed for recognised metrics. Images/PDFs are retained for visual/human extraction; the site will not pretend OCR succeeded. Manually entered values override parsed values.</p><div class='grid'>{_metric_fields()}</div><button class='primary' type='submit'>Save evidence & mentoring analysis</button></form></section><h2>Creator data freshness queue</h2>{cards}</main></body></html>""",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/command-center/agent/backstage-evidence", include_in_schema=False)
async def save_backstage_evidence(
    request: Request,
    creator_user_id: str = Form(...), source_kind: str = Form("export"), source_label: str = Form(""),
    captured_at: str = Form(""), period_label: str = Form(""),
    views: str = Form(""), duration_minutes: str = Form(""), avg_watch_seconds: str = Form(""),
    peak_viewers: str = Form(""), unique_viewers: str = Form(""), new_followers: str = Form(""),
    comments: str = Form(""), shares: str = Form(""), likes: str = Form(""), diamonds: str = Form(""),
    gifters: str = Form(""), analysis_file: UploadFile | None = File(None),
):
    member, _membership, owner = _require_agent(request)
    # Authorize BEFORE reading/saving bytes so an Agent can never write into an unassigned creator namespace.
    try:
        backstage_evidence.assert_authorized(member.user_id, creator_user_id, owner=owner)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    manual = _canonical_metrics({
        "views": views, "duration_minutes": duration_minutes, "avg_watch_seconds": avg_watch_seconds,
        "peak_viewers": peak_viewers, "unique_viewers": unique_viewers, "new_followers": new_followers,
        "comments": comments, "shares": shares, "likes": likes, "diamonds": diamonds, "gifters": gifters,
    })
    upload_name = upload_path = upload_type = None
    parsed = {}
    extraction_status = "manual_metrics" if manual else "manual_review_required"
    if analysis_file and analysis_file.filename:
        content = await analysis_file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, "Backstage evidence upload must be 10 MB or smaller")
        try:
            upload_name, upload_path = save_progress_upload(creator_user_id, analysis_file.filename, content)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        upload_type = analysis_file.content_type
        parsed, extraction_status = extract_structured_metrics(analysis_file.filename, content)
    metrics = {**parsed, **manual}
    if manual and parsed:
        extraction_status = "structured_extracted_with_manual_confirmation"
    backstage_evidence.record(
        member.user_id, creator_user_id, owner=owner, source_kind=source_kind, source_label=source_label,
        captured_at=captured_at or None, period_label=period_label, metrics=metrics,
        extraction_status=extraction_status, upload_name=upload_name, upload_path=upload_path,
        upload_content_type=upload_type,
    )
    return RedirectResponse("/command-center/agent/backstage-evidence", status_code=303)


__all__ = ["router", "BackstageEvidenceStore", "backstage_evidence", "extract_structured_metrics", "DIRECT_BACKSTAGE_ACCESS"]
