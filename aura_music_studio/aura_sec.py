from __future__ import annotations

from dataclasses import asdict, dataclass
from html import escape
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from .accounts import AccountStore
from .aura_sec_health import summarize_security_health
from .aura_sec_member_api import router as aura_sec_member_router
from .aura_sec_store import AuraSecStore

router = APIRouter(tags=["Aura Sec"])
router.include_router(aura_sec_member_router)
accounts = AccountStore()
security_store = AuraSecStore(accounts)
MEMBER_COOKIE = "lss_session"

MASTER_PRODUCT_NAME = "Elevate Souls Productions Content Creation Command Center"
MASTER_ENDORSEMENT = "Powered by Aura AI"

# Working title only. Commercial naming/trademark clearance is required before launch.
AURA_SEC_NAME = "Aura Sec"
AURA_SEC_ENDORSEMENT = f"{MASTER_PRODUCT_NAME} · {MASTER_ENDORSEMENT}"
AURA_SEC_STATE = "foundation"


@dataclass(frozen=True)
class ProtectionDomain:
    id: str
    name: str
    summary: str
    phase: Literal["foundation", "native-agent", "threat-cloud", "recovery", "future"]
    local_first: bool = True
    destructive_actions_require_confirmation: bool = True


@dataclass(frozen=True)
class EndpointTarget:
    id: str
    name: str
    architecture: str
    protection_scope: str
    status: Literal["not_released", "preview", "released"] = "not_released"
    signed_manifest_url: str | None = None
    download_url: str | None = None


PROTECTION_DOMAINS: tuple[ProtectionDomain, ...] = (
    ProtectionDomain(
        "endpoint-edr",
        "Malware + Behavioural EDR",
        "Layered reputation, static analysis, YARA-compatible rules, process-tree telemetry, persistence detection, exploit signals and behavioural containment.",
        "native-agent",
    ),
    ProtectionDomain(
        "ransomware-recovery",
        "Ransomware Shield + Recovery",
        "Canary-file and write-pattern detection, rapid process containment, network isolation, versioned recovery and clean-restore verification where the operating system permits it.",
        "recovery",
    ),
    ProtectionDomain(
        "web-scam",
        "Web, Phishing + Scam Shield",
        "Malicious URL, phishing, smishing, QR-code, download and impersonation-risk analysis with privacy-preserving local checks before cloud enrichment.",
        "threat-cloud",
    ),
    ProtectionDomain(
        "network-vpn",
        "Network, DNS + Private Connection",
        "Firewall posture, malicious-domain filtering, secure DNS, unsafe-network detection and an optional modern encrypted tunnel implemented with audited protocols rather than custom cryptography.",
        "native-agent",
    ),
    ProtectionDomain(
        "identity-auth",
        "Identity + Account Defence",
        "Passkey-first account protection, breach monitoring adapters, compromised-credential checks, high-risk action verification and device-bound session controls.",
        "foundation",
    ),
    ProtectionDomain(
        "vulnerability-patching",
        "Vulnerability + Patch Intelligence",
        "Local software inventory correlated with vulnerability intelligence, known-exploited status, exploitation probability and device-specific impact before recommended patching.",
        "threat-cloud",
    ),
    ProtectionDomain(
        "privacy-permissions",
        "Privacy + Permission Guard",
        "Camera, microphone, screen, clipboard, app-permission and sensitive-data posture checks constrained to the APIs each operating system actually exposes.",
        "native-agent",
    ),
    ProtectionDomain(
        "vault-backup",
        "Encrypted Vault + Resilient Backup",
        "Hardware-backed keys where available, encrypted versioned storage, immutable/offline-capable recovery targets and explicit restore testing.",
        "recovery",
    ),
    ProtectionDomain(
        "storage-optimizer",
        "Aura Storage + Performance Assistant",
        "Duplicate, temporary, large, stale and startup-impact analysis with preview, consent and undo. User documents are never silently deleted and registry-cleaner gimmicks are excluded.",
        "native-agent",
    ),
    ProtectionDomain(
        "home-network",
        "Home Network + IoT Shield",
        "Device discovery, router hygiene, exposed-service warnings, suspicious-network-change detection and least-privilege recommendations without pretending unsupported devices can be remotely controlled.",
        "native-agent",
    ),
    ProtectionDomain(
        "incident-response",
        "Containment + Incident Response",
        "Quarantine, network isolation, evidence-preserving event timelines, guided remediation and recovery actions with auditable user approval for disruptive changes.",
        "foundation",
    ),
    ProtectionDomain(
        "product-integrity",
        "Aura Sec Self-Protection",
        "Signed updates, anti-tamper controls, software bills of materials, build provenance, dependency scanning and rollback protection while preserving an authorised uninstall path.",
        "foundation",
    ),
)


