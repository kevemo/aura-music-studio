from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from datetime import datetime, timezone
from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .esp_agent_development_planner import AgentDevelopmentStore, development
from .esp_backstage_evidence import BackstageEvidenceStore, backstage_evidence
from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["ESP Agent Creator Reports"])

CSV_FIELDS = (
    "record_type",
    "record_id",
    "parent_id",
    "period_or_horizon",
    "status",
    "title",
    "category",
    "metric",
    "current_value",
    "previous_value",
    "delta",
    "target_value",
    "notes",
    "captured_or_due_at",
    "created_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "creator").strip().lower()).strip("-")
    return slug[:80] or "creator"


def _csv_safe(value):
    """Prevent spreadsheet formula injection from user-controlled labels and notes."""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _require_agent_or_owner(request: Request):
    member, membership = require_esp_hub_member(request)
    role = "owner" if membership.get("status") == "owner" else (membership.get("roles") or "").lower()
    if role not in {"agent", "both", "owner"}:
        raise HTTPException(403, "ESP Agent access is required")
    return member, role == "owner"


class AgentReportStore:
    """Build export-safe mentoring packs for explicitly authorised ESP creator relationships."""

    def __init__(
        self,
        evidence_store: BackstageEvidenceStore | None = None,
        development_store: AgentDevelopmentStore | None = None,
        db_path: str | None = None,
    ):
        self.evidence = evidence_store or backstage_evidence
        self.development = development_store or development
        self.db_path = db_path or self.evidence.db_path

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def _creator(self, creator_user_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                """SELECT u.id user_id,u.display_name,m.tiktok_handle,m.region,m.roles,m.status
                   FROM users u JOIN esp_memberships m ON m.user_id=u.id WHERE u.id=?""",
                (creator_user_id,),
            ).fetchone()
        if not row:
            raise KeyError(creator_user_id)
        return dict(row)

    @staticmethod
    def _safe_evidence(item: dict) -> dict:
        allowed = {
            "id", "source_kind", "source_label", "captured_at", "period_label", "upload_name",
            "upload_content_type", "extraction_status", "metrics", "guidance", "created_at",
            "freshness", "trend", "direct_backstage_access",
        }
        return {key: item.get(key) for key in allowed if key in item}

    @staticmethod
    def _safe_plan(plan: dict) -> dict:
        milestones = []
        for row in plan.get("milestones") or []:
            milestones.append({
                key: row.get(key)
                for key in (
                    "id", "horizon_days", "category", "title", "detail", "target_metric",
                    "baseline_value", "target_value", "due_at", "status", "evidence_note",
                    "created_at", "updated_at", "completed_at",
                )
            })
        reviews = []
        for row in plan.get("reviews") or []:
            reviews.append({
                key: row.get(key)
                for key in ("id", "metrics", "evidence_id", "notes", "created_at")
            })
        return {
            "id": plan.get("id"),
            "objective": plan.get("objective"),
            "notes": plan.get("notes"),
            "status": plan.get("status"),
            "outcome": plan.get("outcome"),
            "baseline_metrics": plan.get("baseline_metrics") or {},
            "baseline_evidence_id": plan.get("baseline_evidence_id"),
            "created_at": plan.get("created_at"),
            "updated_at": plan.get("updated_at"),
            "completed_at": plan.get("completed_at"),
            "completion": plan.get("completion") or {"done": 0, "total": 0, "percent": 0.0},
            "focus_suggestions": plan.get("focus_suggestions") or [],
            "milestones": milestones,
            "reviews": reviews,
            "direct_backstage_access": False,
            "automatic_penalties": False,
        }

    def list_creators(self, actor_user_id: str, *, owner: bool) -> list[dict]:
        rows = self.evidence.queue(actor_user_id, owner=owner)
        return [
            {
                "creator_user_id": row.get("creator_user_id"),
                "display_name": row.get("display_name"),
                "tiktok_handle": row.get("tiktok_handle"),
                "region": row.get("region"),
                "freshness": row.get("freshness"),
                "latest_evidence_id": (row.get("latest") or {}).get("id"),
            }
            for row in rows
        ]

    def build_pack(self, actor_user_id: str, creator_user_id: str, *, owner: bool) -> dict:
        self.evidence.assert_authorized(actor_user_id, creator_user_id, owner=owner)
        creator = self._creator(creator_user_id)
        history = self.evidence.list_for_creator(actor_user_id, creator_user_id, owner=owner, limit=500)
        evidence_rows = [self._safe_evidence(row) for row in history]
        plans = [
            self._safe_plan(plan)
            for plan in self.development.plans_for_actor(actor_user_id, owner=owner)
            if plan.get("creator_user_id") == creator_user_id
        ]
        latest = evidence_rows[0] if evidence_rows else None
        active_plans = sum(1 for plan in plans if plan.get("status") == "active")
        open_milestones = sum(
            1
            for plan in plans
            for milestone in plan.get("milestones") or []
            if milestone.get("status") != "done"
        )
        return {
            "report_version": 1,
            "generated_at": _now(),
            "creator": {
                "user_id": creator.get("user_id"),
                "display_name": creator.get("display_name"),
                "tiktok_handle": creator.get("tiktok_handle") or "",
                "region": creator.get("region") or "",
                "esp_role": creator.get("roles") or "creator",
            },
            "summary": {
                "evidence_records": len(evidence_rows),
                "latest_evidence_at": (latest or {}).get("captured_at") or (latest or {}).get("created_at"),
                "latest_freshness": (latest or {}).get("freshness") or {"status": "missing", "age_days": None},
                "latest_metrics": (latest or {}).get("metrics") or {},
                "latest_trend": (latest or {}).get("trend") or {},
                "development_plans": len(plans),
                "active_development_plans": active_plans,
                "open_milestones": open_milestones,
            },
            "evidence": evidence_rows,
            "development_plans": plans,
            "boundaries": {
                "direct_tiktok_backstage_access": False,
                "evidence_source": "authorised_member_or_agent_supplied_data",
                "raw_screenshot_bytes_included": False,
                "server_file_paths_included": False,
                "private_creative_projects_included": False,
                "automatic_penalties_or_role_changes": False,
                "assignment_gated": not owner,
            },
        }

    @staticmethod
    def to_csv(pack: dict) -> str:
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()

        creator = pack.get("creator") or {}
        summary = pack.get("summary") or {}
        writer.writerow({
            "record_type": "summary",
            "record_id": creator.get("user_id"),
            "status": (summary.get("latest_freshness") or {}).get("status", "missing"),
            "title": _csv_safe(creator.get("display_name") or "Creator mentoring report"),
            "notes": _csv_safe(
                f"Evidence records: {summary.get('evidence_records', 0)}; "
                f"development plans: {summary.get('development_plans', 0)}; "
                f"open milestones: {summary.get('open_milestones', 0)}"
            ),
            "captured_or_due_at": summary.get("latest_evidence_at") or "",
            "created_at": pack.get("generated_at") or "",
        })

        for evidence in pack.get("evidence") or []:
            metrics = evidence.get("metrics") or {}
            trends = evidence.get("trend") or {}
            if not metrics:
                writer.writerow({
                    "record_type": "evidence",
                    "record_id": evidence.get("id"),
                    "period_or_horizon": _csv_safe(evidence.get("period_label") or ""),
                    "status": evidence.get("extraction_status") or "",
                    "title": _csv_safe(evidence.get("source_label") or evidence.get("source_kind") or "Evidence"),
                    "notes": _csv_safe("No confirmed structured metrics in this evidence record."),
                    "captured_or_due_at": evidence.get("captured_at") or "",
                    "created_at": evidence.get("created_at") or "",
                })
            for metric, value in metrics.items():
                trend = trends.get(metric) or {}
                writer.writerow({
                    "record_type": "evidence_metric",
                    "record_id": evidence.get("id"),
                    "period_or_horizon": _csv_safe(evidence.get("period_label") or ""),
                    "status": evidence.get("extraction_status") or "",
                    "title": _csv_safe(evidence.get("source_label") or evidence.get("source_kind") or "Evidence"),
                    "metric": metric,
                    "current_value": value,
                    "previous_value": trend.get("previous", ""),
                    "delta": trend.get("delta", ""),
                    "captured_or_due_at": evidence.get("captured_at") or "",
                    "created_at": evidence.get("created_at") or "",
                })

        for plan in pack.get("development_plans") or []:
            writer.writerow({
                "record_type": "development_plan",
                "record_id": plan.get("id"),
                "status": plan.get("status") or "",
                "title": _csv_safe(plan.get("objective") or "Development plan"),
                "notes": _csv_safe(plan.get("outcome") or plan.get("notes") or ""),
                "created_at": plan.get("created_at") or "",
            })
            for milestone in plan.get("milestones") or []:
                writer.writerow({
                    "record_type": "milestone",
                    "record_id": milestone.get("id"),
                    "parent_id": plan.get("id"),
                    "period_or_horizon": f"{milestone.get('horizon_days')} days",
                    "status": milestone.get("status") or "",
                    "title": _csv_safe(milestone.get("title") or "Milestone"),
                    "category": _csv_safe(milestone.get("category") or ""),
                    "metric": milestone.get("target_metric") or "",
                    "current_value": milestone.get("baseline_value") if milestone.get("baseline_value") is not None else "",
                    "target_value": milestone.get("target_value") if milestone.get("target_value") is not None else "",
                    "notes": _csv_safe(milestone.get("evidence_note") or milestone.get("detail") or ""),
                    "captured_or_due_at": milestone.get("due_at") or "",
                    "created_at": milestone.get("created_at") or "",
                })
            for review in plan.get("reviews") or []:
                review_metrics = review.get("metrics") or {}
                if not review_metrics:
                    writer.writerow({
                        "record_type": "review",
                        "record_id": review.get("id"),
                        "parent_id": plan.get("id"),
                        "title": "Development review",
                        "notes": _csv_safe(review.get("notes") or ""),
                        "created_at": review.get("created_at") or "",
                    })
                for metric, value in review_metrics.items():
                    writer.writerow({
                        "record_type": "review_metric",
                        "record_id": review.get("id"),
                        "parent_id": plan.get("id"),
                        "title": "Development review",
                        "metric": metric,
                        "current_value": value,
                        "notes": _csv_safe(review.get("notes") or ""),
                        "created_at": review.get("created_at") or "",
                    })
        return output.getvalue()


reports = AgentReportStore()


@router.get("/command-center/api/agent/reports")
def report_creator_list_api(request: Request):
    member, owner = _require_agent_or_owner(request)
    return {
        "creators": reports.list_creators(member.user_id, owner=owner),
        "assignment_gated": not owner,
        "direct_backstage_access": False,
    }


@router.get("/command-center/api/agent/reports/{creator_user_id}")
def report_pack_api(creator_user_id: str, request: Request):
    member, owner = _require_agent_or_owner(request)
    try:
        return reports.build_pack(member.user_id, creator_user_id, owner=owner)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(404, "Creator report is not available") from exc


@router.get("/command-center/agent/reports/{creator_user_id}.json")
def report_json_download(creator_user_id: str, request: Request):
    member, owner = _require_agent_or_owner(request)
    try:
        pack = reports.build_pack(member.user_id, creator_user_id, owner=owner)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(404, "Creator report is not available") from exc
    filename = f"esp-creator-report-{_slug((pack.get('creator') or {}).get('display_name') or creator_user_id)}.json"
    return JSONResponse(
        pack,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.get("/command-center/agent/reports/{creator_user_id}.csv")
def report_csv_download(creator_user_id: str, request: Request):
    member, owner = _require_agent_or_owner(request)
    try:
        pack = reports.build_pack(member.user_id, creator_user_id, owner=owner)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(404, "Creator report is not available") from exc
    filename = f"esp-creator-report-{_slug((pack.get('creator') or {}).get('display_name') or creator_user_id)}.csv"
    return Response(
        reports.to_csv(pack),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


CSS = """
:root{--line:#ffffff20;--muted:#c8bfd2;--gold:#efc66b;--violet:#a26dff;--green:#78dda7}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 88% 0,#42185d,transparent 31%),#07050d;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1180px,calc(100% - 28px));margin:auto;padding:34px 0 60px}.top,.row{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.eyebrow{color:var(--gold);font-weight:900;letter-spacing:.14em;text-transform:uppercase;font-size:.72rem}h1{font-size:clamp(2.5rem,7vw,5rem);letter-spacing:-.05em;margin:.15em 0}.muted{color:var(--muted);line-height:1.5}.card{border:1px solid var(--line);border-radius:18px;padding:16px;background:#14101deb;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}.metric{border:1px solid var(--line);border-radius:12px;padding:10px;background:#ffffff06}.metric b{display:block;font-size:1.25rem}.btn{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:9px 12px;background:#ffffff09;color:#fff;text-decoration:none;font-weight:800}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160d1d}.pill{border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:.72rem}@media(max-width:760px){.grid{grid-template-columns:1fr}}
"""


@router.get("/command-center/agent/reports", response_class=HTMLResponse, include_in_schema=False)
def report_index_page(request: Request):
    member, owner = _require_agent_or_owner(request)
    creators = reports.list_creators(member.user_id, owner=owner)
    cards = "".join(
        "<article class='card'><div class='row'><div>"
        f"<span class='pill'>{escape(str((row.get('freshness') or {}).get('status','missing')).replace('_',' ').title())}</span>"
        f"<h2>{escape(str(row.get('display_name') or 'Creator'))}</h2>"
        f"<p class='muted'>@{escape(str(row.get('tiktok_handle') or ''))} · {escape(str(row.get('region') or 'Region not supplied'))}</p>"
        "</div><div>"
        f"<a class='btn primary' href='/command-center/agent/reports/{escape(str(row.get('creator_user_id')), quote=True)}'>Open report</a>"
        "</div></div></article>"
        for row in creators
    ) or "<div class='card muted'>No authorised creators are available for reporting yet.</div>"
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<meta name='robots' content='noindex,nofollow'><title>ESP Creator Reports</title><style>{CSS}</style></head>"
        "<body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Agent OS</div>"
        "<h1>Creator Reports & Exports</h1><p class='muted'>Export authorised mentoring evidence and development progress without exposing raw screenshots, server paths or unrelated creative projects.</p></div>"
        "<a class='btn' href='/command-center/dashboard'>Role Dashboard</a></div>"
        f"{cards}</main></body></html>",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/command-center/agent/reports/{creator_user_id}", response_class=HTMLResponse, include_in_schema=False)
def report_detail_page(creator_user_id: str, request: Request):
    member, owner = _require_agent_or_owner(request)
    try:
        pack = reports.build_pack(member.user_id, creator_user_id, owner=owner)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(404, "Creator report is not available") from exc
    creator = pack["creator"]
    summary = pack["summary"]
    trend = summary.get("latest_trend") or {}
    trend_html = "".join(
        f"<div class='metric'><span class='muted'>{escape(metric.replace('_',' ').title())}</span>"
        f"<b>{escape(str(value.get('current','—')))}</b><small class='muted'>Δ {escape(str(value.get('delta','—')))} from prior evidence</small></div>"
        for metric, value in trend.items()
    ) or "<div class='metric'><span class='muted'>Trend</span><b>—</b><small class='muted'>A second confirmed evidence record is needed for comparison.</small></div>"
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<meta name='robots' content='noindex,nofollow'><title>ESP Creator Report</title><style>{CSS}</style></head>"
        "<body><main class='wrap'><div class='top'><div><div class='eyebrow'>ESP Mentoring Report</div>"
        f"<h1>{escape(str(creator.get('display_name') or 'Creator'))}</h1>"
        f"<p class='muted'>@{escape(str(creator.get('tiktok_handle') or ''))} · {escape(str(creator.get('region') or 'Region not supplied'))}</p></div>"
        f"<div><a class='btn primary' href='/command-center/agent/reports/{escape(creator_user_id, quote=True)}.json'>Download JSON</a> "
        f"<a class='btn primary' href='/command-center/agent/reports/{escape(creator_user_id, quote=True)}.csv'>Download CSV</a> "
        "<a class='btn' href='/command-center/agent/reports'>All reports</a></div></div>"
        "<section class='grid'>"
        f"<div class='metric'><span class='muted'>Evidence records</span><b>{summary['evidence_records']}</b></div>"
        f"<div class='metric'><span class='muted'>Development plans</span><b>{summary['development_plans']}</b></div>"
        f"<div class='metric'><span class='muted'>Open milestones</span><b>{summary['open_milestones']}</b></div>"
        "</section><h2>Latest measurable trend</h2>"
        f"<section class='grid'>{trend_html}</section>"
        "<section class='card'><b>Data boundary</b><p class='muted'>This report uses authorised ESP mentoring evidence only. It is not a direct TikTok LIVE Backstage connection. Raw screenshots, server storage paths and private creative projects are excluded.</p></section>"
        "</main></body></html>",
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router", "AgentReportStore", "reports", "CSV_FIELDS"]
