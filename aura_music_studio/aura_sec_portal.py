from __future__ import annotations

from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .accounts import AccountStore
from .aura_sec_health import evaluate_device_health, summarize_security_health
from .aura_sec_read_model import AuraSecReadModel
from .aura_sec_recovery import AuraSecRecoveryStore
from .aura_sec_store import AuraSecStore

router = APIRouter(tags=["Aura Sec Portal"])
accounts = AccountStore()
security = AuraSecStore(accounts)
recovery = AuraSecRecoveryStore(accounts, security)
read_model = AuraSecReadModel(accounts)
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
    ("/aura-sec/threats", "Threats & Incidents"),
    ("/aura-sec/recovery", "Recovery"),
    ("/aura-sec/optimizer", "Optimisation"),
    ("/aura-sec/downloads", "Protection Downloads"),
    ("/aura-sec/settings", "Security Settings"),
)


def _state_label(value: object) -> str:
    return str(value or "unknown").replace("_", " ").title()


def _tone(value: object) -> str:
    state = str(value or "").lower()
    if state in {"healthy", "active", "ransomware_ready", "verified", "success", "clean"}:
        return "good"
    if state in {"critical", "high", "attention_required", "degraded", "stale", "failed", "infected"}:
        return "danger"
    if state in {"proposed", "updating", "restore_test_required", "awaiting_verified_heartbeat", "suspicious"}:
        return "warn"
    return "neutral"


def _shell(*, title: str, active_path: str, body: str, subtitle: str = "") -> HTMLResponse:
    nav = "".join(
        f"<a class='navitem {'active' if path == active_path else ''}' href='{path}'>{escape(label)}</a>"
        for path, label in _NAV
    )
    page = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'><meta name='robots' content='noindex,nofollow'><title>{escape(title)} · {escape(AURA_SEC_NAME)}</title><style>
