from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from fastapi import APIRouter

router = APIRouter(include_in_schema=False)

UI_SCRIPT = r"""
(()=>{
  const api='/aura-intelligence/api';
  function request(url,opt={}){return fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt}).then(async r=>{let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b})}
  function toast(message,bad=false){try{note(message,bad)}catch(_){console[bad?'error':'log'](message)}}

  // Make verified source URLs navigable without changing the base markdown parser.
  if(typeof markdown==='function'){
    const baseMarkdown=markdown;
    markdown=function(value){
      const html=baseMarkdown(value);
      return html.replace(/https:\/\/[^\s<]+/g,url=>{
        const clean=url.replace(/[),.;]+$/,'');
        const tail=url.slice(clean.length);
        return `<a href="${clean}" target="_blank" rel="noopener noreferrer" style="color:#58dfff;text-decoration:underline">${clean}</a>${tail}`;
      });
    };
  }

  // Add rights-gated project promotion controls to messages that contain attachments.
  if(typeof messageHTML==='function'){
    const baseMessageHTML=messageHTML;
    messageHTML=function(message,tools=[]){
      let html=baseMessageHTML(message,tools);
      const attachments=message&&message.attachments||[];
      if(attachments.length){
        const buttons=attachments.map(a=>`<button class="mini" data-promote-attachment="${String(a.id).replace(/[^A-Za-z0-9_-]/g,'')}">＋ Project: ${esc(a.name)}</button>`).join('');
        html=html.replace('<div class="actions">',`<div class="actions">${buttons}`);
      }
      return html;
    };
  }

  document.addEventListener('click',async event=>{
    const button=event.target.closest('[data-promote-attachment]');
    if(!button)return;
    event.preventDefault();
    try{
      if(typeof current==='undefined'||!current)throw new Error('Open an Aura conversation first.');
      const project=document.getElementById('project')?.value||'';
      if(!project)throw new Error('Pin a project to this conversation first.');
      if(!confirm('Add this attachment to the pinned project? Confirm only if you own it or are authorised to use it.'))return;
      button.disabled=true;
      button.textContent='Adding…';
      const result=await request(`${api}/threads/${encodeURIComponent(current)}/attachments/${encodeURIComponent(button.dataset.promoteAttachment)}/promote`,{
        method:'POST',body:JSON.stringify({project_name:project,rights_confirmed:true})
      });
      button.textContent=result.idempotent?'✓ Already in project':'✓ Added to project';
      toast(result.idempotent?'This file was already registered in the project.':'Attachment added to the project with its rights record.');
    }catch(error){button.disabled=false;button.textContent='＋ Add to project';toast(error.message,true)}
  });

  // Persistent reasoning-mode selector layered into the existing top bar.
  const top=document.querySelector('.top');
  const projectSelect=document.getElementById('project');
  if(top&&projectSelect&&!document.getElementById('auraReasoningMode')){
    const select=document.createElement('select');
    select.id='auraReasoningMode';
    select.className='select';
    select.title='Aura reasoning mode';
    select.innerHTML='<option value="fast">⚡ Fast</option><option value="auto">✦ Auto</option><option value="deep">◈ Deep</option><option value="creative">✧ Creative</option>';
    top.insertBefore(select,projectSelect);
    select.addEventListener('change',async()=>{
      try{
        if(typeof current==='undefined'||!current)return;
        const result=await request(`${api}/threads/${encodeURIComponent(current)}/reasoning-mode`,{method:'PUT',body:JSON.stringify({mode:select.value})});
        toast(result.detail||`Aura ${select.value} mode active.`);
      }catch(error){toast(error.message,true)}
    });
    window.__auraSyncReasoningMode=async function(){
      try{
        if(typeof current==='undefined'||!current)return;
        const result=await request(`${api}/threads/${encodeURIComponent(current)}/reasoning-mode`);
        select.value=result.mode||'auto';
      }catch(_){select.value='auto'}
    };
    if(typeof openThread==='function'){
      const baseOpenThread=openThread;
      openThread=async function(id){const result=await baseOpenThread(id);await window.__auraSyncReasoningMode();return result};
    }
    setTimeout(()=>window.__auraSyncReasoningMode(),400);
  }
})();
"""


@router.get("/aura-intelligence/ui-extension.js")
def aura_ui_extension():
    return Response(
        content=UI_SCRIPT,
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


class AuraUIExtensionMiddleware(BaseHTTPMiddleware):
    """Inject a same-origin UI extension only into the Aura realtime HTML page.

    The base page remains independently usable. API, streaming and media responses pass
    through untouched, and no member data is interpolated into the injected script tag.
    """

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
