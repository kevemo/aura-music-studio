from __future__ import annotations

from fastapi import APIRouter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

router = APIRouter(include_in_schema=False)

UI_SCRIPT = r"""
(()=>{
  const api='/aura-intelligence/api';
  function request(url,opt={}){return fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt}).then(async r=>{let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b})}
  function toast(message,bad=false){try{note(message,bad)}catch(_){console[bad?'error':'log'](message)}}

  // Linkify only text nodes using DOM-created HTTPS anchors. Never interpolate a user URL
  // directly into an HTML attribute string.
  function linkifyHttps(html){
    const root=document.createElement('div');root.innerHTML=html;
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);const nodes=[];
    while(walker.nextNode())nodes.push(walker.currentNode);
    for(const node of nodes){
      if(node.parentElement?.closest('a,code,pre,script,style'))continue;
      const text=node.nodeValue||'';const re=/https:\/\/[^\s]+/g;let match,last=0,changed=false;const frag=document.createDocumentFragment();
      while((match=re.exec(text))){
        let raw=match[0],trail='';while(/[),.;]$/.test(raw)){trail=raw.slice(-1)+trail;raw=raw.slice(0,-1)}
        let parsed;try{parsed=new URL(raw)}catch(_){continue}if(parsed.protocol!=='https:')continue;
        changed=true;frag.append(document.createTextNode(text.slice(last,match.index)));
        const a=document.createElement('a');a.href=parsed.href;a.target='_blank';a.rel='noopener noreferrer';a.style.color='#58dfff';a.style.textDecoration='underline';a.textContent=raw;frag.append(a);
        if(trail)frag.append(document.createTextNode(trail));last=match.index+match[0].length;
      }
      if(changed){frag.append(document.createTextNode(text.slice(last)));node.replaceWith(frag)}
    }
    return root.innerHTML;
  }
  if(typeof markdown==='function'){const baseMarkdown=markdown;markdown=function(value){return linkifyHttps(baseMarkdown(value))}}

  // Rights-gated project promotion beside every chat attachment.
  if(typeof messageHTML==='function'){
    const baseMessageHTML=messageHTML;
    messageHTML=function(message,tools=[]){
      let html=baseMessageHTML(message,tools);const attachments=message&&message.attachments||[];
      if(attachments.length){const buttons=attachments.map(a=>`<button class="mini" data-promote-attachment="${String(a.id).replace(/[^A-Za-z0-9_-]/g,'')}">＋ Project: ${esc(a.name)}</button>`).join('');html=html.replace('<div class="actions">',`<div class="actions">${buttons}`)}
      return html;
    };
  }

  document.addEventListener('click',async event=>{
    const button=event.target.closest('[data-promote-attachment]');if(!button)return;event.preventDefault();
    try{
      if(typeof current==='undefined'||!current)throw new Error('Open an Aura conversation first.');
      const project=document.getElementById('project')?.value||'';if(!project)throw new Error('Pin a project to this conversation first.');
      if(!confirm('Add this attachment to the pinned project? Confirm only if you own it or are authorised to use it.'))return;
      button.disabled=true;button.textContent='Adding…';
      const result=await request(`${api}/threads/${encodeURIComponent(current)}/attachments/${encodeURIComponent(button.dataset.promoteAttachment)}/promote`,{method:'POST',body:JSON.stringify({project_name:project,rights_confirmed:true})});
      button.textContent=result.idempotent?'✓ Already in project':'✓ Added to project';toast(result.idempotent?'This file was already registered in the project.':'Attachment added to the project with its rights record.');
    }catch(error){button.disabled=false;button.textContent='＋ Add to project';toast(error.message,true)}
  });

  // Persistent reasoning mode selector.
  const top=document.querySelector('.top');const projectSelect=document.getElementById('project');
  if(top&&projectSelect&&!document.getElementById('auraReasoningMode')){
    const select=document.createElement('select');select.id='auraReasoningMode';select.className='select';select.title='Aura reasoning mode';select.innerHTML='<option value="fast">⚡ Fast</option><option value="auto">✦ Auto</option><option value="deep">◈ Deep</option><option value="creative">✧ Creative</option>';top.insertBefore(select,projectSelect);
    select.addEventListener('change',async()=>{try{if(typeof current==='undefined'||!current)return;const result=await request(`${api}/threads/${encodeURIComponent(current)}/reasoning-mode`,{method:'PUT',body:JSON.stringify({mode:select.value})});toast(result.detail||`Aura ${select.value} mode active.`)}catch(error){toast(error.message,true)}});
    window.__auraSyncReasoningMode=async function(){try{if(typeof current==='undefined'||!current)return;const result=await request(`${api}/threads/${encodeURIComponent(current)}/reasoning-mode`);select.value=result.mode||'auto'}catch(_){select.value='auto'}};
    if(typeof openThread==='function'){const baseOpenThread=openThread;openThread=async function(id){const result=await baseOpenThread(id);await window.__auraSyncReasoningMode();return result}};
    setTimeout(()=>window.__auraSyncReasoningMode(),400);
  }

  // Lightweight workspace controls in the existing sidebar.
  const foot=document.querySelector('.sideFoot');
  if(foot&&!document.getElementById('auraExportChat')){
    const exportBtn=document.createElement('button');exportBtn.id='auraExportChat';exportBtn.className='btn';exportBtn.textContent='↓ Export chat';exportBtn.onclick=()=>{if(typeof current==='undefined'||!current)return toast('Open a conversation first.',true);window.location.href=`${api}/threads/${encodeURIComponent(current)}/export.md`};foot.prepend(exportBtn);
    const capBtn=document.createElement('button');capBtn.id='auraCapabilities';capBtn.className='btn';capBtn.textContent='⚙ Aura capabilities';foot.prepend(capBtn);
    const panel=document.createElement('div');panel.id='auraCapabilityPanel';panel.style.cssText='position:fixed;right:14px;top:78px;bottom:14px;width:min(460px,calc(100vw - 28px));z-index:95;background:#080c18f8;border:1px solid #ffffff20;border-radius:16px;padding:16px;overflow:auto;display:none;box-shadow:0 20px 70px #000b';document.body.append(panel);
    capBtn.onclick=async()=>{
      if(panel.style.display==='block'){panel.style.display='none';return}
      panel.style.display='block';panel.innerHTML='<b>Aura capabilities</b><p style="color:#a9b2c8">Checking this deployment…</p>';
      try{
        const data=await request(`${api}/capabilities`);const runtime=data.runtime||{},software=data.software||{};
        const ready=[['Reasoning model',runtime.reasoning?.provider_mode||'configured by host'],['Deep research',runtime.deep_research_ready?'Ready':'Search backend not configured'],['Vision',runtime.vision?.configured?'Ready':'Not configured'],['Speech input',runtime.speech?.stt_configured?'Ready':'Not configured'],['Speech output',runtime.speech?.tts_configured?'Ready':'Not configured'],['Image renderer',runtime.image_generation_ready?'Configured':'Adapter only'],['Video renderer',runtime.video_generation_ready?'Configured':'Adapter only']];
        panel.innerHTML=`<div style="display:flex;justify-content:space-between;gap:10px"><b>Aura capabilities</b><button id="auraCloseCapabilities" class="mini">✕</button></div><p style="color:#a9b2c8">Software connected vs services configured on this host.</p>${ready.map(x=>`<div style="padding:8px 0;border-bottom:1px solid #ffffff12"><b>${esc(x[0])}</b><div style="color:#a9b2c8">${esc(x[1])}</div></div>`).join('')}<p style="color:#a9b2c8;margin-top:14px">Connected software features: ${Object.values(software).filter(Boolean).length}</p>`;
        document.getElementById('auraCloseCapabilities').onclick=()=>panel.style.display='none';
      }catch(error){panel.innerHTML=`<b>Aura capabilities</b><p style="color:#ff8fa6">${esc(error.message)}</p>`}
    };
  }
})();
"""


@router.get("/aura-intelligence/ui-extension.js")
def aura_ui_extension():
    return Response(content=UI_SCRIPT, media_type="application/javascript", headers={"Cache-Control": "no-store"})


class AuraUIExtensionMiddleware(BaseHTTPMiddleware):
    """Inject the same-origin extension only into the signed-in Aura HTML surface."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/aura-intelligence" or request.method.upper() != "GET":
            return response
        content_type = (response.headers.get("content-type") or "").lower()
        if not content_type.startswith("text/html"):
            return response
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return Response(content=body, status_code=response.status_code, headers=dict(response.headers), background=response.background)
        marker = "<script src='/aura-intelligence/ui-extension.js'></script>"
        if marker not in text:
            text = text.replace("</body>", marker + "</body>")
        encoded = text.encode("utf-8")
        migrated = Response(content=encoded, status_code=response.status_code, background=response.background)
        raw_headers = [(key, value) for key, value in response.raw_headers if key.lower() != b"content-length"]
        raw_headers.append((b"content-length", str(len(encoded)).encode("ascii")))
        migrated.raw_headers = raw_headers
        return migrated


__all__ = ["router", "AuraUIExtensionMiddleware"]
