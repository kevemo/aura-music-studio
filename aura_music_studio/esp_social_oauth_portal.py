from __future__ import annotations

from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .esp_niche import require_esp_social_member

router = APIRouter()


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
    role = escape(
        str(
            esp_membership.get("roles")
            or ("owner" if esp_membership.get("status") == "owner" else "member")
        )
    )

    html = r"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'><meta name='robots' content='noindex,nofollow'><meta name='theme-color' content='#05040b'><title>ESP Social Connections</title><style>
:root{--accent:__ACCENT__;--secondary:__SECONDARY__;--bg:#03040a;--panel:#0d0f1b;--line:#ffffff1c;--text:#fff;--muted:#b7bbce;--green:#79e3a8;--amber:#ffd36b;--red:#ff8f9d}*{box-sizing:border-box}body{margin:0;min-height:100vh;color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:radial-gradient(circle at 8% 0,var(--secondary),transparent 28%),radial-gradient(circle at 92% 4%,var(--accent),transparent 23%),linear-gradient(#03040a,#070811 65%,#020309)}a{color:inherit;text-decoration:none}button,select{font:inherit}.wrap{width:min(1240px,calc(100% - 28px));margin:auto}.nav{position:sticky;top:0;z-index:10;background:#05060bee;backdrop-filter:blur(18px);border-bottom:1px solid var(--line)}.navin{min-height:72px;display:flex;align-items:center;justify-content:space-between;gap:14px}.brand{font-weight:950}.brand small{display:block;color:var(--accent);font-size:.64rem;text-transform:uppercase;letter-spacing:.08em}.actions{display:flex;gap:7px;flex-wrap:wrap}.btn{border:1px solid var(--line);background:#ffffff08;color:#fff;border-radius:12px;padding:9px 12px;font-weight:850;cursor:pointer}.btn.primary{border:0;background:linear-gradient(115deg,var(--accent),var(--secondary));color:#130b18}.btn:disabled{opacity:.45;cursor:not-allowed}.hero{padding:42px 0 20px}.eyebrow{color:var(--accent);font-size:.72rem;font-weight:950;letter-spacing:.17em;text-transform:uppercase}.hero h1{font-size:clamp(2.5rem,6vw,4.8rem);letter-spacing:-.05em;line-height:.95;margin:.14em 0 .2em}.hero h1 span{background:linear-gradient(95deg,#fff,var(--accent),var(--secondary));background-clip:text;color:transparent}.lead,.muted{color:var(--muted)}.lead{max-width:920px;line-height:1.6}.toolbar,.panel,.provider,.connection{border:1px solid var(--line);background:linear-gradient(145deg,#101323e8,#080a14f0);border-radius:18px;padding:14px}.toolbar{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:end;margin-bottom:12px}.field{width:100%;border:1px solid var(--line);background:#070913;color:#fff;border-radius:11px;padding:10px}.truth{border:1px solid #79e3a83d;background:#79e3a80c;border-radius:16px;padding:13px;margin-bottom:12px;color:#dff9e8}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.provider h3,.connection h4{margin:.25rem 0}.provider p,.connection p{font-size:.82rem;line-height:1.5}.status{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-size:.68rem;font-weight:850}.status.good{color:var(--green);border-color:#79e3a84b}.status.warn{color:var(--amber);border-color:#ffd36b4b}.scope{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.65rem;color:#d7d9e5;overflow-wrap:anywhere}.section{margin:20px 0}.connections{display:grid;gap:9px}.connection{display:grid;grid-template-columns:1fr auto;gap:12px;align-items:center}.notice{display:none;border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin:12px 0}.notice.show{display:block}.notice.error{color:#ffd7dc;border-color:#ff8f9d55}@media(max-width:850px){.grid{grid-template-columns:1fr}.toolbar,.connection{grid-template-columns:1fr}.navin{align-items:flex-start;padding:10px 0}}
</style></head><body><nav class='nav'><div class='wrap navin'><a class='brand' href='/command-center/social'>Elevate Souls Productions<small>Private Social Account Connections</small></a><div class='actions'><a class='btn' href='/command-center/social'>Social Management</a><a class='btn' href='/command-center/social/publish-queue'>Publish Queue</a><a class='btn' href='/command-center'>ESP Hub</a></div></div></nav><main class='wrap'><section class='hero'><div class='eyebrow'>__NICHE__ · __ROLE__</div><h1>Connect accounts. <span>Keep credentials private.</span></h1><p class='lead'>Authorise an ESP Social House to publish through the platform's official OAuth flow. Access and refresh tokens are encrypted server-side and are never displayed on this page or stored in Social House JSON.</p></section><div class='truth'><strong>Truthful connection state:</strong> a provider is available only when the ESP deployment has a configured provider application. A member is shown as connected only after the provider callback succeeds and the required publishing permissions are verified.</div><div id='notice' class='notice'></div><section class='toolbar'><label><div class='eyebrow'>Social House</div><select id='space' class='field' onchange='spaceChanged()'><option value=''>Loading…</option></select></label><button class='btn' onclick='reload()'>Refresh state</button></section><section class='section'><div class='eyebrow'>Official providers</div><h2>Authorise publishing</h2><div id='providers' class='grid'><div class='provider'>Loading provider configuration…</div></div></section><section class='section'><div class='eyebrow'>Selected Social House</div><h2>Connected accounts</h2><div id='connections' class='connections'><div class='connection'>Choose a Social House.</div></div></section></main><script>
const BASE='/command-center/api/social';let spaces=[],house=null,providerState={};const q=id=>document.getElementById(id);const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function notice(m,bad=false){const n=q('notice');n.textContent=m;n.className='notice show'+(bad?' error':'');clearTimeout(window._msg);window._msg=setTimeout(()=>n.className='notice',6500)}
async function api(path,opt={}){const r=await fetch(BASE+path,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b}
function providerName(p){return ({tiktok:'TikTok',instagram:'Instagram',youtube:'YouTube'})[p]||p}
function providerCard(name,state){const configured=!!state?.configured,scopes=Array.isArray(state?.scopes)?state.scopes:[];const status=configured?`<span class='status good'>Provider app configured</span>`:`<span class='status warn'>Provider app configuration required</span>`;return `<article class='provider'><div>${status}</div><h3>${esc(providerName(name))}</h3><p class='muted'>${name==='tiktok'?'TikTok Login Kit + Content Posting API':name==='instagram'?'Instagram Login for professional Business/Creator accounts':'Google OAuth + YouTube Data API'}</p><div>${scopes.map(s=>`<div class='scope'>${esc(s)}</div>`).join('')}</div><p class='muted'>Redirect: <span class='scope'>${esc(state?.redirect_uri||'not configured')}</span></p><button class='btn primary' ${configured?'':'disabled'} onclick="connectProvider('${esc(name)}')">Connect ${esc(providerName(name))}</button></article>`}
function renderProviders(){q('providers').innerHTML=Object.entries(providerState?.providers||{}).map(([n,s])=>providerCard(n,s)).join('')||'<div class="provider">No OAuth providers are configured.</div>'}
function renderSpaces(){const s=q('space'),previous=s.value;s.innerHTML=spaces.length?spaces.map(x=>`<option value='${esc(x.id)}'>${esc(x.name)}</option>`).join(''):'<option value="">No Social Houses</option>';if(spaces.some(x=>x.id===previous))s.value=previous}
async function loadHouse(){const id=q('space').value;if(!id){house=null;renderConnections();return}house=await api(`/spaces/${encodeURIComponent(id)}`);renderConnections()}
function renderConnections(){const list=house?.connections||[];q('connections').innerHTML=list.length?list.map(c=>{const oauth=String(c.token_secret_ref||'').startsWith('social-oauth://'),connected=c.state==='connected';return `<article class='connection'><div><span class='status ${connected?'good':'warn'}'>${esc(c.state)}</span><h4>${esc(c.account_label||providerName(c.platform))}</h4><p class='muted'>${esc(providerName(c.platform))}${c.account_external_id?` · ${esc(c.account_external_id)}`:''}${oauth?' · encrypted member OAuth':' · deployment/placeholder connection'}</p></div>${oauth&&connected?`<button class='btn' onclick="disconnectProvider('${esc(c.platform)}','${esc(c.id)}')">Disconnect</button>`:''}</article>`}).join(''):'<div class="connection"><span class="muted">No accounts connected to this Social House yet.</span></div>'}
function connectProvider(provider){const space=q('space').value;if(!space)return notice('Choose or create a Social House first.',true);if(!providerState?.providers?.[provider]?.configured)return notice(`${providerName(provider)} provider app is not configured on this deployment.`,true);location.href=`${BASE}/oauth/${encodeURIComponent(provider)}/start?space_id=${encodeURIComponent(space)}`}
async function disconnectProvider(provider,id){const space=q('space').value;if(!space)return;try{await api(`/oauth/${encodeURIComponent(provider)}/disconnect?space_id=${encodeURIComponent(space)}&connection_id=${encodeURIComponent(id)}`,{method:'POST'});await loadHouse();notice(`${providerName(provider)} disconnected. Local encrypted credentials were removed.`)}catch(e){notice(e.message,true)}}
async function spaceChanged(){try{await loadHouse()}catch(e){notice(e.message,true)}}
async function reload(){try{const [s,p]=await Promise.all([api('/spaces'),api('/oauth/providers')]);spaces=s.spaces||[];providerState=p||{};renderSpaces();renderProviders();await loadHouse()}catch(e){notice(e.message,true)}}
reload();
</script></body></html>"""
    return HTMLResponse(
        html.replace("__ACCENT__", accent)
        .replace("__SECONDARY__", secondary)
        .replace("__NICHE__", niche)
        .replace("__ROLE__", role)
    )


__all__ = ["router"]