ENDPOINT_TARGETS: tuple[EndpointTarget, ...] = (
    EndpointTarget(
        "windows-x64-arm64",
        "Windows 11",
        "x64 / ARM64",
        "Native endpoint, behavioural telemetry, network protection, recovery, posture and optimisation using supported Windows security interfaces.",
    ),
    EndpointTarget(
        "macos-universal",
        "macOS",
        "Apple silicon / Intel universal",
        "Endpoint Security + System Extension + Network Extension architecture, Keychain/Secure Enclave integration and FileVault posture.",
    ),
    EndpointTarget(
        "linux-x64-arm64",
        "Linux",
        "x86_64 / ARM64",
        "User-space agent with eBPF/LSM/fanotify integrations where supported, package inventory, nftables/network posture and secure storage controls.",
    ),
    EndpointTarget(
        "android",
        "Android",
        "Supported Android devices",
        "Local web/scam protection, VPN/DNS layer, device and permission posture, secure vault and hardware-backed Keystore integration within Android platform policy.",
    ),
    EndpointTarget(
        "ios-ipados",
        "iPhone + iPad",
        "iOS / iPadOS",
        "Network/privacy/identity/vault protection within Apple sandbox and entitlement boundaries; no false claim to scan arbitrary third-party apps.",
    ),
    EndpointTarget(
        "browser-guard",
        "Aura Sec Browser Guard",
        "Chrome / Edge / Firefox-family extension targets",
        "Phishing, malicious-link, download, tracker and scam-risk warnings with least-privilege extension permissions.",
    ),
    EndpointTarget(
        "chromeos",
        "ChromeOS",
        "Supported Chromebooks",
        "Browser, secure DNS/VPN, identity and web protection with Android-backed capabilities only where the device supports them.",
    ),
)


SECURITY_PRINCIPLES = (
    "Zero trust: device or network location never creates implicit trust.",
    "Local-first privacy: raw personal content stays on-device unless the member explicitly chooses a cloud analysis path.",
    "Recovery-first ransomware defence: prevent, detect, contain and prove restore capability.",
    "No custom cryptography: use reviewed platform primitives and established protocols.",
    "Least privilege: every client component receives only the operating-system permissions required for its documented function.",
    "Explainable automation: Aura shows the evidence, proposed change and rollback path before disruptive remediation.",
    "Truthful capability reporting: unavailable native agents, feeds or services are shown as unavailable rather than simulated.",
    "Secure supply chain: every release must be signed, traceable to build provenance and accompanied by an SBOM.",
)


def _session_user(request: Request) -> dict:
    token = request.cookies.get(MEMBER_COOKIE)
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    user = accounts.resolve_session(token)
    if not user:
        raise HTTPException(401, "Sign in required")
    return user


def _page_user(request: Request) -> dict | None:
    return accounts.resolve_session(request.cookies.get(MEMBER_COOKIE))


def _product_status(user_id: str | None = None) -> dict:
    released = [target for target in ENDPOINT_TARGETS if target.status == "released"]
    result = {
        "master_product": MASTER_PRODUCT_NAME,
        "master_endorsement": MASTER_ENDORSEMENT,
        "working_title": AURA_SEC_NAME,
        "commercial_name_clearance": "required_before_sale",
        "state": AURA_SEC_STATE,
        "separate_purchase": True,
        "included_in_creative_membership": False,
        "price_configured": False,
        # A web request cannot prove that the requesting physical device has the native agent.
        "protection_active_on_this_device": False,
        "native_agents_released": len(released),
        "native_agents_total": len(ENDPOINT_TARGETS),
        "truthful_status": (
            "Security architecture and member control-plane foundation are present. "
            "No device should be described as protected until a signed native agent is installed, enrolled and reporting fresh verified telemetry."
        ),
        "absolute_security_guarantee": False,
    }
    if user_id:
        licence = security_store.licence(user_id)
        health = summarize_security_health(security_store.list_devices(user_id))
        result["licence"] = licence
        result["device_health"] = health
        result["at_least_one_device_currently_verified_healthy"] = (
            licence.get("status") == "active" and health.get("healthy_devices", 0) > 0
        )
    return result


@router.get("/api/aura-sec/status")
def aura_sec_status(request: Request):
    user = _session_user(request)
    return {"user_id": user["id"], **_product_status(user["id"])}


@router.get("/api/aura-sec/capabilities")
def aura_sec_capabilities(request: Request):
    user = _session_user(request)
    return {
        "status": _product_status(user["id"]),
        "principles": list(SECURITY_PRINCIPLES),
        "domains": [asdict(item) for item in PROTECTION_DOMAINS],
    }