:root{{--bg:#02060a;--panel:#081119;--panel2:#0c1821;--line:#ffffff18;--text:#f7fbff;--muted:#9fb0bf;--cyan:#65ecff;--gold:#f6cf79;--violet:#ad8cff;--good:#7ae6a5;--warn:#ffd477;--danger:#ff8a9b}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 8% 0,#11374f,transparent 30%),radial-gradient(circle at 92% 2%,#34194d,transparent 28%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.layout{{display:grid;grid-template-columns:260px minmax(0,1fr);min-height:100vh}}aside{{border-right:1px solid var(--line);background:#03090ef2;padding:22px 16px;position:sticky;top:0;height:100vh}}.brand{{font-weight:950;line-height:1.12;font-size:.92rem}}.brand small{{display:block;color:var(--gold);font-size:.63rem;letter-spacing:.12em;text-transform:uppercase;margin-top:7px}}.product{{margin:28px 0 12px;font-weight:950;font-size:1.5rem;background:linear-gradient(90deg,#fff,var(--cyan),var(--violet));background-clip:text;color:transparent}}nav{{display:grid;gap:6px}}.navitem{{padding:11px 12px;border:1px solid transparent;border-radius:12px;color:var(--muted);font-size:.86rem;font-weight:800}}.navitem:hover,.navitem.active{{color:#fff;border-color:#65ecff38;background:#65ecff0c}}.back{{display:block;margin-top:24px;padding:10px 12px;border:1px solid var(--line);border-radius:12px;color:var(--muted);font-size:.8rem}}main{{padding:30px clamp(18px,4vw,58px) 64px;min-width:0}}.eyebrow{{font-size:.7rem;letter-spacing:.14em;text-transform:uppercase;font-weight:950;color:var(--cyan)}}h1{{font-size:clamp(2.4rem,5vw,4.8rem);letter-spacing:-.055em;line-height:.95;margin:.17em 0}}h2{{font-size:clamp(1.4rem,2.5vw,2rem);margin:6px 0 14px}}h3{{margin:5px 0 8px}}p{{color:var(--muted);line-height:1.58}}.sub{{max-width:850px;margin:0 0 26px}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}.grid2{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.card,.panel{{border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,#0e1a23e8,#071017eb);padding:17px}}.metric strong{{font-size:1.6rem;display:block}}.metric span{{font-size:.75rem;color:var(--muted)}}.section{{margin-top:30px}}.row{{display:flex;align-items:center;justify-content:space-between;gap:16px;border-top:1px solid var(--line);padding:13px 0}}.row:first-child{{border-top:0;padding-top:0}}.row:last-child{{padding-bottom:0}}.rowmain{{min-width:0}}.rowmain b{{display:block}}.meta{{font-size:.73rem;color:var(--muted);margin-top:4px;line-height:1.45}}.pill{{display:inline-flex;align-items:center;border-radius:999px;padding:5px 9px;font-size:.68rem;font-weight:900;text-transform:uppercase;letter-spacing:.05em;border:1px solid var(--line);white-space:nowrap}}.pill.good{{color:var(--good);border-color:#7ae6a54a;background:#7ae6a50b}}.pill.warn{{color:var(--warn);border-color:#ffd4774a;background:#ffd4770b}}.pill.danger{{color:var(--danger);border-color:#ff8a9b4a;background:#ff8a9b0b}}.pill.neutral{{color:var(--muted)}}.empty{{border:1px dashed #ffffff24;border-radius:18px;padding:26px;text-align:center;background:#ffffff04}}.empty strong{{display:block;margin-bottom:6px}}.truth{{border:1px solid #ffd4773d;border-radius:16px;padding:15px;background:#ffd47709;color:var(--muted);font-size:.84rem;line-height:1.55}}.truth b{{color:var(--warn)}}.action{{display:inline-flex;padding:9px 12px;border-radius:11px;border:1px solid var(--line);font-weight:850;font-size:.78rem;background:#ffffff07}}.action.primary{{background:linear-gradient(105deg,var(--cyan),#b7a4ff,var(--gold));color:#071016;border:0}}.bar{{height:8px;border-radius:999px;background:#ffffff0c;overflow:hidden;margin-top:9px}}.bar i{{display:block;height:100%;background:linear-gradient(90deg,var(--cyan),var(--violet),var(--gold));border-radius:inherit}}.checklist{{display:grid;gap:9px}}.check{{display:flex;gap:10px;align-items:flex-start;border:1px solid var(--line);border-radius:13px;padding:12px;background:#ffffff04}}.check span{{font-size:.82rem;color:var(--muted)}}.download{{display:grid;grid-template-columns:1fr auto;gap:16px;align-items:center;border-top:1px solid var(--line);padding:14px 0}}.download:first-child{{border-top:0}}.disabled{{opacity:.55;cursor:not-allowed}}.footer{{margin-top:38px;padding-top:20px;border-top:1px solid var(--line);color:var(--muted);font-size:.72rem}}@media(max-width:980px){{.layout{{grid-template-columns:1fr}}aside{{position:relative;height:auto;border-right:0;border-bottom:1px solid var(--line)}}nav{{grid-template-columns:repeat(3,minmax(0,1fr))}}.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}@media(max-width:620px){{main{{padding:22px 14px 50px}}nav{{grid-template-columns:1fr 1fr}}.grid,.grid2{{grid-template-columns:1fr}}.row,.download{{align-items:flex-start;flex-direction:column}}}}
</style></head><body><div class='layout'><aside><a class='brand' href='/dashboard'>{escape(MASTER_PRODUCT_NAME)}<small>{escape(MASTER_ENDORSEMENT)}</small></a><div class='product'>Aura Sec</div><nav>{nav}</nav><a class='back' href='/dashboard'>← Back to member dashboard</a></aside><main><div class='eyebrow'>Aura Sec · Member Security</div><h1>{escape(title)}</h1>{f'<p class="sub">{escape(subtitle)}</p>' if subtitle else ''}{body}<div class='footer'>Aura Sec is a separate security product inside the {escape(MASTER_PRODUCT_NAME)}. Protection claims require a signed native client and fresh verified endpoint evidence.</div></main></div></body></html>"""
    return _secure_html(page)


def _require_page_user(request: Request):
    user = _page_user(request)
    if not user:
        return None, RedirectResponse(f"/signin?next={request.url.path}", status_code=303)
    return user, None


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

    body = f"""<div class='grid'><div class='card metric'><strong>{health['managed_devices']}</strong><span>managed devices</span></div><div class='card metric'><strong>{health['healthy_devices']}</strong><span>currently verified healthy</span></div><div class='card metric'><strong>{health['attention_devices']}</strong><span>requiring attention</span></div></div><section class='section panel'><div class='eyebrow'>Device fleet</div><h2>Verified endpoint state</h2>{rows}</section><section class='section truth'><b>Licence: {escape(_state_label(licence.get('status')))}</b> · Creative membership does not activate Aura Sec. Device health is derived from fresh verified heartbeat evidence.</section>"""
    return _shell(title="My Devices", active_path="/aura-sec/devices", subtitle="Every protected device must independently prove its current state to the Command Center.", body=body)


@router.get("/aura-sec/threats", response_class=HTMLResponse, include_in_schema=False)
def aura_sec_threats_page(request: Request):
    user, redirect = _require_page_user(request)
    if redirect:
        return redirect
    incidents = read_model.incidents(user["id"], limit=100)
    actions = read_model.actions(user["id"], limit=100)
    counts = read_model.counts(user["id"])

    incident_rows = "".join(
        f"<div class='row'><div class='rowmain'><b>{escape(str(item['title']))}</b><div class='meta'>{escape(str(item['display_name']))} · {escape(str(item['platform']))} · {escape(str(item['created_at']))}</div>"
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
