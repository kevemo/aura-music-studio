from __future__ import annotations

from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .accounts import AccountStore
from .aura_sec_health import evaluate_device_health
from .aura_sec_read_model import AuraSecReadModel
from .aura_sec_recovery import AuraSecRecoveryStore
from .aura_sec_store import AuraSecStore
from .aura_sec_vulnerability_store import AuraSecVulnerabilityStore

router = APIRouter(tags=["Aura Sec Workflow Portal"])
accounts = AccountStore()
security = AuraSecStore(accounts)
read_model = AuraSecReadModel(accounts)
recovery = AuraSecRecoveryStore(accounts, security)
vulnerabilities = AuraSecVulnerabilityStore(accounts, security)
MEMBER_COOKIE = "lss_session"

MASTER_PRODUCT_NAME = "Elevate Souls Productions Content Creation Command Center"
MASTER_ENDORSEMENT = "Powered by Aura AI"


def _user(request: Request) -> dict | None:
    return accounts.resolve_session(request.cookies.get(MEMBER_COOKIE))


def _require(request: Request):
    user = _user(request)
    if not user:
        return None, RedirectResponse(f"/signin?next={request.url.path}", status_code=303)
    return user, None


def _label(value: object) -> str:
    return str(value or "unknown").replace("_", " ").title()


def _tone(value: object) -> str:
    state = str(value or "").lower()
    if state in {"healthy", "active", "verified", "success", "ransomware_ready", "clean"}:
        return "good"
    if state in {"critical", "emergency", "incident", "high", "failed", "infected", "stale", "degraded"}:
        return "danger"
    if state in {"proposed", "attention_required", "restore_test_required", "awaiting_verified_heartbeat"}:
        return "warn"
    return "neutral"


def _secure_html(html: str, *, status_code: int = 200) -> HTMLResponse:
    response = HTMLResponse(html, status_code=status_code)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; "
        "img-src 'self' data:; style-src 'self' 'unsafe-inline'"
    )
    return response


_NAV = (
    ("/aura-sec", "Overview"),
    ("/aura-sec/plans", "Plans & Licence"),
    ("/aura-sec/enrol", "Enrol Device"),
    ("/aura-sec/devices", "My Devices"),
    ("/aura-sec/vulnerabilities", "Vulnerabilities"),
    ("/aura-sec/threats", "Threats & Incidents"),
    ("/aura-sec/approvals", "Approvals"),
    ("/aura-sec/recovery", "Recovery"),
    ("/aura-sec/recovery/drill", "Restore Drills"),
    ("/aura-sec/optimizer", "Optimisation"),
    ("/aura-sec/downloads", "Downloads"),
    ("/aura-sec/settings", "Settings"),
)


