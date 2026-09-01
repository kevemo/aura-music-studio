from __future__ import annotations

from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .accounts import AccountStore
from .aura_sec_store import AuraSecStore
from .branding import ENDORSEMENT, PRODUCT_FULL_NAME

router = APIRouter(prefix="/aura-sec", tags=["Aura Sec"])
accounts = AccountStore()
security = AuraSecStore(accounts)
MEMBER_COOKIE = "lss_session"


def _member(request: Request) -> dict | None:
    return accounts.resolve_session(request.cookies.get(MEMBER_COOKIE))


def _require_member(request: Request) -> dict:
    member = _member(request)
    if not member:
        raise HTTPException(401, "Authenticated member session required")
    return member


def _safe_control_plane_snapshot(user_id: str) -> dict:
    licence = security.licence(user_id)
    devices = security.list_devices(user_id)
    with security._connect() as con:
        incidents = [
            dict(row)
            for row in con.execute(
                """SELECT id,device_id,severity,status,title,confidence,created_at,updated_at
                   FROM aura_sec_incidents
                   WHERE user_id=?
                   ORDER BY created_at DESC
                   LIMIT 50""",
                (user_id,),
            ).fetchall()
        ]
        actions = [
            dict(row)
            for row in con.execute(
                """SELECT id,incident_id,device_id,action_type,risk_class,status,
                          requested_at,approved_at,executed_at,verified_at
                   FROM aura_sec_actions
                   WHERE user_id=?
                   ORDER BY requested_at DESC
                   LIMIT 50""",
                (user_id,),
            ).fetchall()
        ]

    active_devices = sum(1 for item in devices if item.get("status") != "revoked")
    open_incidents = sum(1 for item in incidents if item.get("status") == "open")
    waiting_actions = sum(
        1 for item in actions if item.get("status") in {"proposed", "approved"}
    )
    return {
        "product": "Aura Sec",
        "licence": licence,
        "devices": devices,
        "incidents": incidents,
        "actions": actions,
        "summary": {
            "active_devices": active_devices,
            "open_incidents": open_incidents,
            "waiting_actions": waiting_actions,
        },
        "trust_boundary": {
            "shared_member_session": True,
            "separate_security_licence": True,
            "browser_can_execute_native_actions": False,
            "browser_can_poll_native_commands": False,
            "browser_can_submit_native_heartbeats": False,
            "browser_has_command_signing_key_access": False,
            "native_signed_command_required": True,
            "native_execution_idempotency_required": True,
            "native_command_anti_rollback_required": True,
            "external_non_exportable_signing_contract_available": True,
            "production_hsm_or_kms_provider_asserted_here": False,
        },
    }


@router.get("/overview")
def aura_sec_overview(request: Request):
    member = _require_member(request)
    return _safe_control_plane_snapshot(member["id"])


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def aura_sec_security_center(request: Request):
    member = _member(request)
    if not member:
        return RedirectResponse("/signin", status_code=303)

    snapshot = _safe_control_plane_snapshot(member["id"])
    licence = snapshot["licence"]
    summary = snapshot["summary"]
    devices = snapshot["devices"]
    incidents = snapshot["incidents"]
    actions = snapshot["actions"]

    licence_status = str(licence.get("status") or "not_purchased").replace("_", " ").title()
    device_rows = "".join(
        "<tr>"
        f"<td>{escape(str(item.get('display_name') or 'Device'))}</td>"
        f"<td>{escape(str(item.get('platform') or 'unknown'))} / {escape(str(item.get('architecture') or 'unknown'))}</td>"
        f"<td>{escape(str(item.get('protection_state') or 'unknown').replace('_', ' '))}</td>"
        f"<td>{escape(str(item.get('status') or 'unknown'))}</td>"
        "</tr>"
        for item in devices[:20]
    ) or "<tr><td colspan='4' class='muted'>No enrolled Aura Sec devices are recorded for this member.</td></tr>"

    incident_rows = "".join(
        "<tr>"
        f"<td>{escape(str(item.get('severity') or 'unknown').upper())}</td>"
        f"<td>{escape(str(item.get('title') or 'Security incident'))}</td>"
        f"<td>{escape(str(item.get('status') or 'unknown'))}</td>"
        "</tr>"
        for item in incidents[:20]
    ) or "<tr><td colspan='3' class='muted'>No Aura Sec incidents are recorded for this member.</td></tr>"

    action_rows = "".join(
        "<tr>"
        f"<td>{escape(str(item.get('action_type') or 'action').replace('_', ' '))}</td>"
        f"<td>{escape(str(item.get('risk_class') or 'unknown').replace('_', ' '))}</td>"
        f"<td>{escape(str(item.get('status') or 'unknown'))}</td>"
        "</tr>"
        for item in actions[:20]
    ) or "<tr><td colspan='3' class='muted'>No bounded Aura Sec actions are recorded for this member.</td></tr>"

    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Aura Sec — {escape(PRODUCT_FULL_NAME)}</title><style>
