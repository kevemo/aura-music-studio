from __future__ import annotations

from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .accounts import AccountStore
from .aura_sec_catalog import AuraSecCatalog
from .aura_sec_health import evaluate_device_health, summarize_security_health
from .aura_sec_read_model import AuraSecReadModel
from .aura_sec_recovery import AuraSecRecoveryStore
from .aura_sec_store import AuraSecStore
from .aura_sec_vulnerability_store import AuraSecVulnerabilityStore

router = APIRouter(tags=["Aura Sec Portal"])
accounts = AccountStore()
security = AuraSecStore(accounts)
recovery = AuraSecRecoveryStore(accounts, security)
read_model = AuraSecReadModel(accounts)
vulnerabilities = AuraSecVulnerabilityStore(accounts, security)
MEMBER_COOKIE = "lss_session"

MASTER_PRODUCT_NAME = "Elevate Souls Productions Content Creation Command Center"
MASTER_ENDORSEMENT = "Powered by Aura AI"
AURA_SEC_NAME = "Aura Sec"


def _page_user(request: Request) -> dict | None:
    return accounts.resolve_session(request.cookies.get(MEMBER_COOKIE))


def _api_user(request: Request) -> dict:
    token = request.cookies.get(MEMBER_COOKIE)
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    user = accounts.resolve_session(token)
    if not user:
        raise HTTPException(401, "Sign in required")
    return user


def _secure_html(content: str, *, status_code: int = 200) -> HTMLResponse:
    response = HTMLResponse(content, status_code=status_code)
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
    ("/aura-sec/devices", "My Devices"),
    ("/aura-sec/enrol", "Enrol Device"),
    ("/aura-sec/threats", "Threats & Incidents"),
    ("/aura-sec/vulnerabilities", "Vulnerabilities"),
    ("/aura-sec/recovery", "Recovery"),
    ("/aura-sec/optimizer", "Optimisation"),
    ("/aura-sec/downloads", "Protection Downloads"),
    ("/aura-sec/plans", "Plans & Licence"),
    ("/aura-sec/settings", "Security Settings"),
)


def _state_label(value: object) -> str:
    return str(value or "unknown").replace("_", " ").title()


def _tone(value: object) -> str:
    state = str(value or "").lower()
    if state in {"healthy", "active", "ransomware_ready", "verified", "success", "clean", "resolved", "low"}:
        return "good"
    if state in {"critical", "emergency", "incident", "high", "attention_required", "degraded", "stale", "failed", "infected"}:
        return "danger"
    if state in {"medium", "proposed", "updating", "restore_test_required", "awaiting_verified_heartbeat", "suspicious"}:
        return "warn"
    return "neutral"


def _shell(*, title: str, active_path: str, body: str, subtitle: str = "", status_code: int = 200) -> HTMLResponse:
    nav = "".join(
        f"<a class='navitem {'active' if path == active_path else ''}' href='{path}'>{escape(label)}</a>"
        for path, label in _NAV
    )
    page = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'><meta name='robots' content='noindex,nofollow'><title>{escape(title)} · {escape(AURA_SEC_NAME)}</title><style>