def _shell(title: str, body: str, *, active: str) -> HTMLResponse:
    nav = "".join(
        f"<a class='nav {'active' if path == active else ''}' href='{path}'>{escape(label)}</a>"
        for path, label in _NAV
    )
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>{escape(title)} · Aura Sec</title><style>
:root{{--bg:#02060a;--panel:#0a141c;--line:#ffffff1b;--text:#f7fbff;--muted:#9eb0c0;--cyan:#69efff;--gold:#f4cc78;--violet:#b18cff;--good:#79e7a5;--warn:#ffd477;--danger:#ff8799}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 5% 0,#123b54,transparent 30%),radial-gradient(circle at 96% 2%,#381a53,transparent 28%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.layout{{display:grid;grid-template-columns:270px 1fr;min-height:100vh}}aside{{padding:22px 15px;border-right:1px solid var(--line);background:#03090ef2}}.brand{{font-weight:950;line-height:1.15}}.brand small{{display:block;margin-top:6px;color:var(--gold);font-size:.66rem;letter-spacing:.1em;text-transform:uppercase}}.product{{font-size:1.5rem;font-weight:950;margin:26px 8px 12px;background:linear-gradient(90deg,#fff,var(--cyan),var(--violet));background-clip:text;color:transparent}}nav{{display:grid;gap:5px}}.nav{{padding:10px 11px;border:1px solid transparent;border-radius:11px;color:var(--muted);font-size:.82rem;font-weight:800}}.nav:hover,.nav.active{{border-color:#69efff3b;background:#69efff0c;color:#fff}}main{{padding:32px clamp(18px,4vw,56px) 60px;min-width:0}}.eyebrow{{color:var(--cyan);font-size:.7rem;letter-spacing:.13em;text-transform:uppercase;font-weight:900}}h1{{font-size:clamp(2.4rem,5vw,4.7rem);line-height:.95;letter-spacing:-.05em;margin:.18em 0 .45em}}h2{{margin:5px 0 14px}}h3{{margin:5px 0 8px}}p,.meta{{color:var(--muted);line-height:1.55}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}.grid2{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.card,.panel,.truth{{border:1px solid var(--line);border-radius:18px;padding:17px;background:linear-gradient(145deg,#0e1a23df,#071017e8)}}.metric strong{{display:block;font-size:1.55rem}}.metric span,.meta{{font-size:.76rem}}.section{{margin-top:28px}}.row{{display:flex;justify-content:space-between;gap:16px;align-items:center;border-top:1px solid var(--line);padding:13px 0}}.row:first-child{{border-top:0}}.pill{{border:1px solid var(--line);border-radius:999px;padding:5px 9px;font-size:.67rem;font-weight:900;text-transform:uppercase;white-space:nowrap}}.pill.good{{color:var(--good);border-color:#79e7a54a}}.pill.warn{{color:var(--warn);border-color:#ffd4774a}}.pill.danger{{color:var(--danger);border-color:#ff87994a}}.pill.neutral{{color:var(--muted)}}.truth{{border-color:#ffd4773d;background:#ffd47708;color:var(--muted)}}.truth b{{color:var(--warn)}}.action{{display:inline-flex;margin-top:8px;padding:8px 11px;border:1px solid var(--line);border-radius:10px;font-size:.76rem;font-weight:850}}.disabled{{opacity:.55;cursor:not-allowed}}.empty{{padding:24px;text-align:center;border:1px dashed #ffffff26;border-radius:16px;color:var(--muted)}}ul{{color:var(--muted);line-height:1.7}}@media(max-width:950px){{.layout{{grid-template-columns:1fr}}aside{{border-right:0;border-bottom:1px solid var(--line)}}nav{{grid-template-columns:repeat(3,minmax(0,1fr))}}.grid{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:620px){{nav,.grid,.grid2{{grid-template-columns:1fr}}main{{padding:22px 14px 48px}}.row{{align-items:flex-start;flex-direction:column}}}}
</style></head><body><div class='layout'><aside><a class='brand' href='/dashboard'>{escape(MASTER_PRODUCT_NAME)}<small>{escape(MASTER_ENDORSEMENT)}</small></a><div class='product'>Aura Sec</div><nav>{nav}</nav></aside><main><div class='eyebrow'>Aura Sec · Verified Security State</div><h1>{escape(title)}</h1>{body}</main></div></body></html>"""
    return _secure_html(html)


@router.get("/aura-sec/device/{device_id}", response_class=HTMLResponse, include_in_schema=False)
def device_detail_page(device_id: str, request: Request):
    user, redirect = _require(request)
    if redirect:
        return redirect
    try:
        device = security.get_device(user["id"], device_id)
    except ValueError as exc:
        raise HTTPException(404, "Aura Sec device not found") from exc

    health = evaluate_device_health(device)
    findings = vulnerabilities.list(user["id"], device_id=device_id, status="open", limit=250)
    incidents = [item for item in read_model.incidents(user["id"], limit=500) if item.get("device_id") == device_id]
    actions = [item for item in read_model.actions(user["id"], limit=500) if item.get("device_id") == device_id]
    readiness = None if device.get("status") == "revoked" else recovery.readiness(user["id"], device_id)

    finding_rows = "".join(
        f"<div class='row'><div><b>{escape(str(item['cve_id']))} · {escape(str(item['product']))}</b><div class='meta'>Installed {escape(str(item['installed_version']))} · Score {escape(str(item['priority_score']))}</div></div><a class='pill {_tone(item['priority'])}' href='/aura-sec/vulnerability/{escape(str(item['id']))}'>{escape(_label(item['priority']))}</a></div>"
        for item in findings
    ) or "<div class='empty'>No verified open vulnerabilities for this device.</div>"
    incident_rows = "".join(
        f"<div class='row'><div><b>{escape(str(item['title']))}</b><div class='meta'>{escape(str(item['created_at']))}</div></div><a class='pill {_tone(item['severity'])}' href='/aura-sec/incident/{escape(str(item['id']))}'>{escape(_label(item['severity']))}</a></div>"
        for item in incidents[:100]
    ) or "<div class='empty'>No verified incidents for this device.</div>"

    recovery_state = readiness.get("state") if readiness else "not_managed"
    body = f"""<div class='grid'><div class='card metric'><strong>{escape(_label(health.health))}</strong><span>verified health</span></div><div class='card metric'><strong>{len(findings)}</strong><span>open verified vulnerabilities</span></div><div class='card metric'><strong>{sum(1 for item in actions if item.get('status') == 'proposed')}</strong><span>actions awaiting approval</span></div></div><section class='section grid2'><div class='card'><div class='eyebrow'>Endpoint identity</div><h2>{escape(str(device.get('display_name') or 'Unnamed device'))}</h2><p>{escape(_label(device.get('platform')))} · {escape(str(device.get('architecture') or 'unknown'))}</p><div class='meta'>Agent {escape(str(device.get('agent_version') or 'not reporting'))} · Policy {escape(str(device.get('last_policy_version') or 'not reporting'))}</div><div class='meta'>{escape(health.reason)}</div></div><div class='card'><div class='eyebrow'>Recovery</div><h2>{escape(_label(recovery_state))}</h2><p>{escape(str((readiness or {}).get('truth') or 'Recovery readiness is not evaluated for a revoked device.'))}</p><a class='action' href='/aura-sec/recovery/drill'>Review restore-drill readiness</a></div></section><section class='section panel'><div class='eyebrow'>Vulnerability posture</div><h2>Verified device-applicable findings</h2>{finding_rows}</section><section class='section panel'><div class='eyebrow'>Threat history</div><h2>Verified incidents</h2>{incident_rows}</section><section class='section truth'><b>Read-only device view.</b> Opening this page cannot approve remediation, issue a command, mark a CVE fixed, or make a stale device healthy.</section>"""
    return _shell(str(device.get("display_name") or "Device"), body, active="/aura-sec/devices")


@router.get("/aura-sec/approvals", response_class=HTMLResponse, include_in_schema=False)
def approvals_page(request: Request):
    user, redirect = _require(request)
    if redirect:
        return redirect
    proposed = [item for item in read_model.actions(user["id"], limit=500) if item.get("status") == "proposed"]
    high_risk = sum(1 for item in proposed if item.get("risk_class") == "strong_reauth_required")
    rows = "".join(
        f"<div class='row'><div><b>{escape(_label(item['action_type']))}</b><div class='meta'>{escape(str(item.get('display_name') or 'Device'))} · Risk {escape(_label(item['risk_class']))} · Proposed {escape(str(item['requested_at']))}</div></div><span class='pill {_tone(item['status'])}'>{escape(_label(item['status']))}</span></div>"
        for item in proposed
    ) or "<div class='empty'>No bounded security actions are currently awaiting approval.</div>"
    body = f"""<div class='grid'><div class='card metric'><strong>{len(proposed)}</strong><span>proposed actions</span></div><div class='card metric'><strong>{high_risk}</strong><span>requiring strong re-authentication</span></div><div class='card metric'><strong>0</strong><span>browser-executable generic commands</span></div></div><section class='section panel'><div class='eyebrow'>Human approval boundary</div><h2>Actions awaiting review</h2>{rows}</section><section class='section truth'><b>Execution is intentionally locked here.</b> The production approval gateway must verify CSRF/session integrity and, for high-risk actions, strong re-authentication before the trusted control plane can mark an action approved. Only then can a signed native session receive a typed command.</section>"""
    return _shell("Approvals", body, active="/aura-sec/approvals")


@router.get("/aura-sec/recovery/drill", response_class=HTMLResponse, include_in_schema=False)
def recovery_drill_page(request: Request):
    user, redirect = _require(request)
    if redirect:
        return redirect
    devices = [item for item in security.list_devices(user["id"]) if item.get("status") != "revoked"]
    drills = read_model.restore_drills(user["id"], limit=100)
    cards = []
    for device in devices:
        state = recovery.readiness(user["id"], device["id"])
        cards.append(
            f"<div class='card'><div class='eyebrow'>{escape(str(device.get('display_name') or 'Device'))}</div><h3>{escape(_label(state['state']))}</h3><p>{escape(state['truth'])}</p><div class='meta'>Restore proven {'Yes' if state['restore_proven'] else 'No'} · Active targets {state['active_targets']}</div><span class='action disabled'>Start drill — native workflow not released</span></div>"
        )
    device_cards = "".join(cards) or "<div class='empty'>No enrolled device is available for a restore drill.</div>"
    body = f"""<div class='grid'><div class='card metric'><strong>{len(devices)}</strong><span>eligible enrolled devices</span></div><div class='card metric'><strong>{len(drills)}</strong><span>restore drill records</span></div><div class='card metric'><strong>{sum(1 for d in devices if recovery.readiness(user['id'], d['id'])['state'] == 'ransomware_ready')}</strong><span>ransomware-ready devices</span></div></div><section class='section'><div class='eyebrow'>Recovery verification</div><h2>Restore-drill readiness</h2><div class='grid2'>{device_cards}</div></section><section class='section panel'><div class='eyebrow'>Required before a drill can launch</div><ul><li>Active Aura Sec licence and enrolled signed native client.</li><li>Verified encrypted recovery target and integrity-checked recovery point.</li><li>Explicit member approval for disruptive restore operations.</li><li>Strong re-authentication for a real recovery-point restore.</li><li>Post-restore integrity re-verification before the drill can count as successful.</li></ul></section><section class='section truth'><b>No browser-only restore.</b> These controls remain disabled until the native recovery workflow and approval gateway are production-ready.</section>"""
    return _shell("Restore Drills", body, active="/aura-sec/recovery/drill")


__all__ = ["router"]