@router.get("/api/aura-sec/downloads")
def aura_sec_downloads(request: Request):
    _session_user(request)
    return {
        "release_policy": (
            "A download becomes available only after a platform-specific native package has a signed release manifest, "
            "cryptographic hash, version, supported-OS declaration and verified distribution URL."
        ),
        "targets": [asdict(item) for item in ENDPOINT_TARGETS],
    }


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


@router.get("/aura-sec", response_class=HTMLResponse, include_in_schema=False)
def aura_sec_portal(request: Request):
    user = _page_user(request)
    if not user:
        return RedirectResponse("/signin?next=/aura-sec", status_code=303)

    domains = "".join(
        "<article class='card'>"
        f"<div class='phase'>{escape(item.phase.replace('-', ' ').title())}</div>"
        f"<h3>{escape(item.name)}</h3><p>{escape(item.summary)}</p>"
        f"<small>{'Local-first' if item.local_first else 'Cloud-assisted'} · "
        f"{'Confirmation required for disruptive actions' if item.destructive_actions_require_confirmation else 'Automatic action permitted'}</small>"
        "</article>"
        for item in PROTECTION_DOMAINS
    )
    targets = "".join(
        "<div class='target'>"
        f"<div><b>{escape(item.name)}</b><span>{escape(item.architecture)}</span>"
        f"<small>{escape(item.protection_scope)}</small></div>"
        "<button disabled title='A signed native release has not been published'>Not released</button>"
        "</div>"
        for item in ENDPOINT_TARGETS
    )
    principles = "".join(f"<li>{escape(item)}</li>" for item in SECURITY_PRINCIPLES)
    status = _product_status(user["id"])
    licence = status.get("licence") or {}
    health = status.get("device_health") or {}
    licence_label = str(licence.get("status") or "not_purchased").replace("_", " ").title()
    health_label = str(health.get("overall") or "not_enrolled").replace("_", " ").title()

    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'><meta name='robots' content='noindex,nofollow'><title>{escape(AURA_SEC_NAME)} — Security Center</title><style>
