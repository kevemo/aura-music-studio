from __future__ import annotations

from fastapi import APIRouter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .aura_connectors_ui import router as aura_connectors_ui_router
from .aura_tasks_ui import router as aura_tasks_ui_router

router = APIRouter(include_in_schema=False)
router.include_router(aura_tasks_ui_router)
router.include_router(aura_connectors_ui_router)

ARTIFACT_UI_SCRIPT = r"""
(()=>{
  const api='/aura-intelligence/api';let artifactRows=[],activeArtifact=null;
  function request(url,opt={}){return fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt}).then(async r=>{let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b})}
  function toast(message,bad=false){try{note(message,bad)}catch(_){console[bad?'error':'log'](message)}}
  const foot=document.querySelector('.sideFoot');if(!foot||document.getElementById('auraArtifactsButton'))return;
  const button=document.createElement('button');button.id='auraArtifactsButton';button.className='btn';button.textContent='▤ Aura artifacts';foot.prepend(button);
  const panel=document.createElement('div');panel.id='auraArtifactsPanel';panel.style.cssText='position:fixed;right:0;top:0;bottom:0;width:min(620px,100%);z-index:99;background:#080c18fc;border-left:1px solid #ffffff20;padding:16px;overflow:auto;display:none;box-shadow:-20px 0 70px #000a';panel.innerHTML=`
    <div style="display:flex;align-items:center;gap:8px"><b style="font-size:1.05rem;flex:1">Aura Artifacts</b><button id="auraArtifactClose" class="mini">✕</button></div>
    <p style="color:#a9b2c8;font-size:.78rem">Private, versioned documents/code for this conversation. Code is editable but never executed on the web host.</p>
    <div style="display:flex;gap:7px;flex-wrap:wrap"><button id="auraArtifactNew" class="btn">＋ New</button><button id="auraArtifactRefresh" class="btn">↻ Refresh</button></div>
    <div id="auraArtifactList" style="margin-top:12px"></div>
    <div id="auraArtifactEditor" style="display:none;margin-top:14px;border-top:1px solid #ffffff18;padding-top:14px">
      <input id="auraArtifactTitle" class="search" maxlength="160" placeholder="Artifact title">
      <div style="display:flex;gap:7px;margin-top:7px"><select id="auraArtifactKind" class="select"><option>document</option><option>markdown</option><option>code</option><option>json</option><option>yaml</option><option>csv</option><option>text</option><option>prompt</option><option>lyrics</option></select><input id="auraArtifactLanguage" class="search" style="margin-top:0" maxlength="80" placeholder="Language/format"></div>
      <textarea id="auraArtifactContent" class="search" style="min-height:340px;resize:vertical;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;line-height:1.45" maxlength="200000"></textarea>
      <div style="display:flex;gap:7px;flex-wrap:wrap;margin-top:8px"><button id="auraArtifactSave" class="btn primary">Save new version</button><button id="auraArtifactVersions" class="btn">Version history</button><button id="auraArtifactDelete" class="btn">Delete</button></div>
      <div id="auraArtifactMeta" style="color:#a9b2c8;font-size:.72rem;margin-top:8px"></div><div id="auraArtifactHistory"></div>
    </div>`;document.body.append(panel);
  const $=id=>document.getElementById(id);
  function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
  async function load(){if(typeof current==='undefined'||!current){artifactRows=[];renderList();return}try{artifactRows=await request(`${api}/threads/${encodeURIComponent(current)}/artifacts`);renderList()}catch(error){toast(error.message,true)}}
  function renderList(){$('auraArtifactList').innerHTML=artifactRows.length?artifactRows.map(a=>`<button class="btn" data-artifact-open="${esc(a.id)}" style="display:block;width:100%;text-align:left;margin:6px 0"><b>${esc(a.title)}</b><div style="font-size:.7rem;color:#a9b2c8">${esc(a.kind)}${a.language?' · '+esc(a.language):''} · v${a.current_version} · ${a.characters} chars</div></button>`).join(''):'<p style="color:#a9b2c8">No Artifacts in this conversation yet. Ask Aura to create one, or press New.</p>'}
  function blank(){activeArtifact=null;$('auraArtifactEditor').style.display='block';$('auraArtifactTitle').value='Untitled Artifact';$('auraArtifactKind').value='document';$('auraArtifactLanguage').value='';$('auraArtifactContent').value='';$('auraArtifactMeta').textContent='New artifact · code execution disabled';$('auraArtifactHistory').innerHTML=''}
  async function openArtifact(id){try{activeArtifact=await request(`${api}/threads/${encodeURIComponent(current)}/artifacts/${encodeURIComponent(id)}`);$('auraArtifactEditor').style.display='block';$('auraArtifactTitle').value=activeArtifact.title;$('auraArtifactKind').value=activeArtifact.kind;$('auraArtifactKind').disabled=true;$('auraArtifactLanguage').value=activeArtifact.language||'';$('auraArtifactContent').value=activeArtifact.content||'';$('auraArtifactMeta').textContent=`Version ${activeArtifact.current_version} · ${activeArtifact.characters} characters · code execution disabled`;$('auraArtifactHistory').innerHTML=''}catch(error){toast(error.message,true)}}
  async function save(){if(!current)return toast('Open a conversation first.',true);const title=$('auraArtifactTitle').value.trim(),kind=$('auraArtifactKind').value,language=$('auraArtifactLanguage').value.trim(),content=$('auraArtifactContent').value;if(!title)return toast('Artifact title is required.',true);try{if(activeArtifact){activeArtifact=await request(`${api}/threads/${encodeURIComponent(current)}/artifacts/${encodeURIComponent(activeArtifact.id)}`,{method:'PATCH',body:JSON.stringify({title,language,content,note:'Edited in Aura Artifact workspace'})})}else{activeArtifact=await request(`${api}/threads/${encodeURIComponent(current)}/artifacts`,{method:'POST',body:JSON.stringify({title,kind,language,content})});$('auraArtifactKind').disabled=true}toast(`Artifact saved as version ${activeArtifact.current_version}.`);$('auraArtifactMeta').textContent=`Version ${activeArtifact.current_version} · ${activeArtifact.characters} characters · code execution disabled`;await load()}catch(error){toast(error.message,true)}}
  async function versions(){if(!activeArtifact)return;try{const rows=await request(`${api}/threads/${encodeURIComponent(current)}/artifacts/${encodeURIComponent(activeArtifact.id)}/versions`);$('auraArtifactHistory').innerHTML=`<div style="margin-top:12px"><b>Version history</b>${rows.map(v=>`<div style="border:1px solid #ffffff14;border-radius:9px;padding:7px;margin:5px 0"><b>v${v.version}</b> · ${v.characters} chars <span style="color:#a9b2c8">${esc(v.note||'')}</span> <button class="mini" data-artifact-restore="${v.version}">Restore</button></div>`).join('')}</div>`}catch(error){toast(error.message,true)}}
  async function restore(version){if(!activeArtifact||!confirm(`Restore version ${version} as a new current version?`))return;try{activeArtifact=await request(`${api}/threads/${encodeURIComponent(current)}/artifacts/${encodeURIComponent(activeArtifact.id)}/restore`,{method:'POST',body:JSON.stringify({version:Number(version)})});$('auraArtifactContent').value=activeArtifact.content;toast(`Version ${version} restored as new version ${activeArtifact.current_version}.`);await load();await versions()}catch(error){toast(error.message,true)}}
  async function remove(){if(!activeArtifact||!confirm(`Delete Aura Artifact “${activeArtifact.title}” and its version history?`))return;try{await request(`${api}/threads/${encodeURIComponent(current)}/artifacts/${encodeURIComponent(activeArtifact.id)}`,{method:'DELETE'});activeArtifact=null;$('auraArtifactEditor').style.display='none';toast('Aura Artifact deleted.');await load()}catch(error){toast(error.message,true)}}
  button.onclick=async()=>{panel.style.display=panel.style.display==='block'?'none':'block';if(panel.style.display==='block')await load()};$('auraArtifactClose').onclick=()=>panel.style.display='none';$('auraArtifactNew').onclick=()=>{blank();$('auraArtifactKind').disabled=false};$('auraArtifactRefresh').onclick=load;$('auraArtifactSave').onclick=save;$('auraArtifactVersions').onclick=versions;$('auraArtifactDelete').onclick=remove;
  panel.addEventListener('click',event=>{const open=event.target.closest('[data-artifact-open]'),restoreButton=event.target.closest('[data-artifact-restore]');if(open)openArtifact(open.dataset.artifactOpen);if(restoreButton)restore(restoreButton.dataset.artifactRestore)});
})();
"""


@router.get("/aura-intelligence/artifacts-ui.js")
def artifacts_ui_script():
    return Response(content=ARTIFACT_UI_SCRIPT, media_type="application/javascript", headers={"Cache-Control": "no-store"})


class AuraArtifactsUIMiddleware(BaseHTTPMiddleware):
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
        markers = (
            "<script src='/aura-intelligence/artifacts-ui.js'></script>",
            "<script src='/aura-intelligence/tasks-ui.js'></script>",
            "<script src='/aura-intelligence/connectors-ui.js'></script>",
        )
        for marker in markers:
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
        encoded = text.encode("utf-8")
        migrated = Response(content=encoded, status_code=response.status_code, background=response.background)
        raw_headers = [(key, value) for key, value in response.raw_headers if key.lower() != b"content-length"]
        raw_headers.append((b"content-length", str(len(encoded)).encode("ascii")))
        migrated.raw_headers = raw_headers
        return migrated


__all__ = ["router", "AuraArtifactsUIMiddleware"]
