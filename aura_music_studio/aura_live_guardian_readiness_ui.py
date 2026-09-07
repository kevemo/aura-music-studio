from __future__ import annotations

import os
from html import escape
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .aura_live_guardian_readiness import assess_live_readiness
from .branding import PRODUCT_FULL_NAME

router = APIRouter(tags=["Aura LIVE Guardian Readiness"])


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _database() -> Path:
    path = Path(os.getenv("AURA_LIVE_MODERATOR_DB", "data/aura_live_moderator.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@router.get("/live-guardian/readiness", response_class=HTMLResponse, include_in_schema=False)
def live_guardian_readiness_page(request: Request):
    member = _member(request)
    report = assess_live_readiness(database=_database(), user_id=member.user_id)
    status_label = "Ready for LIVE" if report.pre_live_ready else "Action required before LIVE"
    status_class = "safe" if report.pre_live_ready else "warn"
    checks = "".join(
        "<article class='check'>"
        f"<div class='icon {'safe' if check.passed else 'warn'}'>{'✓' if check.passed else '!'}</div>"
        "<div>"
        f"<h3>{escape(check.label)}</h3>"
        f"<p>{escape(check.detail)}</p>"
        f"<span class='badge'>{'Required' if check.blocking else 'Informational'}</span>"
        "</div></article>"
        for check in report.checks
    )
    blocking = "".join(f"<li>{escape(check.label)}</li>" for check in report.blocking_failures)
    blocking_panel = (
        f"<div class='card warnbox'><h2>Resolve before LIVE</h2><ul>{blocking}</ul></div>"
        if blocking
        else "<div class='card safebox'><h2>Blocking Guardian checks are clear</h2><p>Continue to monitor Human Review and your live provider connection during the session.</p></div>"
    )
    title = escape(PRODUCT_FULL_NAME)
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='referrer' content='no-referrer'><meta name='robots' content='noindex,nofollow'><title>Aura LIVE Readiness — {title}</title><style>
body{{margin:0;background:#070812;color:#fff;font-family:Inter,system-ui,sans-serif}}.wrap{{width:min(1100px,calc(100% - 28px));margin:auto;padding:36px 0 64px}}h1{{font-size:clamp(2.6rem,6vw,5rem);margin:.1em 0}}.muted,p{{color:#bdc6d8;line-height:1.55}}.card,.check{{background:#111526;border:1px solid #ffffff20;border-radius:18px;padding:18px;margin:14px 0}}.check{{display:grid;grid-template-columns:48px 1fr;gap:14px;align-items:start}}.check h3{{margin:2px 0 5px}}.check p{{margin:0 0 9px}}.icon{{width:40px;height:40px;border-radius:50%;display:grid;place-items:center;font-weight:950;background:#ffffff0d}}.safe{{color:#8ef0b0}}.warn{{color:#ffd483}}.badge{{font-size:.76rem;font-weight:900;text-transform:uppercase;letter-spacing:.05em;color:#efc96b}}.btn{{font:inherit;border:1px solid #ffffff25;border-radius:10px;background:#ffffff0d;color:#fff;padding:10px 12px;cursor:pointer;font-weight:850;text-decoration:none;display:inline-block;margin-right:8px}}.primary{{background:linear-gradient(120deg,#efc96b,#9b72ff);color:#160b22;border:0}}.stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.stat{{background:#ffffff08;border-radius:14px;padding:14px}}.stat b{{font-size:1.6rem;display:block}}.warnbox{{border-color:#ffd48355}}.safebox{{border-color:#8ef0b055}}@media(max-width:700px){{.stats{{grid-template-columns:1fr}}}}
</style></head><body><main class='wrap'><div class='badge'>Aura LIVE Guardian · Pre-LIVE Safety</div><h1>LIVE Safety Readiness</h1><p class='{status_class}' style='font-weight:950;font-size:1.25rem'>{status_label}</p><p class='muted'>This checklist evaluates persisted Guardian safety state only. It never treats browser state or historical approval as proof that a TikTok/partner connector is currently connected or authorized to write.</p><div><a class='btn primary' href='/live-guardian'>LIVE Guardian</a><a class='btn' href='/live-guardian/review'>Human Review Queue</a><a class='btn' href='/live-guardian/policy'>Moderation Policy</a></div><div class='card stats'><div class='stat'><span>Guardian mode</span><b>{escape(report.mode.replace('_', ' ').title())}</b></div><div class='stat'><span>Pending reviews</span><b>{report.pending_reviews}</b></div><div class='stat'><span>Critical escalations</span><b>{report.critical_escalations}</b></div></div>{blocking_panel}<section><h2>Readiness checks</h2>{checks}</section><div class='card'><h2>Provider execution truth</h2><p><b class='warn'>Fail-closed / not asserted by this page.</b></p><p>This readiness view does not claim current provider execution capability. Any actual TikTok action must independently pass current consent, moderator assignment, approved transport and connector capability checks at execution time.</p></div></main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"})


__all__ = ["router", "live_guardian_readiness_page"]
