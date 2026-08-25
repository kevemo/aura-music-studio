from __future__ import annotations

from fastapi import APIRouter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

router = APIRouter(include_in_schema=False)

CONNECTORS_SCRIPT = r"""
(()=>{
  const api='/aura-intelligence/api';
  function q(id){return document.getElementById(id)}
  function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot',"'":'&#39;'}[c]||c))}
  async function request(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b}
  function toast(message,bad=false){try{note(message,bad)}catch(_){console[bad?'error':'log'](message)}}

  function ensurePanel(){
    if(q('auraConnectorsPanel'))return q('auraConnectorsPanel');
    const panel=document.createElement('div');panel.id='auraConnectorsPanel';panel.className='drawer';panel.innerHTML=`
      <div style="display:flex;align-items:center;gap:8px"><div style="flex:1"><b>Aura Connectors</b><div class="muted" style="font-size:.7rem">Private services you explicitly connect</div></div><button id="auraConnectorsClose" class="btn">✕</button></div>
      <div id="auraConnectorRuntime" style="margin:12px 0;padding:10px;border:1px solid #ffffff18;border-radius:12px"></div>
      <div style="padding:12px;border:1px solid #ffffff18;border-radius:12px;background:#ffffff04">
        <div style="display:flex;align-items:center;gap:8px"><div style="font-size:1.3rem">G</div><div style="flex:1"><b>Google Workspace</b><div class="muted" style="font-size:.68rem">Read-only OAuth connection</div></div></div>
        <div id="auraGoogleConnected" style="margin-top:10px"></div>
        <div id="auraGoogleScopes" style="display:grid;gap:6px;margin-top:10px">
          <label class="toggle"><input type="checkbox" id="auraGoogleDrive" checked> Drive search</label>
          <label class="toggle"><input type="checkbox" id="auraGoogleCalendar" checked> Calendar events</label>
          <label class="toggle"><input type="checkbox" id="auraGoogleGmail" checked> Gmail search</label>
        </div>
        <div style="display:flex;gap:7px;margin-top:10px;flex-wrap:wrap"><button id="auraGoogleConnect" class="btn primary">Connect Google</button><button id="auraGoogleDisconnect" class="btn" style="display:none">Disconnect</button></div>
        <div class="muted" style="font-size:.64rem;line-height:1.45;margin-top:9px">Aura receives only the scopes you select. Tokens remain encrypted server-side and are never inserted into chat messages. This release is read-only.</div>
      </div>
      <div style="margin-top:14px"><b>What Aura can do when connected</b><div class="muted" style="font-size:.7rem;line-height:1.6;margin-top:6px">Try: “search my Drive for campaign brief”, “what’s on my calendar this week?”, or “search Gmail for the latest invoice”.</div></div>`;
    document.body.append(panel);q('auraConnectorsClose').onclick=()=>panel.classList.remove('open');q('auraGoogleConnect').onclick=connectGoogle;q('auraGoogleDisconnect').onclick=disconnectGoogle;return panel;
  }

  function runtimeHTML(runtime){const vault=!!runtime.encrypted_vault_ready,oauth=!!runtime.google_oauth_configured,ready=vault&&oauth;return `<div style="display:flex;gap:8px;align-items:center"><span style="width:9px;height:9px;border-radius:50%;background:${ready?'#73e2aa':'#ffb36b'}"></span><b>${ready?'Connector runtime ready':'Connector setup required'}</b></div><div class="muted" style="font-size:.67rem;margin-top:5px">Encrypted vault: ${vault?'ready':'missing master key'} · Google OAuth: ${oauth?'configured':'client credentials missing'}</div>`}

  async function load(){
    try{const data=await request(`${api}/connectors`);ensurePanel();q('auraConnectorRuntime').innerHTML=runtimeHTML(data.runtime||{});const google=(data.connectors||[]).find(x=>x.provider==='google');const connected=q('auraGoogleConnected'),connect=q('auraGoogleConnect'),disconnect=q('auraGoogleDisconnect');if(google){connected.innerHTML=`<div style="padding:8px;border-radius:9px;background:#73e2aa12;border:1px solid #73e2aa33"><b>Connected${google.account_label?': '+esc(google.account_label):''}</b><div class="muted" style="font-size:.67rem">Scopes: ${esc((google.services||[]).join(', '))} · encrypted at rest · read-only</div></div>`;connect.textContent='Reconnect / change scopes';disconnect.style.display='inline-block'}else{connected.innerHTML='<div class="muted" style="font-size:.7rem">No Google account connected.</div>';connect.textContent='Connect Google';disconnect.style.display='none'}}catch(error){toast(error.message,true)}
  }
  function selected(){const rows=[];if(q('auraGoogleDrive').checked)rows.push('drive');if(q('auraGoogleCalendar').checked)rows.push('calendar');if(q('auraGoogleGmail').checked)rows.push('gmail');return rows}
  function connectGoogle(){const services=selected();if(!services.length)return toast('Select at least one Google service.',true);window.location.href=`/aura-intelligence/connectors/google/start?services=${encodeURIComponent(services.join(','))}`}
  async function disconnectGoogle(){if(!confirm('Disconnect Google from Aura and revoke/remove this saved credential?'))return;try{await request(`${api}/connectors/google`,{method:'DELETE'});toast('Google disconnected from Aura.');await load()}catch(error){toast(error.message,true)}}

  const foot=document.querySelector('.sideFoot');if(foot&&!q('auraConnectorsButton')){const b=document.createElement('button');b.id='auraConnectorsButton';b.className='btn';b.textContent='⌁ Connectors';b.onclick=async()=>{ensurePanel().classList.add('open');await load()};foot.prepend(b)}
  const params=new URLSearchParams(location.search);if(params.get('connector')==='google'){const status=params.get('status');setTimeout(()=>toast(status==='connected'?'Google connected to Aura.':'Google connection was not completed.',status!=='connected'),300);history.replaceState({},document.title,location.pathname)}
})();
"""


@router.get('/aura-intelligence/connectors-ui.js')
def connectors_ui_script():
    return Response(content=CONNECTORS_SCRIPT, media_type='application/javascript', headers={'Cache-Control':'no-store'})


class AuraConnectorsUIMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path != '/aura-intelligence' or request.method.upper() != 'GET':
            return response
        content_type=(response.headers.get('content-type') or '').lower()
        if not content_type.startswith('text/html'):
            return response
        body=b''
        async for chunk in response.body_iterator:
            body+=chunk
        try:text=body.decode('utf-8')
        except UnicodeDecodeError:return Response(content=body,status_code=response.status_code,headers=dict(response.headers),background=response.background)
        marker="<script src='/aura-intelligence/connectors-ui.js'></script>"
        if marker not in text:text=text.replace('</body>',marker+'</body>')
        encoded=text.encode('utf-8');migrated=Response(content=encoded,status_code=response.status_code,background=response.background)
        raw=[(k,v) for k,v in response.raw_headers if k.lower()!=b'content-length'];raw.append((b'content-length',str(len(encoded)).encode('ascii')));migrated.raw_headers=raw;return migrated


__all__=['router','AuraConnectorsUIMiddleware']