:root{{--bg:#05070d;--panel:#101624;--line:#ffffff1e;--gold:#f2c36f;--cyan:#55e7ff;--good:#75dda0;--muted:#bfc5d4;--bad:#ff91a5}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 12% 0,#18395a,transparent 30%),#05070d;color:#fff;font-family:Inter,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.wrap{{width:min(1220px,calc(100% - 28px));margin:auto;padding:26px 0 60px}}.top,.row{{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}}.eyebrow{{color:var(--cyan);text-transform:uppercase;letter-spacing:.15em;font-size:.72rem;font-weight:900}}h1{{font-size:clamp(2.7rem,7vw,5.8rem);line-height:.95;margin:.15em 0}}h2{{margin:.2em 0 .6em}}.muted{{color:var(--muted);line-height:1.58}}.btn{{border:1px solid var(--line);border-radius:11px;padding:10px 13px;background:#ffffff08;font-weight:850}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:20px 0}}.card{{background:#101624e8;border:1px solid var(--line);border-radius:20px;padding:18px}}.metric{{font-size:2rem;font-weight:950;margin-top:7px}}.good{{color:var(--good)}}.warn{{color:var(--gold)}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left;font-size:.88rem}}th{{color:var(--cyan)}}.truth{{border-color:#55e7ff50;background:#071924}}code{{color:#aff2ff}}@media(max-width:780px){{.grid{{grid-template-columns:1fr}}table{{display:block;overflow:auto}}}}</style></head><body><main class='wrap'>
<header class='top'><div><div class='eyebrow'>Aura Sec · member security control plane</div><b>{escape(PRODUCT_FULL_NAME)}</b></div><div><a class='btn' href='/dashboard'>← Dashboard</a> <a class='btn' href='/support'>Support</a></div></header>
<section style='padding:48px 0 16px'><div class='eyebrow'>Security Center</div><h1>Aura Sec</h1><p class='muted'>One account, one site, separate security entitlement. This page shows the member-safe Aura Sec control plane. Native protection, signed heartbeat ingestion, command polling, command signing and endpoint execution are deliberately not exposed to the browser.</p></section>
<section class='grid'><article class='card'><div class='eyebrow'>Licence</div><div class='metric'>{escape(licence_status)}</div><p class='muted'>Device allowance: {int(licence.get('device_limit') or 0)}</p></article><article class='card'><div class='eyebrow'>Managed devices</div><div class='metric'>{int(summary['active_devices'])}</div><p class='muted'>Revoked devices never regain native trust through this page.</p></article><article class='card'><div class='eyebrow'>Open incidents</div><div class='metric'>{int(summary['open_incidents'])}</div><p class='muted'>Waiting bounded actions: {int(summary['waiting_actions'])}</p></article></section>
<section class='card'><div class='eyebrow'>Devices</div><h2>Enrolled endpoints</h2><table><thead><tr><th>Device</th><th>Platform</th><th>Protection state</th><th>Status</th></tr></thead><tbody>{device_rows}</tbody></table></section>
<section class='card'><div class='eyebrow'>Incidents</div><h2>Recent security findings</h2><table><thead><tr><th>Severity</th><th>Finding</th><th>Status</th></tr></thead><tbody>{incident_rows}</tbody></table></section>
<section class='card'><div class='eyebrow'>Actions</div><h2>Bounded action lifecycle</h2><table><thead><tr><th>Action</th><th>Risk</th><th>Status</th></tr></thead><tbody>{action_rows}</tbody></table></section>
<section class='card truth'><div class='eyebrow'>Trust boundary</div><h2>What the browser cannot do</h2><p class='muted'>This Security Center cannot execute native commands, poll the native command channel, submit native heartbeat proof, access signing keys or bypass strong re-authentication. Aura Sec native work remains behind enrolled-device signatures, bounded approval lifetimes, signed server commands, execute-once admission and per-device anti-rollback sequencing.</p><p class='muted'>A non-exportable HSM/KMS signing integration contract exists in the codebase, but this page does not claim that a production provider has been configured or independently evidenced.</p></section>
<footer class='muted' style='margin-top:24px'>{escape(ENDORSEMENT)}</footer></main></body></html>"""
    return HTMLResponse(html)


__all__ = ["router"]
