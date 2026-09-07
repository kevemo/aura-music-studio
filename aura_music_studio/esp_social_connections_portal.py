from __future__ import annotations

import os
import re
from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_niche import require_esp_social_member
from .esp_social_facebook_adapter import FacebookPagesAdapter
from .esp_social_provider_analytics_portal import router as provider_analytics_router
from .esp_social_publish_capabilities import implemented_content_types, resolve_publish_capability
from .esp_social_secret_refs import social_token_env_name
from .social_management import SocialConnection, SocialHouseStore, platform_capabilities

router = APIRouter()
router.include_router(provider_analytics_router)

_FACEBOOK_PAGE_ID_RE = re.compile(r"^[0-9]{2,40}$")
_TOKEN_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class FacebookPagesConnectionRequest(BaseModel):
    account_label: str = Field(default="Facebook Page", max_length=160)
    page_id: str = Field(min_length=2, max_length=40)
    token_alias: str = Field(min_length=1, max_length=64)


def _is_owner(membership: dict) -> bool:
    return str(membership.get("status") or "").strip().lower() == "owner"


def _require_owner(membership: dict) -> None:
    if not _is_owner(membership):
        raise HTTPException(403, "Only an ESP owner can configure deployment-managed Facebook Pages publishing")


def _facebook_deployment_state(connection: SocialConnection | None) -> dict:
    reasons: list[str] = []
    credential_configured = False
    graph_api_configured = False
    page_configured = False
    capability = resolve_publish_capability(connection, platform="facebook", content_type="post")

    if connection is not None:
        page_id = (connection.account_external_id or str(connection.metadata.get("facebook_page_id") or "")).strip()
        page_configured = bool(_FACEBOOK_PAGE_ID_RE.fullmatch(page_id))
        if not page_configured:
            reasons.append("A verified numeric Facebook Page ID is required.")
        env_name = social_token_env_name(connection.token_secret_ref)
        credential_configured = bool(env_name and (os.getenv(env_name) or "").strip())
        if not credential_configured:
            reasons.append("The deployment-managed Facebook Page access-token alias is not configured on this server.")
    else:
        reasons.append("No Facebook Page connection is registered for this Social House.")

    try:
        FacebookPagesAdapter._base()
        graph_api_configured = True
    except Exception as exc:
        reasons.append(str(exc))

    reasons.extend(reason for reason in capability.reasons if reason not in reasons)
    deployment_ready = bool(
        connection is not None
        and page_configured
        and credential_configured
        and graph_api_configured
        and capability.publishable
    )
    return {
        "platform": "facebook",
        "connection_id": connection.id if connection else None,
        "account_label": connection.account_label if connection else "",
        "account_external_id": connection.account_external_id if connection else None,
        "state": connection.state if connection else "not_connected",
        "deployment_managed": True,
        "credential_configured": credential_configured,
        "graph_api_configured": graph_api_configured,
        "page_configured": page_configured,
        "deployment_ready": deployment_ready,
        "auto_publish_content_types": implemented_content_types("facebook"),
        "capability": capability.model_dump(mode="json"),
        "reasons": reasons,
        "remote_provider_verification": "Provider identity, permissions and publication are confirmed by Meta responses when a publish is executed.",
    }


def _runtime_connection_row(connection: SocialConnection, content_types: list[str]) -> dict:
    capabilities = [
        resolve_publish_capability(connection, platform=connection.platform, content_type=content_type).model_dump(mode="json")
        for content_type in content_types
    ]
    return {
        "connection_id": connection.id,
        "platform": connection.platform,
        "account_label": connection.account_label,
        "account_external_id": connection.account_external_id,
        "state": connection.state,
        "supports_auto_publish": connection.supports_auto_publish,
        "member_oauth": connection.metadata.get("oauth_verified") is True,
        "publishing_adapter": str(connection.metadata.get("publishing_adapter") or ""),
        "publishing_adapter_active": connection.metadata.get("publishing_adapter_active") is True,
        "capabilities": capabilities,
    }


