from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .owner_identity import owner_session_authorized, owner_theme, request_owner_persona

router = APIRouter(tags=["Owner Provider Cost Governance"])

_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
_SAFE_EVENT_ID = re.compile(r"^[a-f0-9]{32}$")
_DEFAULT_CURRENCY = "GBP"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).isoformat()


def _hash_ref(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _label(value: str, *, fallback: str = "unknown") -> str:
    raw = str(value or "").strip().lower()
    if _SAFE_LABEL.fullmatch(raw):
        return raw
    normalized = re.sub(r"[^a-z0-9._:-]+", "_", raw).strip("_.:-")[:80]
    return normalized if normalized and _SAFE_LABEL.fullmatch(normalized) else fallback


def _currency(value: str | None = None) -> str:
    resolved = str(value or os.getenv("AURA_PROVIDER_COST_CURRENCY") or _DEFAULT_CURRENCY).strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", resolved):
        raise ValueError("Provider cost currency must be a three-letter ISO currency code")
    return resolved


def _env_fragment(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")


def configured_estimate_minor(provider: str, service: str, operation: str) -> int | None:
    """Return an operator-configured per-submission estimate, never a fabricated price."""

    provider_key = _env_fragment(_label(provider))
    service_key = _env_fragment(_label(service))
    operation_key = _env_fragment(_label(operation))
    keys = [
        f"AURA_PROVIDER_COST_ESTIMATE_{provider_key}_{service_key}_{operation_key}_MINOR",
        f"AURA_PROVIDER_COST_ESTIMATE_{provider_key}_{service_key}_MINOR",
        f"AURA_PROVIDER_COST_ESTIMATE_{provider_key}_MINOR",
    ]
    for key in keys:
        raw = (os.getenv(key) or "").strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError as exc:
            raise RuntimeError(f"{key} must be a non-negative integer in minor currency units") from exc
        if value < 0:
            raise RuntimeError(f"{key} must be a non-negative integer in minor currency units")
        return value
    return None


def _configured_budget_minor(name: str) -> int | None:
    key = f"AURA_PROVIDER_COST_BUDGET_{name.upper()}_MINOR"
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{key} must be a positive integer in minor currency units") from exc
    if value <= 0:
        raise RuntimeError(f"{key} must be a positive integer in minor currency units")
    return value


def _warning_percent() -> int:
    raw = (os.getenv("AURA_PROVIDER_COST_WARNING_PERCENT") or "80").strip()
    try:
        value = int(raw)
    except ValueError:
        return 80
    return max(1, min(value, 100))


class ProviderCostStore:
    """Durable operational provider-cost ledger.

    This ledger is deliberately independent from subscriptions and Creation Coins. It records
    infrastructure/provider usage for Mary/Kev operational oversight and never grants a member
    entitlement, subscription tier, or ESP role.
    """

    def __init__(self, db_path: str | Path | None = None):
        configured = db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3"
        self.db_path = Path(configured)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS provider_cost_events (
                    id TEXT PRIMARY KEY,
                    event_key_hash TEXT NOT NULL UNIQUE,
                    provider TEXT NOT NULL,
                    service TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    media_kind TEXT,
                    user_ref_hash TEXT,
                    project_ref_hash TEXT,
                    job_ref_hash TEXT,
                    unit_name TEXT NOT NULL,
                    units INTEGER NOT NULL DEFAULT 1 CHECK(units >= 0),
                    estimated_cost_minor INTEGER CHECK(estimated_cost_minor IS NULL OR estimated_cost_minor >= 0),
                    actual_cost_minor INTEGER CHECK(actual_cost_minor IS NULL OR actual_cost_minor >= 0),
                    currency TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    reconciled_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_provider_cost_created
                    ON provider_cost_events(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_provider_cost_provider_created
                    ON provider_cost_events(provider, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_provider_cost_service_created
                    ON provider_cost_events(service, created_at DESC);
                """
            )

    def record_submission(
        self,
        *,
        provider: str,
        service: str,
        operation: str,
        job_ref: str,
        user_ref: str | None = None,
        project_ref: str | None = None,
        media_kind: str | None = None,
        unit_name: str = "render",
        units: int = 1,
        source: str = "renderer_submission",
        estimated_cost_minor: int | None = None,
        currency: str | None = None,
    ) -> dict:
        provider = _label(provider)
        service = _label(service)
        operation = _label(operation, fallback="render")
        media_kind = _label(media_kind, fallback="") if media_kind else None
        unit_name = _label(unit_name, fallback="unit")
        source = _label(source, fallback="application")
        job_ref = str(job_ref or "").strip()
        if not job_ref:
            raise ValueError("Provider cost events require a stable job reference")
        units = int(units)
        if units < 0:
            raise ValueError("Provider usage units cannot be negative")
        currency_code = _currency(currency)
        configured_currency = _currency()
        if currency_code != configured_currency:
            raise ValueError("Provider cost events must use the configured operational currency")
        if estimated_cost_minor is None:
            estimated_cost_minor = configured_estimate_minor(provider, service, operation)
        if estimated_cost_minor is not None:
            estimated_cost_minor = int(estimated_cost_minor)
            if estimated_cost_minor < 0:
                raise ValueError("Estimated provider cost cannot be negative")

        event_key_hash = _hash_ref(f"{provider}|{service}|{operation}|{job_ref}")
        assert event_key_hash is not None
        now = _iso()
        event_id = uuid4().hex
        values = (
            event_id,
            event_key_hash,
            provider,
            service,
            operation,
            media_kind,
            _hash_ref(user_ref),
            _hash_ref(project_ref),
            _hash_ref(job_ref),
            unit_name,
            units,
            estimated_cost_minor,
            currency_code,
            source,
            now,
        )
        with self._connect() as con:
            existing = con.execute(
                "SELECT * FROM provider_cost_events WHERE event_key_hash=?", (event_key_hash,)
            ).fetchone()
            if existing:
                return dict(existing)
            con.execute(
                """INSERT INTO provider_cost_events
                   (id,event_key_hash,provider,service,operation,media_kind,user_ref_hash,
                    project_ref_hash,job_ref_hash,unit_name,units,estimated_cost_minor,currency,
                    source,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
            row = con.execute("SELECT * FROM provider_cost_events WHERE id=?", (event_id,)).fetchone()
        return dict(row) if row else {}

    def reconcile_actual(self, event_id: str, actual_cost_minor: int, *, currency: str | None = None) -> dict:
        if not _SAFE_EVENT_ID.fullmatch(str(event_id or "")):
            raise ValueError("Invalid provider cost event id")
        amount = int(actual_cost_minor)
        if amount < 0:
            raise ValueError("Actual provider cost cannot be negative")
        with self._connect() as con:
            row = con.execute("SELECT * FROM provider_cost_events WHERE id=?", (event_id,)).fetchone()
            if not row:
                raise KeyError("Provider cost event not found")
            currency_code = _currency(currency or row["currency"])
            if currency_code != row["currency"]:
                raise ValueError("Actual cost currency must match the original provider cost event")
            now = _iso()
            con.execute(
                "UPDATE provider_cost_events SET actual_cost_minor=?,reconciled_at=? WHERE id=?",
                (amount, now, event_id),
            )
            updated = con.execute("SELECT * FROM provider_cost_events WHERE id=?", (event_id,)).fetchone()
        return dict(updated) if updated else {}

    def recent(self, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 250))
        with self._connect() as con:
            rows = con.execute(
                """SELECT id,provider,service,operation,media_kind,unit_name,units,
                          estimated_cost_minor,actual_cost_minor,currency,source,created_at,reconciled_at
                   FROM provider_cost_events ORDER BY created_at DESC,id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _spend_since(self, start: datetime, *, currency: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                """SELECT COUNT(*) AS jobs,
                          SUM(CASE WHEN actual_cost_minor IS NOT NULL THEN actual_cost_minor
                                   WHEN estimated_cost_minor IS NOT NULL THEN estimated_cost_minor ELSE 0 END) AS spend_minor,
                          SUM(CASE WHEN actual_cost_minor IS NOT NULL THEN 1 ELSE 0 END) AS actual_jobs,
                          SUM(CASE WHEN actual_cost_minor IS NULL AND estimated_cost_minor IS NOT NULL THEN 1 ELSE 0 END) AS estimated_jobs,
                          SUM(CASE WHEN actual_cost_minor IS NULL AND estimated_cost_minor IS NULL THEN 1 ELSE 0 END) AS unpriced_jobs
                   FROM provider_cost_events WHERE created_at>=? AND currency=?""",
                (_iso(start), currency),
            ).fetchone()
        return {
            "jobs": int((row or {})["jobs"] or 0),
            "spend_minor": int((row or {})["spend_minor"] or 0),
            "actual_jobs": int((row or {})["actual_jobs"] or 0),
            "estimated_jobs": int((row or {})["estimated_jobs"] or 0),
            "unpriced_jobs": int((row or {})["unpriced_jobs"] or 0),
        }

    def summary(self, days: int = 30) -> dict:
        days = max(1, min(int(days), 366))
        currency_code = _currency()
        start = _utcnow() - timedelta(days=days)
        with self._connect() as con:
            rows = con.execute(
                """SELECT provider,service,operation,currency,
                          COUNT(*) AS jobs,
                          SUM(units) AS units,
                          SUM(CASE WHEN actual_cost_minor IS NOT NULL THEN actual_cost_minor
                                   WHEN estimated_cost_minor IS NOT NULL THEN estimated_cost_minor ELSE 0 END) AS spend_minor,
                          SUM(CASE WHEN actual_cost_minor IS NULL AND estimated_cost_minor IS NULL THEN 1 ELSE 0 END) AS unpriced_jobs
                   FROM provider_cost_events
                   WHERE created_at>=? AND currency=?
                   GROUP BY provider,service,operation,currency
                   ORDER BY spend_minor DESC,jobs DESC,provider,service,operation""",
                (_iso(start), currency_code),
            ).fetchall()
        groups = [
            {
                "provider": row["provider"],
                "service": row["service"],
                "operation": row["operation"],
                "currency": row["currency"],
                "jobs": int(row["jobs"] or 0),
                "units": int(row["units"] or 0),
                "spend_minor": int(row["spend_minor"] or 0),
                "unpriced_jobs": int(row["unpriced_jobs"] or 0),
            }
            for row in rows
        ]
        totals = self._spend_since(start, currency=currency_code)
        return {
            "days": days,
            "currency": currency_code,
            "totals": totals,
            "groups": groups,
            "spend_basis": "actual_where_known_otherwise_operator_estimate",
            "creation_coin_effect": "none",
            "subscription_effect": "none",
            "esp_role_effect": "none",
        }

    def budget_status(self) -> dict:
        now = _utcnow()
        currency_code = _currency()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        warning_at = _warning_percent()
        windows = {
            "daily": (day_start, _configured_budget_minor("daily")),
            "monthly": (month_start, _configured_budget_minor("monthly")),
        }
        result: dict[str, dict] = {}
        for name, (start, budget) in windows.items():
            spend = self._spend_since(start, currency=currency_code)
            percent = round((spend["spend_minor"] / budget) * 100, 2) if budget else None
            state = "not_configured"
            if budget:
                if spend["spend_minor"] >= budget:
                    state = "over_budget"
                elif percent is not None and percent >= warning_at:
                    state = "warning"
                else:
                    state = "within_budget"
            result[name] = {
                **spend,
                "budget_minor": budget,
                "percent_used": percent,
                "state": state,
            }
        return {
            "currency": currency_code,
            "warning_percent": warning_at,
            "enforcement": "warning_only",
            "windows": result,
        }


store = ProviderCostStore()


def _renderer_units(kind: str, variables: dict) -> tuple[str, int]:
    if kind == "video":
        try:
            frames = max(1, int(variables.get("frames") or 1))
        except (TypeError, ValueError):
            frames = 1
        return "frames", frames
    return "render", 1


def install_provider_cost_governance() -> None:
    """Wrap successful ComfyUI submissions with non-blocking operational metering."""

    from .creative_renderers import ComfyUIRenderer

    original = ComfyUIRenderer.submit
    if getattr(original, "__provider_cost_governed__", False):
        return

    def governed_submit(self, variables):
        submission = original(self, variables)
        try:
            values = variables if isinstance(variables, dict) else {}
            unit_name, units = _renderer_units(self.kind, values)
            store.record_submission(
                provider=submission.provider,
                service=self.kind,
                operation=str(values.get("operation") or "render"),
                job_ref=submission.prompt_id,
                user_ref=str(values.get("user_id") or "") or None,
                project_ref=str(values.get("project_name") or "") or None,
                media_kind=self.kind,
                unit_name=unit_name,
                units=units,
                source="renderer_submission",
            )
        except Exception:
            # A successful provider submission must never be converted into a failed member job
            # merely because optional operational metering is temporarily unavailable.
            pass
        return submission

    governed_submit.__provider_cost_governed__ = True  # type: ignore[attr-defined]
    governed_submit.__wrapped__ = original  # type: ignore[attr-defined]
    ComfyUIRenderer.submit = governed_submit  # type: ignore[assignment]


def _require_owner(request: Request) -> None:
    if not owner_session_authorized(request):
        raise HTTPException(403, "Owner authorization required")


def _money(minor: int | None, currency_code: str) -> str:
    if minor is None:
        return "Unpriced"
    symbols = {"GBP": "£", "USD": "$", "EUR": "€"}
    symbol = symbols.get(currency_code, f"{currency_code} ")
    return f"{symbol}{int(minor) / 100:.2f}"


@router.get("/owner/api/provider-costs/summary", include_in_schema=False)
def owner_provider_cost_summary(request: Request, days: int = 30):
    _require_owner(request)
    return {
        "summary": store.summary(days),
        "budgets": store.budget_status(),
        "recent": store.recent(100),
        "privacy": {
            "raw_user_refs_exposed": False,
            "raw_project_refs_exposed": False,
            "raw_provider_job_refs_exposed": False,
            "provider_secrets_exposed": False,
        },
    }


@router.post("/owner/provider-costs/{event_id}/reconcile", include_in_schema=False)
def owner_reconcile_provider_cost(
    event_id: str,
    request: Request,
    actual_cost_minor: int = Form(...),
    currency: str = Form(_DEFAULT_CURRENCY),
):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    try:
        store.reconcile_actual(event_id, actual_cost_minor, currency=currency)
    except KeyError:
        return RedirectResponse("/owner/provider-costs?message=Event%20not%20found", status_code=303)
    except ValueError as exc:
        return RedirectResponse(
            "/owner/provider-costs?message=" + quote(str(exc), safe=""),
            status_code=303,
        )
    return RedirectResponse("/owner/provider-costs?message=Actual%20cost%20reconciled", status_code=303)


@router.get("/owner/provider-costs", response_class=HTMLResponse, include_in_schema=False)
def owner_provider_cost_dashboard(request: Request, days: int = 30, message: str = ""):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    days = max(1, min(int(days), 366))
    summary = store.summary(days)
    budgets = store.budget_status()
    recent = store.recent(75)
    theme = owner_theme(request_owner_persona(request))
    currency_code = summary["currency"]
    totals = summary["totals"]
    daily = budgets["windows"]["daily"]
    monthly = budgets["windows"]["monthly"]

    rows = "".join(
        f"<tr><td>{escape(item['provider'])}</td><td>{escape(item['service'])}</td>"
        f"<td>{escape(item['operation'])}</td><td>{int(item['jobs'])}</td><td>{int(item['units'])}</td>"
        f"<td>{_money(item['spend_minor'], item['currency'])}</td><td>{int(item['unpriced_jobs'])}</td></tr>"
        for item in summary["groups"]
    ) or "<tr><td colspan='7' class='muted'>No provider submissions have been recorded in this window.</td></tr>"

    recent_rows = "".join(
        f"<tr><td><code>{escape(item['id'][:10])}</code></td><td>{escape(item['provider'])}</td>"
        f"<td>{escape(item['service'])} / {escape(item['operation'])}</td>"
        f"<td>{_money(item['estimated_cost_minor'], item['currency'])}</td>"
        f"<td>{_money(item['actual_cost_minor'], item['currency'])}</td>"
        f"<td>{escape(item['created_at'][:19].replace('T',' '))}</td>"
        f"<td><form method='post' action='/owner/provider-costs/{escape(item['id'], quote=True)}/reconcile' class='reconcile'>"
        f"<input type='number' min='0' name='actual_cost_minor' placeholder='minor units' required>"
        f"<input type='hidden' name='currency' value='{escape(item['currency'], quote=True)}'>"
        f"<button type='submit'>Set actual</button></form></td></tr>"
        for item in recent
    ) or "<tr><td colspan='7' class='muted'>No provider events recorded yet.</td></tr>"

    def budget_card(label: str, value: dict) -> str:
        budget = _money(value["budget_minor"], currency_code) if value["budget_minor"] else "Not configured"
        percent = f"{value['percent_used']}%" if value["percent_used"] is not None else "—"
        return (
            f"<div class='metric'><small>{escape(label)} budget</small><b>{budget}</b>"
            f"<span class='state {escape(value['state'])}'>{escape(value['state'].replace('_',' '))}</span>"
            f"<p class='muted'>{_money(value['spend_minor'], currency_code)} effective spend · {percent} used</p></div>"
        )

    notice = f"<div class='notice'>{escape(message)}</div>" if message else ""
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'>
<title>Provider Cost Governance</title><style>
:root{{--accent:{theme.accent};--secondary:{theme.secondary};--line:#ffffff1d;--muted:#c9bfd5;--good:#75dda0;--warn:#ffd17b;--bad:#ff8ea4}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 90% 0,var(--secondary),transparent 28%),#07050c;color:#fff;font-family:Inter,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.wrap{{width:min(1420px,calc(100% - 28px));margin:auto;padding:30px 0 60px}}.top{{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:flex-start}}.eyebrow{{color:var(--accent);text-transform:uppercase;letter-spacing:.14em;font-size:.72rem;font-weight:900}}h1{{font-size:clamp(2.5rem,6vw,5rem);letter-spacing:-.055em;margin:.12em 0}}.muted{{color:var(--muted);line-height:1.55}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0}}.card,.metric{{border:1px solid var(--line);border-radius:17px;background:#15101ded;padding:15px}}.card{{margin:13px 0}}.metric b{{display:block;font-size:1.55rem;margin:4px 0}}.btn,button{{border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#ffffff09;color:#fff;font-weight:800;cursor:pointer}}.primary{{background:linear-gradient(110deg,var(--accent),var(--secondary));color:#120817;border:0}}.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;min-width:950px}}th,td{{padding:9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}}th{{font-size:.74rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}}input{{width:115px;border:1px solid var(--line);border-radius:8px;background:#09070e;color:#fff;padding:8px}}.reconcile{{display:flex;gap:6px}}.state{{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:3px 7px;font-size:.7rem;text-transform:uppercase}}.within_budget{{color:var(--good)}}.warning{{color:var(--warn)}}.over_budget{{color:var(--bad)}}.notice{{padding:10px 13px;border:1px solid var(--accent);border-radius:12px;margin:12px 0}}code{{color:var(--accent)}}@media(max-width:900px){{.grid{{grid-template-columns:1fr 1fr}}}}@media(max-width:520px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Mary / Kev · Operational Finance</div><h1>Provider Cost Governance</h1><p class='muted'>Real infrastructure/provider usage only. Creation Coins, member subscriptions and ESP permissions are separate systems.</p></div><div><a class='btn primary' href='/owner/dashboard'>Owner dashboard</a> <a class='btn' href='/owner/users'>Users</a></div></div>{notice}
<section class='grid'><div class='metric'><small>{days}-day effective spend</small><b>{_money(totals['spend_minor'], currency_code)}</b><p class='muted'>{totals['jobs']} provider jobs</p></div><div class='metric'><small>Actual-cost jobs</small><b>{totals['actual_jobs']}</b><p class='muted'>Provider/invoice reconciled</p></div><div class='metric'><small>Estimated-cost jobs</small><b>{totals['estimated_jobs']}</b><p class='muted'>Operator-configured estimate</p></div><div class='metric'><small>Unpriced jobs</small><b>{totals['unpriced_jobs']}</b><p class='muted'>Usage recorded; cost intentionally unknown</p></div></section>
<section class='grid'>{budget_card('Daily', daily)}{budget_card('Monthly', monthly)}<div class='metric'><small>Budget policy</small><b>Warning only</b><p class='muted'>No member render is blocked by this dashboard stage.</p></div><div class='metric'><small>Accounting basis</small><b>Actual → estimate</b><p class='muted'>Actual cost wins; configured estimate is used only when actual is absent.</p></div></section>
<section class='card'><div class='eyebrow'>Provider breakdown</div><h2>Last {days} days</h2><div class='scroll'><table><thead><tr><th>Provider</th><th>Service</th><th>Operation</th><th>Jobs</th><th>Units</th><th>Effective spend</th><th>Unpriced</th></tr></thead><tbody>{rows}</tbody></table></div></section>
<section class='card'><div class='eyebrow'>Reconciliation</div><h2>Recent provider jobs</h2><p class='muted'>Only opaque event IDs are displayed. Raw member IDs, project names, provider job IDs, prompts, paths and provider credentials are never exposed here.</p><div class='scroll'><table><thead><tr><th>Event</th><th>Provider</th><th>Operation</th><th>Estimate</th><th>Actual</th><th>Created</th><th>Reconcile actual</th></tr></thead><tbody>{recent_rows}</tbody></table></div></section>
<section class='card'><div class='eyebrow'>Commercial boundary</div><h2>Operational cost ≠ Creation Coins</h2><p class='muted'>This ledger cannot activate Free/Base/Pro, cannot change billing status, cannot grant Creator/Agent/Owner roles and cannot debit a member Creation Coin wallet. It exists only to help Mary and Kev understand the real cost of running AI/render providers.</p></section>
</main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


__all__ = [
    "ProviderCostStore",
    "configured_estimate_minor",
    "install_provider_cost_governance",
    "router",
    "store",
]