:root{{--bg:#02060a;--panel:#081119;--panel2:#0c1821;--line:#ffffff18;--text:#f7fbff;--muted:#9fb0bf;--cyan:#65ecff;--gold:#f6cf79;--violet:#ad8cff;--good:#7ae6a5;--warn:#ffd477;--danger:#ff8a9b}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 8% 0,#11374f,transparent 30%),radial-gradient(circle at 92% 2%,#34194d,transparent 28%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.layout{{display:grid;grid-template-columns:280px minmax(0,1fr);min-height:100vh}}aside{{border-right:1px solid var(--line);background:#03090ef2;padding:22px 16px;position:sticky;top:0;height:100vh;overflow:auto}}.brand{{font-weight:950;line-height:1.12;font-size:.92rem}}.brand small{{display:block;color:var(--gold);font-size:.63rem;letter-spacing:.12em;text-transform:uppercase;margin-top:7px}}.product{{margin:28px 0 12px;font-weight:950;font-size:1.5rem;background:linear-gradient(90deg,#fff,var(--cyan),var(--violet));background-clip:text;color:transparent}}nav{{display:grid;gap:6px}}.navitem{{padding:10px 12px;border:1px solid transparent;border-radius:12px;color:var(--muted);font-size:.83rem;font-weight:800}}.navitem:hover,.navitem.active{{color:#fff;border-color:#65ecff38;background:#65ecff0c}}.back{{display:block;margin-top:24px;padding:10px 12px;border:1px solid var(--line);border-radius:12px;color:var(--muted);font-size:.8rem}}main{{padding:30px clamp(18px,4vw,58px) 64px;min-width:0}}.eyebrow{{font-size:.7rem;letter-spacing:.14em;text-transform:uppercase;font-weight:950;color:var(--cyan)}}h1{{font-size:clamp(2.4rem,5vw,4.8rem);letter-spacing:-.055em;line-height:.95;margin:.17em 0}}h2{{font-size:clamp(1.4rem,2.5vw,2rem);margin:6px 0 14px}}h3{{margin:5px 0 8px}}p{{color:var(--muted);line-height:1.58}}.sub{{max-width:850px;margin:0 0 26px}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}.grid2{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.card,.panel{{border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,#0e1a23e8,#071017eb);padding:17px}}.metric strong{{font-size:1.6rem;display:block}}.metric span{{font-size:.75rem;color:var(--muted)}}.section{{margin-top:30px}}.row{{display:flex;align-items:center;justify-content:space-between;gap:16px;border-top:1px solid var(--line);padding:13px 0}}.row:first-child{{border-top:0;padding-top:0}}.row:last-child{{padding-bottom:0}}.rowmain{{min-width:0}}.rowmain b{{display:block}}.meta{{font-size:.73rem;color:var(--muted);margin-top:4px;line-height:1.45}}.pill{{display:inline-flex;align-items:center;border-radius:999px;padding:5px 9px;font-size:.68rem;font-weight:900;text-transform:uppercase;letter-spacing:.05em;border:1px solid var(--line);white-space:nowrap}}.pill.good{{color:var(--good);border-color:#7ae6a54a;background:#7ae6a50b}}.pill.warn{{color:var(--warn);border-color:#ffd4774a;background:#ffd4770b}}.pill.danger{{color:var(--danger);border-color:#ff8a9b4a;background:#ff8a9b0b}}.pill.neutral{{color:var(--muted)}}.empty{{border:1px dashed #ffffff24;border-radius:18px;padding:26px;text-align:center;background:#ffffff04}}.empty strong{{display:block;margin-bottom:6px}}.truth{{border:1px solid #ffd4773d;border-radius:16px;padding:15px;background:#ffd47709;color:var(--muted);font-size:.84rem;line-height:1.55}}.truth b{{color:var(--warn)}}.action{{display:inline-flex;padding:9px 12px;border-radius:11px;border:1px solid var(--line);font-weight:850;font-size:.78rem;background:#ffffff07}}.action.primary{{background:linear-gradient(105deg,var(--cyan),#b7a4ff,var(--gold));color:#071016;border:0}}.actions{{display:flex;gap:9px;flex-wrap:wrap;margin-top:14px}}.bar{{height:8px;border-radius:999px;background:#ffffff0c;overflow:hidden;margin-top:9px}}.bar i{{display:block;height:100%;background:linear-gradient(90deg,var(--cyan),var(--violet),var(--gold));border-radius:inherit}}.checklist{{display:grid;gap:9px}}.check{{display:flex;gap:10px;align-items:flex-start;border:1px solid var(--line);border-radius:13px;padding:12px;background:#ffffff04}}.check span{{font-size:.82rem;color:var(--muted)}}.download{{display:grid;grid-template-columns:1fr auto;gap:16px;align-items:center;border-top:1px solid var(--line);padding:14px 0}}.download:first-child{{border-top:0}}.disabled{{opacity:.55;cursor:not-allowed}}.kv{{display:grid;grid-template-columns:minmax(150px,.35fr) 1fr;gap:9px 14px;padding:8px 0;border-top:1px solid var(--line)}}.kv:first-child{{border-top:0}}.kv b{{font-size:.76rem}}.kv span{{color:var(--muted);font-size:.8rem;word-break:break-word}}.reasons{{margin:0;padding-left:20px;color:var(--muted)}}.reasons li{{margin:8px 0;line-height:1.5}}.price{{font-size:2rem;font-weight:950;margin:6px 0}}.footer{{margin-top:38px;padding-top:20px;border-top:1px solid var(--line);color:var(--muted);font-size:.72rem}}@media(max-width:980px){{.layout{{grid-template-columns:1fr}}aside{{position:relative;height:auto;border-right:0;border-bottom:1px solid var(--line)}}nav{{grid-template-columns:repeat(3,minmax(0,1fr))}}.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}@media(max-width:620px){{main{{padding:22px 14px 50px}}nav{{grid-template-columns:1fr 1fr}}.grid,.grid2{{grid-template-columns:1fr}}.row,.download,.kv{{align-items:flex-start;grid-template-columns:1fr;flex-direction:column}}}}
</style></head><body><div class='layout'><aside><a class='brand' href='/dashboard'>{escape(MASTER_PRODUCT_NAME)}<small>{escape(MASTER_ENDORSEMENT)}</small></a><div class='product'>Aura Sec</div><nav>{nav}</nav><a class='back' href='/dashboard'>← Back to member dashboard</a></aside><main><div class='eyebrow'>Aura Sec · Member Security</div><h1>{escape(title)}</h1>{f'<p class="sub">{escape(subtitle)}</p>' if subtitle else ''}{body}<div class='footer'>Aura Sec is a separate security product inside the {escape(MASTER_PRODUCT_NAME)}. Protection claims require a signed native client and fresh verified endpoint evidence.</div></main></div></body></html>"""
    return _secure_html(page, status_code=status_code)


def _require_page_user(request: Request):
    user = _page_user(request)
    if not user:
        return None, RedirectResponse(f"/signin?next={request.url.path}", status_code=303)
    return user, None


def _safe_detail_rows(values: dict) -> str:
    if not values:
        return "<div class='empty'><strong>No structured evidence fields recorded.</strong><p>The incident still exists, but its verified summary contains no additional structured fields.</p></div>"
    rows = []
    for key, value in sorted(values.items(), key=lambda item: str(item[0]))[:40]:
        text = str(value)
        if len(text) > 600:
            text = text[:597] + "..."
        rows.append(
            f"<div class='kv'><b>{escape(_state_label(key))}</b><span>{escape(text)}</span></div>"
        )
    return "".join(rows)


@router.get("/api/aura-sec/member/security-center")
def security_center_api(request: Request):
    user = _api_user(request)
    devices = security.list_devices(user["id"])
    health = summarize_security_health(devices)
    licence = security.licence(user["id"])
    counts = read_model.counts(user["id"])
    readiness = []
    for device in devices:
        if device.get("status") != "revoked":
            readiness.append({"device_id": device["id"], **recovery.readiness(user["id"], device["id"])})
    return {
        "licence": licence,
        "health": health,
        "counts": counts,
        "recovery": readiness,
        "truth": "This endpoint is read-only and summarizes persisted verified state; loading it does not alter device protection.",
    }


@router.get("/aura-sec/devices", response_class=HTMLResponse, include_in_schema=False)
def aura_sec_devices_page(request: Request):
    user, redirect = _require_page_user(request)
    if redirect:
        return redirect
    devices = security.list_devices(user["id"])
    licence = security.licence(user["id"])
    health = summarize_security_health(devices)

    if devices:
        rows = "".join(
            f"<div class='row'><div class='rowmain'><b>{escape(str(device.get('display_name') or 'Unnamed device'))}</b>"
            f"<div class='meta'>{escape(_state_label(device.get('platform')))} · {escape(str(device.get('architecture') or 'unknown architecture'))} · Agent {escape(str(device.get('agent_version') or 'not reporting'))}</div>"
            f"<div class='meta'>{escape(evaluate_device_health(device).reason)}</div></div>"
            f"<span class='pill {_tone(evaluate_device_health(device).health)}'>{escape(_state_label(evaluate_device_health(device).health))}</span></div>"
            for device in devices
        )
    else:
        rows = "<div class='empty'><strong>No enrolled devices yet.</strong><p>A website session is not an endpoint enrolment. A signed Aura Sec client must complete the verified enrolment flow before a device appears here.</p></div>"

    body = f"""<div class='grid'><div class='card metric'><strong>{health['managed_devices']}</strong><span>managed devices</span></div><div class='card metric'><strong>{health['healthy_devices']}</strong><span>currently verified healthy</span></div><div class='card metric'><strong>{health['attention_devices']}</strong><span>requiring attention</span></div></div><div class='actions'><a class='action primary' href='/aura-sec/enrol'>Enrol a device</a><a class='action' href='/aura-sec/vulnerabilities'>View vulnerabilities</a></div><section class='section panel'><div class='eyebrow'>Device fleet</div><h2>Verified endpoint state</h2>{rows}</section><section class='section truth'><b>Licence: {escape(_state_label(licence.get('status')))}</b> · Creative membership does not activate Aura Sec. Device health is derived from fresh verified heartbeat evidence.</section>"""
    return _shell(title="My Devices", active_path="/aura-sec/devices", subtitle="Every protected device must independently prove its current state to the Command Center.", body=body)


@router.get("/aura-sec/enrol", response_class=HTMLResponse, include_in_schema=False)
def aura_sec_enrol_page(request: Request):
    user, redirect = _require_page_user(request)
    if redirect:
        return redirect
    licence = security.licence(user["id"])
    devices = [item for item in security.list_devices(user["id"]) if item.get("status") != "revoked"]
    limit = int(licence.get("device_limit") or 0)
    remaining = max(0, limit - len(devices))
    licence_active = licence.get("status") == "active"
    if not licence_active:
        callout = "<div class='truth'><b>A separate Aura Sec licence is required.</b> Creative Free, Basic or Pro membership does not create a security entitlement.</div><div class='actions'><a class='action primary' href='/aura-sec/plans'>View Aura Sec plans</a></div>"
    elif remaining <= 0:
        callout = "<div class='truth'><b>Your current device allowance is full.</b> Revoke an old device or change to an approved security plan with a larger allowance before starting another enrolment.</div>"
    else:
        callout = f"<div class='truth'><b>{remaining} device slot(s) available.</b> The native client will create a device key, request a short-lived one-time challenge, prove possession/attestation, and only then create the enrolled device record.</div>"
    body = f"""<div class='grid'><div class='card metric'><strong>{escape(_state_label(licence.get('status')))}</strong><span>Aura Sec licence</span></div><div class='card metric'><strong>{len(devices)}/{limit}</strong><span>active device allowance used</span></div><div class='card metric'><strong>0</strong><span>signed native client families released</span></div></div><section class='section panel'><div class='eyebrow'>Verified enrolment</div><h2>How a device becomes trusted</h2><div class='checklist'><div class='check'><b>1</b><span>Install a signed Aura Sec client from the verified Downloads page when that platform release is available.</span></div><div class='check'><b>2</b><span>The client generates its device identity/key locally and requests a short-lived one-time enrolment challenge.</span></div><div class='check'><b>3</b><span>The native proof/attestation service verifies possession. The browser cannot mark this step successful.</span></div><div class='check'><b>4</b><span>The new device initially appears as Awaiting Verified Heartbeat. Only fresh signed telemetry can later make it healthy.</span></div></div></section>{callout}<section class='section empty'><strong>Browser-only enrolment is deliberately unavailable.</strong><p>There is no checkbox or form that can pretend device proof was verified. The enrolment completion path belongs to the signed native client and attestation verifier.</p><div class='actions' style='justify-content:center'><a class='action' href='/aura-sec/downloads'>Protection downloads</a></div></section>"""
    return _shell(title="Enrol Device", active_path="/aura-sec/enrol", subtitle="A member account can authorise enrolment, but only the native client can prove the device identity.", body=body)


@router.get("/aura-sec/threats", response_class=HTMLResponse, include_in_schema=False)
def aura_sec_threats_page(request: Request):
    user, redirect = _require_page_user(request)
    if redirect:
        return redirect
    incidents = read_model.incidents(user["id"], limit=100)
    actions = read_model.actions(user["id"], limit=100)
    counts = read_model.counts(user["id"])

    incident_rows = "".join(
        f"<div class='row'><div class='rowmain'><b><a href='/aura-sec/incident/{escape(str(item['id']))}'>{escape(str(item['title']))}</a></b><div class='meta'>{escape(str(item['display_name']))} · {escape(str(item['platform']))} · {escape(str(item['created_at']))}</div>"
        f"<div class='meta'>Detection {escape(str(item.get('detection_id') or 'unassigned'))} · Confidence {escape(str(item.get('confidence') if item.get('confidence') is not None else 'not scored'))}</div></div>"
        f"<span class='pill {_tone(item['severity'])}'>{escape(str(item['severity']).upper())}</span></div>"
        for item in incidents
    ) or "<div class='empty'><strong>No verified security incidents recorded.</strong><p>Aura Sec will not fabricate threats to make the dashboard look active. Incidents appear only after a verified detection source records evidence.</p></div>"

    action_rows = "".join(
        f"<div class='row'><div class='rowmain'><b>{escape(_state_label(item['action_type']))}</b><div class='meta'>{escape(str(item['display_name']))} · Risk: {escape(_state_label(item['risk_class']))}</div><div class='meta'>Requested {escape(str(item['requested_at']))}</div></div>"
        f"<span class='pill {_tone(item['status'])}'>{escape(_state_label(item['status']))}</span></div>"
        for item in actions
    ) or "<div class='empty'><strong>No remediation actions recorded.</strong><p>Aura recommendations become device actions only through the bounded approval and command-delivery protocol.</p></div>"

    body = f"""<div class='grid'><div class='card metric'><strong>{counts['incidents']['open']}</strong><span>open incidents</span></div><div class='card metric'><strong>{counts['incidents']['urgent']}</strong><span>open high / critical</span></div><div class='card metric'><strong>{counts['actions']['awaiting_approval']}</strong><span>actions awaiting approval</span></div></div><section class='section panel'><div class='eyebrow'>Threat timeline</div><h2>Verified incidents</h2>{incident_rows}</section><section class='section panel'><div class='eyebrow'>Response ledger</div><h2>Aura remediation actions</h2>{action_rows}</section>"""
    return _shell(title="Threats & Incidents", active_path="/aura-sec/threats", subtitle="Evidence first: threat detections, confidence and response state remain separate from Aura's explanation layer.", body=body)


@router.get("/aura-sec/incident/{incident_id}", response_class=HTMLResponse, include_in_schema=False)
def aura_sec_incident_detail_page(incident_id: str, request: Request):
    user, redirect = _require_page_user(request)
    if redirect:
        return redirect
    incident = read_model.incident(user["id"], incident_id)
    if not incident:
        return _shell(title="Incident not found", active_path="/aura-sec/threats", subtitle="The requested incident does not exist in this member's verified security record.", body="<div class='empty'><strong>No incident is available at this identifier.</strong></div>", status_code=404)
    actions = read_model.actions_for_incident(user["id"], incident_id, limit=100)
    action_rows = "".join(
        f"<div class='row'><div class='rowmain'><b>{escape(_state_label(item['action_type']))}</b><div class='meta'>Risk {escape(_state_label(item['risk_class']))} · Requested {escape(str(item['requested_at']))}</div><div class='meta'>Approved {escape(str(item.get('approved_at') or 'No'))} · Executed {escape(str(item.get('executed_at') or 'No'))} · Verified {escape(str(item.get('verified_at') or 'No'))}</div></div><span class='pill {_tone(item['status'])}'>{escape(_state_label(item['status']))}</span></div>"
        for item in actions
    ) or "<div class='empty'><strong>No remediation action has been recorded for this incident.</strong><p>Aura may explain risk, but no endpoint command exists until the bounded action/approval path creates one.</p></div>"
    confidence = "Not scored" if incident.get("confidence") is None else str(incident.get("confidence"))
    body = f"""<div class='grid'><div class='card'><div class='eyebrow'>Severity</div><h3><span class='pill {_tone(incident['severity'])}'>{escape(str(incident['severity']).upper())}</span></h3></div><div class='card'><div class='eyebrow'>Incident state</div><h3>{escape(_state_label(incident['status']))}</h3></div><div class='card'><div class='eyebrow'>Detection confidence</div><h3>{escape(confidence)}</h3></div></div><section class='section panel'><div class='eyebrow'>Verified event</div><h2>{escape(str(incident['title']))}</h2><div class='kv'><b>Device</b><span>{escape(str(incident['display_name']))} · {escape(_state_label(incident['platform']))} · {escape(str(incident.get('architecture') or 'unknown'))}</span></div><div class='kv'><b>Detection ID</b><span>{escape(str(incident.get('detection_id') or 'Not assigned'))}</span></div><div class='kv'><b>Created</b><span>{escape(str(incident['created_at']))}</span></div><div class='kv'><b>Last updated</b><span>{escape(str(incident['updated_at']))}</span></div><div class='kv'><b>Endpoint state</b><span>{escape(_state_label(incident.get('protection_state')))} · Agent {escape(str(incident.get('agent_version') or 'not reporting'))}</span></div></section><section class='section panel'><div class='eyebrow'>Evidence summary</div><h2>What Aura can ground its explanation on</h2>{_safe_detail_rows(incident.get('summary') or {})}</section><section class='section panel'><div class='eyebrow'>Aura Security Guide</div><h2>Evidence before automation</h2><p>Aura can explain this incident using the verified facts above and can propose only bounded security actions. It cannot invent a successful containment, lower a fixed risk class, or turn incident text into a shell command.</p><div class='truth'><b>Current response truth:</b> {len(actions)} linked response action(s) are recorded. An action is not complete merely because it was requested; execution and verification remain separate states.</div></section><section class='section panel'><div class='eyebrow'>Response timeline</div><h2>Bounded remediation</h2>{action_rows}</section><div class='actions'><a class='action' href='/aura-sec/threats'>← All incidents</a></div>"""
    return _shell(title="Incident Detail", active_path="/aura-sec/threats", subtitle="A member-owned evidence view with Aura guidance constrained by the verified incident and response ledger.", body=body)


@router.get("/aura-sec/vulnerabilities", response_class=HTMLResponse, include_in_schema=False)
def aura_sec_vulnerabilities_page(request: Request):
    user, redirect = _require_page_user(request)
    if redirect:
        return redirect
    findings = vulnerabilities.list(user["id"], status="open", limit=500)
    urgent = sum(1 for item in findings if item.get("priority") in {"critical", "emergency", "incident"})
    kev = sum(1 for item in findings if item.get("cisa_kev"))
    rows = "".join(
        f"<div class='row'><div class='rowmain'><b><a href='/aura-sec/vulnerability/{escape(str(item['id']))}'>{escape(str(item['cve_id']))} · {escape(str(item['product']))}</a></b><div class='meta'>{escape(str(item['display_name']))} · Installed {escape(str(item['installed_version']))} · Fixed {escape(str(item.get('fixed_version') or 'not verified'))}</div><div class='meta'>CVSS {escape(str(item['cvss_base']))} · EPSS {escape(str(item['epss_probability']))} · CISA KEV {'Yes' if item['cisa_kev'] else 'No'}</div></div><span class='pill {_tone(item['priority'])}'>{escape(_state_label(item['priority']))} · {int(item['priority_score'])}</span></div>"
        for item in findings
    ) or "<div class='empty'><strong>No open verified vulnerabilities are recorded.</strong><p>The browser does not infer installed software. Findings appear only after signed native inventory evidence is correlated with threat-intelligence evidence.</p></div>"
    body = f"""<div class='grid'><div class='card metric'><strong>{len(findings)}</strong><span>open verified findings</span></div><div class='card metric'><strong>{urgent}</strong><span>critical / emergency / incident</span></div><div class='card metric'><strong>{kev}</strong><span>CISA known-exploited findings</span></div></div><section class='section panel'><div class='eyebrow'>Patch intelligence</div><h2>Device-applicable vulnerabilities</h2>{rows}</section><section class='section truth'><b>Priority is advisory, not patch permission.</b> Aura combines technical severity with exploitation evidence and device context, but patching still requires authoritative package/signature verification, compatibility checks and the configured approval policy.</section>"""
    return _shell(title="Vulnerabilities", active_path="/aura-sec/vulnerabilities", subtitle="Verified installed-version findings prioritized using CVSS, exploitation evidence and the device's actual exposure/context.", body=body)


@router.get("/aura-sec/vulnerability/{finding_id}", response_class=HTMLResponse, include_in_schema=False)
def aura_sec_vulnerability_detail_page(finding_id: str, request: Request):
    user, redirect = _require_page_user(request)
    if redirect:
        return redirect
    try:
        finding = vulnerabilities.get(user["id"], finding_id)
    except ValueError:
        return _shell(title="Vulnerability not found", active_path="/aura-sec/vulnerabilities", subtitle="The requested finding does not exist in this member's verified vulnerability ledger.", body="<div class='empty'><strong>No verified finding is available at this identifier.</strong></div>", status_code=404)
    reasons = "".join(f"<li>{escape(str(reason))}</li>" for reason in finding.get("reasons", [])) or "<li>No additional priority reasons were recorded.</li>"
    patch_truth = (
        f"A fixed version ({escape(str(finding.get('fixed_version')))}) is recorded as available. Aura still cannot call this resolved until the native client verifies the installed version is fixed."
        if finding.get("patch_available")
        else "No verified fixed version is recorded. Aura should recommend compensating controls rather than invent a patch."
    )
    body = f"""<div class='grid'><div class='card'><div class='eyebrow'>Priority</div><h3><span class='pill {_tone(finding['priority'])}'>{escape(_state_label(finding['priority']))} · {int(finding['priority_score'])}/100</span></h3></div><div class='card'><div class='eyebrow'>CISA KEV</div><h3>{'Known exploited' if finding['cisa_kev'] else 'Not currently recorded in KEV'}</h3></div><div class='card'><div class='eyebrow'>EPSS probability</div><h3>{escape(str(finding['epss_probability']))}</h3></div></div><section class='section panel'><div class='eyebrow'>Verified finding</div><h2>{escape(str(finding['cve_id']))} · {escape(str(finding['product']))}</h2><div class='kv'><b>Device</b><span>{escape(str(finding.get('display_name') or finding['device_id']))} · {escape(_state_label(finding.get('platform')))}</span></div><div class='kv'><b>Installed version</b><span>{escape(str(finding['installed_version']))}</span></div><div class='kv'><b>Fixed version</b><span>{escape(str(finding.get('fixed_version') or 'Not verified'))}</span></div><div class='kv'><b>CVSS base</b><span>{escape(str(finding['cvss_base']))}/10</span></div><div class='kv'><b>Exposure</b><span>{escape(_state_label(finding['exposure']))}</span></div><div class='kv'><b>Privileges required</b><span>{escape(_state_label(finding['privilege_required']))}</span></div><div class='kv'><b>Asset sensitivity</b><span>{escape(_state_label(finding['asset_sensitivity']))}</span></div><div class='kv'><b>Local exploit evidence</b><span>{'Observed' if finding['exploit_observed_locally'] else 'Not recorded'}</span></div><div class='kv'><b>Last verified</b><span>{escape(str(finding['last_verified_at']))}</span></div></section><section class='section panel'><div class='eyebrow'>Aura reasoning</div><h2>Why this is prioritised here</h2><ul class='reasons'>{reasons}</ul></section><section class='section truth'><b>Remediation remains approval-gated.</b> {patch_truth}</section><div class='actions'><a class='action' href='/aura-sec/vulnerabilities'>← All vulnerabilities</a></div>"""
    return _shell(title="Vulnerability Detail", active_path="/aura-sec/vulnerabilities", subtitle="Traceable prioritisation from verified inventory and intelligence evidence, without turning a score into automatic permission to patch.", body=body)


@router.get("/aura-sec/recovery", response_class=HTMLResponse, include_in_schema=False)
def aura_sec_recovery_page(request: Request):
    user, redirect = _require_page_user(request)
    if redirect:
        return redirect
    devices = [item for item in security.list_devices(user["id"]) if item.get("status") != "revoked"]
    targets = read_model.backup_targets(user["id"])
    points = read_model.recovery_points(user["id"], limit=50)
    drills = read_model.restore_drills(user["id"], limit=50)

    readiness_cards = []
    ransomware_ready_count = 0
    for device in devices:
        state = recovery.readiness(user["id"], device["id"])
        if state["state"] == "ransomware_ready":
            ransomware_ready_count += 1
        readiness_cards.append(
            f"<article class='card'><div class='eyebrow'>{escape(str(device.get('display_name') or 'Device'))}</div><h3>{escape(_state_label(state['state']))}</h3><p>{escape(state['truth'])}</p>"
            f"<div class='meta'>Targets {state['active_targets']} · Restore proven {'Yes' if state['restore_proven'] else 'No'} · Immutable/isolated {'Yes' if state['isolated_or_immutable_target_present'] else 'No'}</div></article>"
        )
    readiness_html = "".join(readiness_cards) or "<div class='empty'><strong>No recovery-capable enrolled devices yet.</strong><p>Recovery readiness can only be calculated after an Aura Sec client is enrolled.</p></div>"

    target_rows = "".join(
        f"<div class='row'><div class='rowmain'><b>{escape(str(item['display_name']))}</b><div class='meta'>{escape(_state_label(item['target_type']))} · Encrypted {'Yes' if item['encrypted'] else 'No'} · Isolated/immutable {'Yes' if item['isolated_or_immutable'] else 'No'}</div></div><span class='pill {_tone(item['status'])}'>{escape(_state_label(item['status']))}</span></div>"
        for item in targets
    ) or "<div class='empty'><strong>No verified backup targets.</strong><p>Configuring cloud storage alone does not count as proven ransomware recovery.</p></div>"

    body = f"""<div class='grid'><div class='card metric'><strong>{len(targets)}</strong><span>verified backup targets</span></div><div class='card metric'><strong>{len(points)}</strong><span>verified recovery points retained in view</span></div><div class='card metric'><strong>{ransomware_ready_count}/{len(devices)}</strong><span>devices currently ransomware-ready</span></div></div><section class='section'><div class='eyebrow'>Recovery posture</div><h2>Proof, not a backup checkbox</h2><div class='grid2'>{readiness_html}</div></section><section class='section panel'><div class='eyebrow'>Recovery destinations</div><h2>Verified targets</h2>{target_rows}</section><section class='section truth'><b>{len(drills)} restore drill record(s) in the current view.</b> Aura Sec only calls recovery proven when integrity has been reverified after an actual restore drill.</section>"""
    return _shell(title="Recovery", active_path="/aura-sec/recovery", subtitle="Ransomware resilience requires clean, fresh, integrity-verified recovery points and successful restore drills.", body=body)


@router.get("/aura-sec/optimizer", response_class=HTMLResponse, include_in_schema=False)
def aura_sec_optimizer_page(request: Request):
    user, redirect = _require_page_user(request)
    if redirect:
        return redirect
    devices = security.list_devices(user["id"])
    available = sum(1 for item in devices if evaluate_device_health(item).health == "healthy")
    body = f"""<div class='grid'><div class='card metric'><strong>{available}</strong><span>healthy device(s) eligible for future optimiser scans</span></div><div class='card metric'><strong>0 B</strong><span>verified reclaimable storage reported</span></div><div class='card metric'><strong>0</strong><span>approved optimisation changes pending</span></div></div><section class='section panel'><div class='eyebrow'>Aura Storage Assistant</div><h2>Safe optimisation workflow</h2><div class='checklist'><div class='check'><b>1</b><span>Native client inventories temporary files, cache, duplicates, large files, startup impact and storage pressure locally.</span></div><div class='check'><b>2</b><span>Aura explains each candidate, expected gain and whether it is reversible.</span></div><div class='check'><b>3</b><span>Personal files, project assets and startup changes require member approval. Active project references block unsafe moves.</span></div><div class='check'><b>4</b><span>Security, backup and accessibility components can never be disabled merely to improve a performance score.</span></div></div></section><section class='section empty'><strong>No native optimiser report has been received yet.</strong><p>The page deliberately shows zero verified reclaimable storage rather than inventing a cleanup figure from the web browser.</p></section>"""
    return _shell(title="Optimisation", active_path="/aura-sec/optimizer", subtitle="Aura organises and improves devices without turning into an unsafe one-click cleaner.", body=body)


@router.get("/aura-sec/downloads", response_class=HTMLResponse, include_in_schema=False)
def aura_sec_downloads_page(request: Request):
    user, redirect = _require_page_user(request)
    if redirect:
        return redirect
    licence = security.licence(user["id"])
    targets = (
        ("Windows 11", "x64 / ARM64", "Endpoint + EDR + recovery + network + optimisation"),
        ("macOS", "Apple silicon / Intel", "Endpoint Security + Network Extension + recovery"),
        ("Linux", "x86_64 / ARM64", "Endpoint + package posture + network protection"),
        ("Android", "Supported Android", "Web/scam + VPN/DNS + privacy + vault"),
        ("iPhone + iPad", "iOS / iPadOS", "Network/privacy/identity/vault within Apple sandbox"),
        ("Browser Guard", "Chrome / Edge / Firefox", "Phishing + malicious-link + download protection"),
        ("ChromeOS", "Supported Chromebook", "Browser + identity + secure DNS/VPN"),
    )
    rows = "".join(
        f"<div class='download'><div><b>{escape(name)}</b><div class='meta'>{escape(arch)} · {escape(scope)}</div></div><span class='action disabled'>Not released</span></div>"
        for name, arch, scope in targets
    )
    body = f"""<div class='truth'><b>Licence state: {escape(_state_label(licence.get('status')))}</b>. Downloads remain disabled regardless of licence until a platform package has a verified signed release manifest, SHA-256, supported-OS declaration, SBOM/provenance evidence and trusted distribution URL.</div><section class='section panel'><div class='eyebrow'>Native protection</div><h2>Signed application downloads</h2>{rows}</section><section class='section panel'><div class='eyebrow'>Release integrity</div><h2>What must verify before download</h2><div class='checklist'><div class='check'><b>✓</b><span>Trusted release-signing identity and non-expired signed manifest.</span></div><div class='check'><b>✓</b><span>Artifact version, architecture, file size and SHA-256 bound into release metadata.</span></div><div class='check'><b>✓</b><span>Platform code signing/notarisation in addition to Aura Sec update signatures.</span></div><div class='check'><b>✓</b><span>SBOM and build-provenance references retained for release auditing.</span></div></div></section>"""
    return _shell(title="Protection Downloads", active_path="/aura-sec/downloads", subtitle="No unsigned placeholders. A download appears only when a real native release passes the security gate.", body=body)


@router.get("/aura-sec/plans", response_class=HTMLResponse, include_in_schema=False)
def aura_sec_plans_page(request: Request):
    user, redirect = _require_page_user(request)
    if redirect:
        return redirect
    licence = security.licence(user["id"])
    try:
        catalog = AuraSecCatalog.from_environment().public_state()
        config_error = None
    except ValueError as exc:
        catalog = {"sale_configured": False, "skus": [], "checkout_enabled": False}
        config_error = str(exc)
    if catalog.get("skus"):
        cards = "".join(
            f"<article class='card'><div class='eyebrow'>{escape(str(sku['id']))}</div><h3>{escape(str(sku['display_name']))}</h3><div class='price'>{escape(str(sku['price_major']))} {escape(str(sku['currency']))}</div><p>{int(sku['period_days'])} day licence · up to {int(sku['device_limit'])} device(s).</p><span class='action disabled'>Checkout not connected</span></article>"
            for sku in catalog["skus"]
        )
    else:
        cards = "<div class='empty'><strong>Aura Sec is not yet available for purchase.</strong><p>No owner-approved security SKU is configured, so the site does not invent a price or borrow a creative membership price.</p></div>"
    error_html = f"<section class='section truth'><b>Configuration remains fail-closed.</b> {escape(config_error)}</section>" if config_error else ""
    body = f"""<div class='grid'><div class='card metric'><strong>{escape(_state_label(licence.get('status')))}</strong><span>your Aura Sec licence</span></div><div class='card metric'><strong>{'Yes' if catalog.get('sale_configured') else 'No'}</strong><span>approved security SKU configured</span></div><div class='card metric'><strong>{'Yes' if catalog.get('checkout_enabled') else 'No'}</strong><span>verified checkout enabled</span></div></div><section class='section'><div class='eyebrow'>Standalone security purchase</div><h2>Aura Sec plans</h2><div class='grid'>{cards}</div></section><section class='section truth'><b>Creative plans remain separate.</b> Free, Basic and Pro Content Creation Command Center memberships cannot be submitted as Aura Sec SKU IDs. A configured price still does not activate checkout until the verified payment flow is explicitly connected.</section>{error_html}"""
    return _shell(title="Plans & Licence", active_path="/aura-sec/plans", subtitle="A separate security entitlement with owner-approved pricing and a payment-verification gate.", body=body)


@router.get("/aura-sec/settings", response_class=HTMLResponse, include_in_schema=False)
def aura_sec_settings_page(request: Request):
    user, redirect = _require_page_user(request)
    if redirect:
        return redirect
    licence = security.licence(user["id"])
    devices = security.list_devices(user["id"])
    body = f"""<div class='grid'><div class='card'><div class='eyebrow'>Subscription boundary</div><h3>{escape(_state_label(licence.get('status')))}</h3><p>Aura Sec remains separate from creative membership entitlements. Security access cannot be inferred from Free, Basic or Pro.</p></div><div class='card'><div class='eyebrow'>Approval policy</div><h3>Human-controlled</h3><p>Quarantine, process termination, network isolation, patching and personal-file changes require approval. Wipe, recovery-key and device-credential changes require strong re-authentication.</p></div><div class='card'><div class='eyebrow'>Privacy mode</div><h3>Local first</h3><p>Raw personal content stays on-device by default. Cloud analysis must be explicit and purpose-bound.</p></div></div><section class='section panel'><div class='eyebrow'>Managed devices</div><h2>{len(devices)} enrolled record(s)</h2><p>Future settings will be capability-aware per operating system. Aura will never show a control that implies an unsupported iOS/macOS/Android/Windows permission can be enforced.</p></section><section class='section truth'><b>High-risk policy cannot be weakened by ordinary member settings.</b> Fixed action-risk classes are enforced by both the web control plane and the independent native protocol.</section>"""
    return _shell(title="Security Settings", active_path="/aura-sec/settings", subtitle="Privacy, automation and response controls with hard safety floors that cannot be bypassed by presentation settings.", body=body)


__all__ = ["router"]