:root{{--bg:#03070b;--panel:#0b141b;--line:#ffffff1b;--cyan:#68efff;--gold:#f4cc78;--violet:#b188ff;--good:#78e6a6;--warn:#ffd479;--text:#f8fbff;--muted:#aebbc7}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 15% 0,#123957,transparent 32%),radial-gradient(circle at 95% 10%,#39175c,transparent 30%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.wrap{{width:min(1280px,calc(100% - 28px));margin:auto;padding:24px 0 60px}}.top{{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}}.brand{{font-weight:950;max-width:820px}}.brand small{{display:block;color:var(--gold);font-size:.66rem;letter-spacing:.08em;text-transform:uppercase;margin-top:3px}}.btn,button{{border:1px solid var(--line);border-radius:12px;padding:10px 14px;background:#ffffff09;color:#fff;font-weight:850}}.primary{{background:linear-gradient(110deg,var(--cyan),#b2a4ff,var(--gold));color:#071016;border:0}}.hero{{padding:58px 0 30px;display:grid;grid-template-columns:1.45fr .75fr;gap:26px;align-items:end}}.eyebrow,.phase{{font-size:.7rem;letter-spacing:.14em;text-transform:uppercase;font-weight:900;color:var(--cyan)}}h1{{font-size:clamp(3rem,8vw,7rem);line-height:.88;letter-spacing:-.065em;margin:.13em 0 .2em}}h1 span{{background:linear-gradient(90deg,#fff,var(--cyan),var(--violet),var(--gold));background-clip:text;color:transparent}}p{{color:var(--muted);line-height:1.6}}.truth{{border:1px solid #ffd47955;background:#ffd4790d;border-radius:18px;padding:18px}}.truth b{{color:var(--warn)}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:10px 0 28px}}.stat{{border:1px solid var(--line);border-radius:16px;padding:16px;background:#ffffff06}}.stat strong{{display:block;font-size:1.15rem}}.stat span{{color:var(--muted);font-size:.78rem}}h2{{font-size:clamp(1.6rem,3vw,2.5rem);margin:7px 0 16px}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.card{{border:1px solid var(--line);border-radius:19px;padding:18px;background:linear-gradient(145deg,#101b24db,#080d13e8)}}.card h3{{margin:9px 0 6px}}.card p{{font-size:.9rem}}.card small,.target small,.target span{{color:var(--muted);display:block;line-height:1.45}}.card small{{font-size:.68rem}}.section{{margin-top:34px}}.principles{{border:1px solid #68efff2f;border-radius:22px;padding:20px 24px;background:#68efff09}}.principles li{{margin:9px 0;color:var(--muted);line-height:1.5}}.targets{{display:grid;gap:8px}}.target{{border:1px solid var(--line);border-radius:15px;padding:14px;display:flex;justify-content:space-between;align-items:center;gap:18px;background:#ffffff05}}.target div{{max-width:900px}}.target span{{font-size:.74rem;margin:2px 0 5px}}.target small{{font-size:.75rem}}button:disabled{{opacity:.55;cursor:not-allowed}}.purchase{{margin-top:28px;border:1px solid #b188ff48;border-radius:22px;padding:22px;background:linear-gradient(120deg,#b188ff12,#68efff09,#0b1118)}}.purchase strong{{color:var(--gold)}}footer{{border-top:1px solid var(--line);margin-top:38px;padding-top:22px;color:var(--muted);font-size:.78rem}}@media(max-width:900px){{.hero{{grid-template-columns:1fr}}.grid{{grid-template-columns:repeat(2,1fr)}}.stats{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:600px){{.stats,.grid{{grid-template-columns:1fr}}.target{{align-items:flex-start;flex-direction:column}}}}
</style></head><body><main class='wrap'><header class='top'><a class='brand' href='/dashboard'>{escape(MASTER_PRODUCT_NAME)}<small>{escape(MASTER_ENDORSEMENT)}</small></a><a class='btn' href='/dashboard'>← Member dashboard</a></header><section class='hero'><div><div class='eyebrow'>Security platform · working title</div><h1><span>{escape(AURA_SEC_NAME)}</span></h1><p>The security command layer inside the Elevate Souls Productions Content Creation Command Center: endpoint defence, web and identity protection, resilient recovery, private storage and safe device optimisation — orchestrated by Aura and designed around least privilege, explainability and verifiable protection state.</p></div><aside class='truth'><b>Foundation status — native clients not released.</b><p>{escape(status['truthful_status'])}</p></aside></section><div class='stats'><div class='stat'><strong>{len(PROTECTION_DOMAINS)}</strong><span>protection domains designed</span></div><div class='stat'><strong>{len([target for target in ENDPOINT_TARGETS if target.status == 'released'])} / {len(ENDPOINT_TARGETS)}</strong><span>signed endpoint targets released</span></div><div class='stat'><strong>{escape(licence_label)}</strong><span>separate Aura Sec licence</span></div><div class='stat'><strong>{escape(health_label)}</strong><span>verified enrolled-device health</span></div></div><section><div class='eyebrow'>Defence in depth</div><h2>Prevent. Detect. Contain. Recover.</h2><div class='grid'>{domains}</div></section><section class='section principles'><div class='eyebrow'>Non-negotiable engineering rules</div><h2>Security without theatre.</h2><ul>{principles}</ul></section><section class='section'><div class='eyebrow'>Native protection targets</div><h2>Downloads unlock only after signed builds exist.</h2><div class='targets'>{targets}</div></section><section class='purchase'><div class='eyebrow'>Commercial model</div><h2>Standalone Aura Sec purchase</h2><p><strong>No price has been invented or activated.</strong> Aura Sec is deliberately separate from the Content Creation Command Center's creative membership entitlements. Checkout will be enabled only after the security SKU, licence period, device allowance, payment verification and signed-client release channel are configured.</p><p>The name “Aura Sec” remains a working title and requires commercial name/trademark clearance before sale.</p></section><footer>{escape(AURA_SEC_ENDORSEMENT)} · No security product can truthfully promise absolute or “impenetrable” protection. Aura Sec is engineered as layered risk reduction with containment and recovery.</footer></main></body></html>"""
    return _secure_html(html)


_DASHBOARD_CARD = """<article class='tool' data-aura-sec-card='1'><div class='icon'>🛡️</div><h3>Aura Sec</h3><p>Security Center for device, web, identity, recovery, private storage and safe optimisation inside the ESP Content Creation Command Center. Separate security purchase; native protection activates only after a signed client is installed and verified.</p><div class='toolactions'><a class='btn primary' href='/aura-sec'>Open Security Center</a></div></article>"""


class AuraSecDashboardMiddleware(BaseHTTPMiddleware):
    """Add the isolated Aura Sec entry without coupling security billing to creative plans."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/dashboard" or response.status_code != 200:
            return response
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type.lower():
            return response
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        text = body.decode("utf-8", errors="replace")
        if "data-aura-sec-card='1'" not in text and "<div class='tools'>" in text:
            text = text.replace("<div class='tools'>", "<div class='tools'>" + _DASHBOARD_CARD, 1)
        headers = dict(response.headers)
        headers.pop("content-length", None)
        return Response(
            content=text,
            status_code=response.status_code,
            headers=headers,
            media_type="text/html",
        )


__all__ = [
    "AURA_SEC_NAME",
    "AURA_SEC_STATE",
    "AuraSecDashboardMiddleware",
    "ENDPOINT_TARGETS",
    "MASTER_ENDORSEMENT",
    "MASTER_PRODUCT_NAME",
    "PROTECTION_DOMAINS",
    "SECURITY_PRINCIPLES",
    "router",
]