@router.get("/command-center/api/social/spaces/{space_id}/connections/runtime")
def social_connection_runtime(space_id: str, request: Request):
    _member, membership, _profile = require_esp_social_member(request)
    try:
        house = SocialHouseStore().load(space_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc

    platform_rows = platform_capabilities()
    connections: dict[str, list[dict]] = {}
    for connection in house.connections:
        content_types = list(platform_rows.get(connection.platform, {}).get("auto_publish_content_types", []))
        connections.setdefault(connection.platform, []).append(_runtime_connection_row(connection, content_types))

    facebook_connection = next(
        (item for item in house.connections if item.platform == "facebook" and item.state == "connected"),
        None,
    )
    return {
        "space_id": space_id,
        "owner_can_configure_facebook": _is_owner(membership),
        "connections": connections,
        "facebook": _facebook_deployment_state(facebook_connection),
        "credential_values_exposed": False,
    }


@router.put("/command-center/api/social/spaces/{space_id}/connections/facebook-pages")
def configure_facebook_pages(space_id: str, body: FacebookPagesConnectionRequest, request: Request):
    member, membership, _profile = require_esp_social_member(request)
    _require_owner(membership)
    page_id = body.page_id.strip()
    alias = body.token_alias.strip()
    if not _FACEBOOK_PAGE_ID_RE.fullmatch(page_id):
        raise HTTPException(400, "Facebook Page ID must contain 2 to 40 digits")
    if not _TOKEN_ALIAS_RE.fullmatch(alias):
        raise HTTPException(400, "Facebook token alias may contain only letters, numbers, underscores and hyphens")

    store = SocialHouseStore()
    try:
        house = store.load(space_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    existing = next((item for item in house.connections if item.platform == "facebook"), None)
    connection = SocialConnection(
        id=existing.id if existing else None,
        platform="facebook",
        account_label=(body.account_label or "Facebook Page").strip()[:160],
        account_external_id=page_id,
        state="connected",
        supports_auto_publish=True,
        supports_analytics=False,
        supports_inbox=False,
        token_secret_ref=f"social-token://{alias}",
        metadata={
            "facebook_page_id": page_id,
            "publishing_adapter": "facebook_pages_graph",
            "publishing_adapter_active": True,
            "provisioning_mode": "owner_deployment_managed",
            "configured_by": str(getattr(member, "user_id", "owner")),
        },
    ) if existing else SocialConnection(
        platform="facebook",
        account_label=(body.account_label or "Facebook Page").strip()[:160],
        account_external_id=page_id,
        state="connected",
        supports_auto_publish=True,
        supports_analytics=False,
        supports_inbox=False,
        token_secret_ref=f"social-token://{alias}",
        metadata={
            "facebook_page_id": page_id,
            "publishing_adapter": "facebook_pages_graph",
            "publishing_adapter_active": True,
            "provisioning_mode": "owner_deployment_managed",
            "configured_by": str(getattr(member, "user_id", "owner")),
        },
    )
    house = store.connect_placeholder(space_id, connection)
    saved = next(item for item in house.connections if item.id == connection.id)
    return {
        "connection": _runtime_connection_row(saved, implemented_content_types("facebook")),
        "facebook": _facebook_deployment_state(saved),
        "credential_value_stored_in_social_house": False,
    }


@router.delete("/command-center/api/social/spaces/{space_id}/connections/facebook-pages")
def disconnect_facebook_pages(space_id: str, request: Request):
    member, membership, _profile = require_esp_social_member(request)
    _require_owner(membership)
    store = SocialHouseStore()
    try:
        house = store.load(space_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    existing = next((item for item in house.connections if item.platform == "facebook"), None)
    if existing is None:
        return {"disconnected": False, "reason": "No Facebook Page connection is registered"}
    disconnected = existing.model_copy(
        update={
            "state": "not_connected",
            "supports_auto_publish": False,
            "token_secret_ref": None,
            "metadata": {
                **existing.metadata,
                "publishing_adapter_active": False,
                "provisioning_mode": "owner_deployment_managed",
                "disconnected_by": str(getattr(member, "user_id", "owner")),
            },
        }
    )
    store.connect_placeholder(space_id, disconnected)
    return {"disconnected": True, "facebook": _facebook_deployment_state(None)}


@router.get(
    "/command-center/social/connections",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def social_connections_portal(request: Request):
    _member, esp_membership, profile = require_esp_social_member(request)
    definition = profile["catalog"]
    accent = escape(definition["theme"]["accent"])
    secondary = escape(definition["theme"]["secondary"])
    niche = escape(definition["title"])
    role = esp_membership.get("roles") or (
        "owner" if esp_membership.get("status") == "owner" else "member"
    )

    html = r"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'><meta name='robots' content='noindex,nofollow'><meta name='theme-color' content='#05040b'><title>ESP Social Account Connections</title><style>
:root{--accent:__ACCENT__;--secondary:__SECONDARY__;--bg:#03040a;--panel:#0d0f1b;--line:#ffffff1c;--text:#fff;--muted:#b7bbce;--green:#79e3a8;--amber:#ffd36b;--red:#ff8f9d;--cyan:#5ce8ff}*{box-sizing:border-box}body{margin:0;min-height:100vh;color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:radial-gradient(circle at 8% 0,var(--secondary),transparent 28%),radial-gradient(circle at 92% 4%,var(--accent),transparent 23%),linear-gradient(#03040a,#070811 65%,#020309)}a{color:inherit;text-decoration:none}button,input,select{font:inherit}.wrap{width:min(1240px,calc(100% - 28px));margin:auto}.nav{position:sticky;top:0;z-index:10;background:#05060bee;backdrop-filter:blur(18px);border-bottom:1px solid var(--line)}.navin{min-height:72px;display:flex;align-items:center;justify-content:space-between;gap:14px}.brand{font-weight:950}.brand small{display:block;color:var(--accent);font-size:.64rem;text-transform:uppercase;letter-spacing:.08em}.actions{display:flex;gap:7px;flex-wrap:wrap}.btn{border:1px solid var(--line);background:#ffffff08;color:#fff;border-radius:12px;padding:9px 12px;font-weight:850;cursor:pointer}.btn.primary{border:0;background:linear-gradient(115deg,var(--accent),var(--secondary));color:#130b18}.btn.danger{border-color:#ff8f9d55;color:#ffd8de}.btn:disabled{opacity:.45;cursor:not-allowed}.hero{padding:42px 0 20px}.eyebrow{color:var(--accent);font-size:.72rem;font-weight:950;letter-spacing:.17em;text-transform:uppercase}.hero h1{font-size:clamp(2.5rem,6vw,4.7rem);letter-spacing:-.05em;line-height:.95;margin:.14em 0 .2em}.hero h1 span{background:linear-gradient(95deg,#fff,var(--accent),var(--secondary));background-clip:text;color:transparent}.lead{color:var(--muted);max-width:980px;line-height:1.62}.toolbar,.truth,.provider,.summary{border:1px solid var(--line);background:linear-gradient(145deg,#101323e8,#080a14f0);border-radius:20px;padding:14px}.toolbar{display:grid;grid-template-columns:1fr auto;gap:9px;align-items:center;margin-bottom:12px}.field{width:100%;border:1px solid var(--line);background:#070913;color:#fff;border-radius:11px;padding:10px}.truth{margin-bottom:12px;color:#dffaff;border-color:#5ce8ff38}.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin-bottom:12px}.metric{border:1px solid var(--line);background:#ffffff05;border-radius:15px;padding:13px}.metric b{display:block;font-size:1.45rem}.metric small,.muted{color:var(--muted)}.providers{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.provider{min-height:320px;display:flex;flex-direction:column;gap:12px}.providerhead{display:flex;gap:11px;align-items:flex-start}.icon{width:46px;height:46px;border-radius:14px;border:1px solid var(--line);display:grid;place-items:center;background:#ffffff09;font-size:1.45rem}.provider h2{margin:0;font-size:1.3rem}.status{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:999px;padding:5px 8px;font-size:.7rem;font-weight:900;text-transform:uppercase;letter-spacing:.04em}.status.good{color:var(--green);border-color:#79e3a850}.status.warn{color:var(--amber);border-color:#ffd36b50}.status.bad{color:var(--red);border-color:#ff8f9d55}.account,.setup{border:1px solid var(--line);background:#ffffff05;border-radius:14px;padding:11px}.account strong{display:block}.small{font-size:.72rem;line-height:1.45}.provideractions{margin-top:auto;display:flex;gap:7px;flex-wrap:wrap}.notice{display:none;border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin-bottom:12px}.notice.show{display:block}.notice.error{color:#ffd6dc;border-color:#ff8f9d50}.empty{border:1px dashed #ffffff2a;border-radius:16px;padding:24px;text-align:center;color:var(--muted)}.caps{display:flex;gap:6px;flex-wrap:wrap}.chip{border:1px solid #5ce8ff38;border-radius:999px;padding:4px 7px;color:#c9f8ff;font-size:.68rem}.setupgrid{display:grid;grid-template-columns:1fr 1fr;gap:7px}.setupgrid .wide{grid-column:1/-1}.footer{border-top:1px solid var(--line);padding:28px 0 40px;margin-top:32px;color:var(--muted);font-size:.84rem}@media(max-width:950px){.providers{grid-template-columns:1fr}.summary{grid-template-columns:1fr 1fr}}@media(max-width:620px){.toolbar,.summary,.setupgrid{grid-template-columns:1fr}.setupgrid .wide{grid-column:auto}.actions .optional{display:none}}
</style></head><body><nav class='nav'><div class='wrap navin'><a class='brand' href='/command-center'>Elevate Souls Productions<small>Private Creator & Agent Hub · Social Connections</small></a><div class='actions'><a class='btn primary' href='/command-center/social'>Social Management</a><a class='btn optional' href='/command-center/social/provider-analytics'>Provider Analytics</a><a class='btn optional' href='/command-center/social/publish-queue'>Publish Queue</a><a class='btn optional' href='/command-center/social/approvals'>Approvals</a></div></div></nav><main class='wrap'><section class='hero'><div class='eyebrow'>__NICHE__ · __ROLE__ · Secure provider authorisation</div><h1>Connect your <span>social accounts.</span></h1><p class='lead'>TikTok, Instagram and YouTube use official member OAuth. Facebook Pages uses an ESP owner-managed Page ID plus a deployment-held token alias. Access tokens stay encrypted or deployment-held server-side and are never requested or rendered by this browser page. Runtime publishing status is derived from the adapters actually implemented by this deployment.</p></section><div id='notice' class='notice'></div><section class='toolbar'><select id='space' class='field' aria-label='Social House'></select><button class='btn' onclick='reloadAll()'>Reload Status</button></section><section class='summary' id='summary'></section><section class='truth' id='truth'>Loading secure connection and runtime capability state…</section><section class='providers' id='providers'><div class='empty'>Loading provider readiness…</div></section></main><footer class='footer'><div class='wrap'><strong>Elevate Souls Productions</strong> · Content Creation Command Center · Powered by Aura AI</div></footer><script>
const BASE='/command-center/api/social';const q=id=>document.getElementById(id);const PROVIDERS={tiktok:{label:'TikTok',icon:'🎵',mode:'oauth',detail:'Official TikTok Login Kit + Content Posting authorisation.'},instagram:{label:'Instagram',icon:'📸',mode:'oauth',detail:'Instagram Login for Professional Business or Creator accounts.'},youtube:{label:'YouTube',icon:'▶️',mode:'oauth',detail:'Google OAuth for the authorised YouTube channel and upload scope.'},facebook:{label:'Facebook Pages',icon:'📘',mode:'managed',detail:'Bounded Page feed and single-image publishing using an ESP owner-managed deployment credential.'}};let readiness=null,house=null,platforms={},runtime=null;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function notice(m,bad=false){const n=q('notice');n.textContent=m;n.className='notice show'+(bad?' error':'');clearTimeout(window._cn);window._cn=setTimeout(()=>n.className='notice',6500)}
async function api(path,opt={}){const r=await fetch(BASE+path,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b}
function providerConnections(name){return (runtime?.connections?.[name]||[]).filter(c=>c.state==='connected')}
function memberOauth(c){return c?.member_oauth===true}
function statusBadge(cls,text){return `<span class="status ${cls}">${esc(text)}</span>`}
function metric(label,value){return `<div class="metric"><b>${esc(value)}</b><small>${esc(label)}</small></div>`}
function capChips(name){const types=platforms?.[name]?.auto_publish_content_types||[];return types.length?`<div class="caps">${types.map(x=>`<span class="chip">Auto-publish: ${esc(x)}</span>`).join('')}</div>`:'<div class="muted small">Planning is available, but no automatic publishing surface is implemented.</div>'}
function renderSummary(){const all=Object.values(runtime?.connections||{}).flat(),connected=all.filter(c=>c.state==='connected').length,oauth=all.filter(c=>c.state==='connected'&&memberOauth(c)).length,implemented=Object.keys(PROVIDERS).filter(name=>platforms?.[name]?.auto_publish_implemented===true).length,fb=runtime?.facebook?.deployment_ready===true?'Ready':'Setup';q('summary').innerHTML=metric('Connected accounts',connected)+metric('Member OAuth connections',oauth)+metric('Runtime publishing providers',`${implemented}/4`)+metric('Facebook Pages',fb)}
function render(){renderSummary();const vault=readiness?.encrypted_vault_ready===true,fbReady=runtime?.facebook?.deployment_ready===true;q('truth').textContent=`Member OAuth vault: ${vault?'ready':'not configured'}. Facebook Pages deployment publishing: ${fbReady?'ready':'not ready'}. Publishing is enabled only when the selected connection, credential path, adapter and content surface pass server-side runtime checks.`;q('providers').innerHTML=Object.entries(PROVIDERS).map(([name,spec])=>providerCard(name,spec,vault)).join('')}
function providerCard(name,spec,vault){if(spec.mode==='managed')return facebookCard(spec);const cfg=readiness?.providers?.[name]||{},connections=providerConnections(name),oauthConnections=connections.filter(memberOauth);let badge,reason;if(!vault){badge=statusBadge('bad','Vault not ready');reason='Secure encrypted credential storage must be configured before connecting an account.'}else if(!cfg.configured){badge=statusBadge('warn','Provider app not configured');reason='ESP must configure this provider application before members can authorise accounts.'}else if(connections.length){badge=statusBadge('good','Connected');reason='An authorised connection is available for this Social House and will still be revalidated before every provider call.'}else{badge=statusBadge('warn','Authorisation required');reason='Provider application is ready; this Social House has not authorised an account yet.'}const accounts=connections.length?connections.map(c=>`<div class="account"><strong>${esc(c.account_label||spec.label)}</strong><div class="muted small">${c.supports_auto_publish?'Automatic publishing enabled for supported runtime surfaces':'Planning / capability state only'}</div><div class="muted small">Member-authorised OAuth credential</div><div style="margin-top:8px"><button class="btn danger" onclick="disconnect('${esc(name)}','${esc(c.connection_id)}')">Disconnect</button></div></div>`).join(''):'<div class="empty small">No account connected to this Social House.</div>';const canConnect=vault&&cfg.configured&&oauthConnections.length===0;return `<article class="provider"><div class="providerhead"><div class="icon">${spec.icon}</div><div><h2>${esc(spec.label)}</h2><div class="muted small">${esc(spec.detail)}</div></div></div><div>${badge}</div><div class="muted small">${esc(reason)}</div>${capChips(name)}${accounts}<div class="provideractions"><button class="btn primary" ${canConnect?'':'disabled'} onclick="connectProvider('${esc(name)}')">Connect ${esc(spec.label)}</button></div></article>`}
function facebookCard(spec){const fb=runtime?.facebook||{},connections=providerConnections('facebook'),owner=runtime?.owner_can_configure_facebook===true;let badge=fb.deployment_ready?statusBadge('good','Deployment ready'):connections.length?statusBadge('bad','Configuration incomplete'):statusBadge('warn','Owner setup required');const reasons=(fb.reasons||[]).length?`<div class="muted small">${(fb.reasons||[]).map(esc).join('<br>')}</div>`:'<div class="muted small">Page ID, token alias and Graph API version are configured. Meta still confirms identity/permissions from provider responses during publication.</div>';const account=connections.length?connections.map(c=>`<div class="account"><strong>${esc(c.account_label||'Facebook Page')}</strong><div class="muted small">Page ID: ${esc(c.account_external_id||'Not set')}</div><div class="muted small">Adapter: ${esc(c.publishing_adapter||'Not configured')}</div></div>`).join(''):'<div class="empty small">No Facebook Page registered for this Social House.</div>';const setup=owner?`<div class="setup"><strong>Owner-managed Facebook Pages setup</strong><div class="muted small" style="margin:5px 0 9px">Enter the Page identity and the alias of a token already provisioned in the deployment secret store. Never paste a Facebook access token here.</div><div class="setupgrid"><input id="fbLabel" class="field" maxlength="160" value="${esc(connections[0]?.account_label||'Facebook Page')}" aria-label="Facebook Page label"><input id="fbPage" class="field" inputmode="numeric" maxlength="40" value="${esc(connections[0]?.account_external_id||'')}" placeholder="Numeric Page ID" aria-label="Facebook Page ID"><input id="fbAlias" class="field wide" maxlength="64" placeholder="Deployment token alias, e.g. facebook_pages" aria-label="Deployment token alias"></div><div class="provideractions" style="margin-top:9px"><button class="btn primary" onclick="saveFacebook()">Save & Check Runtime</button>${connections.length?'<button class="btn danger" onclick="disconnectFacebook()">Disconnect Page</button>':''}</div></div>`:'<div class="account small"><strong>Owner-managed integration</strong><span class="muted">Only an ESP owner can register or change the deployment-managed Facebook Page. Creators and agents can see truthful readiness but cannot alter credentials or Page identity.</span></div>';return `<article class="provider"><div class="providerhead"><div class="icon">${spec.icon}</div><div><h2>${esc(spec.label)}</h2><div class="muted small">${esc(spec.detail)}</div></div></div><div>${badge}</div>${capChips('facebook')}${account}${reasons}${setup}</article>`}
async function loadHouse(){const id=q('space').value;if(!id){house=null;runtime=null;render();return}try{const [h,r]=await Promise.all([api(`/spaces/${encodeURIComponent(id)}`),api(`/spaces/${encodeURIComponent(id)}/connections/runtime`)]);house=h;runtime=r;render()}catch(e){notice(e.message,true)}}
async function reloadAll(){try{const [spacesData,providerData,platformData]=await Promise.all([api('/spaces'),api('/oauth/providers'),api('/platforms')]);readiness=providerData;platforms=platformData.capabilities||{};const spaces=spacesData.spaces||[],current=q('space').value;q('space').innerHTML=spaces.map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');if(current&&spaces.some(s=>s.id===current))q('space').value=current;q('space').onchange=loadHouse;if(spaces.length){await loadHouse()}else{house=null;runtime=null;q('summary').innerHTML='';q('truth').textContent='Create a Social House in Social Management before connecting social accounts.';q('providers').innerHTML='<div class="empty">No Social Houses yet.</div>'}}catch(e){notice(e.message,true)}}
function connectProvider(provider){const space=q('space').value,cfg=readiness?.providers?.[provider];if(!space)return notice('Choose a Social House first.',true);if(!PROVIDERS[provider]||PROVIDERS[provider].mode!=='oauth')return notice('This provider does not use member OAuth.',true);if(readiness?.encrypted_vault_ready!==true)return notice('Encrypted OAuth vault is not configured.',true);if(!cfg?.configured)return notice(`${PROVIDERS[provider].label} provider application is not configured.`,true);window.location.assign(`${BASE}/oauth/${encodeURIComponent(provider)}/start?space_id=${encodeURIComponent(space)}`)}
async function disconnect(provider,id){const space=q('space').value;if(!space||!id)return;try{await api(`/oauth/${encodeURIComponent(provider)}/disconnect?space_id=${encodeURIComponent(space)}&connection_id=${encodeURIComponent(id)}`,{method:'POST'});await loadHouse();notice(`${PROVIDERS[provider]?.label||provider} disconnected. The member OAuth credential is no longer available to the publishing worker.`)}catch(e){notice(e.message,true)}}
async function saveFacebook(){const space=q('space').value,page=q('fbPage')?.value?.trim(),alias=q('fbAlias')?.value?.trim(),label=q('fbLabel')?.value?.trim()||'Facebook Page';if(!space)return notice('Choose a Social House first.',true);if(!/^\d{2,40}$/.test(page||''))return notice('Enter a numeric Facebook Page ID.',true);if(!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(alias||''))return notice('Enter the deployment token alias, not an access token.',true);try{const result=await api(`/spaces/${encodeURIComponent(space)}/connections/facebook-pages`,{method:'PUT',body:JSON.stringify({account_label:label,page_id:page,token_alias:alias})});q('fbAlias').value='';await loadHouse();notice(result.facebook?.deployment_ready?'Facebook Pages deployment is ready for supported Page posts.':'Facebook Page saved. Runtime checks show remaining deployment configuration above.',!result.facebook?.deployment_ready)}catch(e){notice(e.message,true)}}
async function disconnectFacebook(){const space=q('space').value;if(!space)return;try{await api(`/spaces/${encodeURIComponent(space)}/connections/facebook-pages`,{method:'DELETE'});await loadHouse();notice('Facebook Page disconnected from this Social House. Deployment secrets were not exposed or deleted.') }catch(e){notice(e.message,true)}}
reloadAll();
</script></body></html>"""
    return HTMLResponse(
        html.replace("__ACCENT__", accent)
        .replace("__SECONDARY__", secondary)
        .replace("__NICHE__", niche)
        .replace("__ROLE__", escape(str(role).title()))
    )


__all__ = [
    "FacebookPagesConnectionRequest",
    "configure_facebook_pages",
    "disconnect_facebook_pages",
    "router",
    "social_connection_runtime",
]
