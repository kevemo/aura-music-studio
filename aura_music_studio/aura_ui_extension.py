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

  const top=document.querySelector('.top');const projectSelect=document.getElementById('project');
  let reasoningSelect=null;
  if(top&&projectSelect&&!document.getElementById('auraReasoningMode')){
    reasoningSelect=document.createElement('select');reasoningSelect.id='auraReasoningMode';reasoningSelect.className='select';reasoningSelect.title='Aura reasoning mode';reasoningSelect.innerHTML='<option value="fast">⚡ Fast</option><option value="auto">✦ Auto</option><option value="deep">◈ Deep</option><option value="creative">✧ Creative</option>';top.insertBefore(reasoningSelect,projectSelect);
    reasoningSelect.addEventListener('change',async()=>{try{if(typeof current==='undefined'||!current)return;const result=await request(`${api}/threads/${encodeURIComponent(current)}/reasoning-mode`,{method:'PUT',body:JSON.stringify({mode:reasoningSelect.value})});toast(result.detail||`Aura ${reasoningSelect.value} mode active.`)}catch(error){toast(error.message,true)}});
    window.__auraSyncReasoningMode=async function(){try{if(typeof current==='undefined'||!current)return;const result=await request(`${api}/threads/${encodeURIComponent(current)}/reasoning-mode`);reasoningSelect.value=result.mode||'auto'}catch(_){reasoningSelect.value='auto'}};
  }

  // Custom-GPT-style private Aura Profiles. Profiles personalize expertise/workflow only;
  // the server keeps them subordinate to Aura Core access, rights and safety rules.
  let auraProfiles=[];
  let profileSelect=null;
  async function loadProfiles(){
    try{auraProfiles=await request(`${api}/profiles`)}catch(_){auraProfiles=[]}
    if(profileSelect){const old=profileSelect.value;profileSelect.innerHTML='<option value="">Aura · Default profile</option>'+auraProfiles.map(p=>`<option value="${esc(p.id)}">Aura · ${esc(p.name)}</option>`).join('');if(auraProfiles.some(p=>p.id===old))profileSelect.value=old}
    renderProfileList();
  }
  async function syncProfile(){
    if(!profileSelect||typeof current==='undefined'||!current)return;
    try{const result=await request(`${api}/threads/${encodeURIComponent(current)}/profile`);profileSelect.value=result.profile?.id||''}catch(_){profileSelect.value=''}
  }
  if(top&&projectSelect&&!document.getElementById('auraProfileSelect')){
    profileSelect=document.createElement('select');profileSelect.id='auraProfileSelect';profileSelect.className='select';profileSelect.title='Private Aura Profile';profileSelect.innerHTML='<option value="">Aura · Default profile</option>';top.insertBefore(profileSelect,reasoningSelect||projectSelect);
    profileSelect.addEventListener('change',async()=>{
      try{
        if(typeof current==='undefined'||!current)return;
        const result=await request(`${api}/threads/${encodeURIComponent(current)}/profile`,{method:'PUT',body:JSON.stringify({profile_id:profileSelect.value||null,apply_default_mode:true})});
        toast(result.detail||'Aura Profile updated.');if(window.__auraSyncReasoningMode)await window.__auraSyncReasoningMode();
      }catch(error){toast(error.message,true);await syncProfile()}
    });
  }

  const foot=document.querySelector('.sideFoot');
  let profilePanel=null,editingProfileId=null;
  function profilePanelHTML(){return `<div style="display:flex;justify-content:space-between;gap:10px"><b>Aura Profile Studio</b><button id="auraCloseProfiles" class="mini">✕</button></div><p style="color:#a9b2c8">Create private specialist versions of Aura. Profiles cannot grant access or bypass safety/rights.</p><input id="auraProfileName" class="search" maxlength="100" placeholder="Profile name · e.g. Studio Producer"><input id="auraProfileDescription" class="search" maxlength="1000" placeholder="Short description"><select id="auraProfileMode" class="select" style="margin-top:8px"><option value="auto">✦ Auto default</option><option value="fast">⚡ Fast default</option><option value="deep">◈ Deep default</option><option value="creative">✧ Creative default</option></select><textarea id="auraProfileInstructions" class="search" style="min-height:160px;resize:vertical" maxlength="8000" placeholder="How should this Aura profile work? Expertise, response style, workflow priorities, creative direction…"></textarea><div style="display:flex;gap:7px;margin-top:8px"><button id="auraSaveProfile" class="btn primary">Save profile</button><button id="auraCancelProfileEdit" class="btn">Clear form</button></div><div id="auraProfileList" style="margin-top:16px"></div>`}
  function clearProfileForm(){editingProfileId=null;for(const id of ['auraProfileName','auraProfileDescription','auraProfileInstructions']){const el=document.getElementById(id);if(el)el.value=''}const mode=document.getElementById('auraProfileMode');if(mode)mode.value='auto';const save=document.getElementById('auraSaveProfile');if(save)save.textContent='Save profile'}
  function renderProfileList(){
    const list=document.getElementById('auraProfileList');if(!list)return;
    list.innerHTML=auraProfiles.length?auraProfiles.map(p=>`<div style="border:1px solid #ffffff18;border-radius:12px;padding:10px;margin:8px 0;background:#ffffff05"><b>${esc(p.name)}</b><div style="color:#a9b2c8;font-size:.78rem">${esc(p.description||'No description')} · ${esc(p.default_mode)} mode</div><div style="margin-top:7px;display:flex;gap:5px;flex-wrap:wrap"><button class="mini" data-profile-use="${esc(p.id)}">Use</button><button class="mini" data-profile-edit="${esc(p.id)}">Edit</button><button class="mini" data-profile-delete="${esc(p.id)}">Delete</button></div></div>`).join(''):'<p style="color:#a9b2c8">No custom profiles yet.</p>';
  }
  if(foot&&!document.getElementById('auraProfileStudio')){
    const profileBtn=document.createElement('button');profileBtn.id='auraProfileStudio';profileBtn.className='btn';profileBtn.textContent='✦ Aura profiles';foot.prepend(profileBtn);
    profilePanel=document.createElement('div');profilePanel.id='auraProfilePanel';profilePanel.style.cssText='position:fixed;right:0;top:0;bottom:0;width:min(500px,100%);z-index:98;background:#080c18fb;border-left:1px solid #ffffff20;padding:18px;overflow:auto;display:none;box-shadow:-20px 0 70px #0009';profilePanel.innerHTML=profilePanelHTML();document.body.append(profilePanel);
    profileBtn.onclick=async()=>{profilePanel.style.display=profilePanel.style.display==='block'?'none':'block';if(profilePanel.style.display==='block')await loadProfiles()};
    document.getElementById('auraCloseProfiles').onclick=()=>profilePanel.style.display='none';
    document.getElementById('auraCancelProfileEdit').onclick=clearProfileForm;
    document.getElementById('auraSaveProfile').onclick=async()=>{
      const name=document.getElementById('auraProfileName').value.trim(),description=document.getElementById('auraProfileDescription').value.trim(),instructions=document.getElementById('auraProfileInstructions').value.trim(),default_mode=document.getElementById('auraProfileMode').value;
      if(!name||!instructions)return toast('Profile name and instructions are required.',true);
      try{
        if(editingProfileId)await request(`${api}/profiles/${encodeURIComponent(editingProfileId)}`,{method:'PATCH',body:JSON.stringify({name,description,instructions,default_mode})});
        else await request(`${api}/profiles`,{method:'POST',body:JSON.stringify({name,description,instructions,default_mode})});
        toast(editingProfileId?'Aura Profile updated.':'Aura Profile created.');clearProfileForm();await loadProfiles();await syncProfile();
      }catch(error){toast(error.message,true)}
    };
    profilePanel.addEventListener('click',async event=>{
      const use=event.target.closest('[data-profile-use]'),edit=event.target.closest('[data-profile-edit]'),del=event.target.closest('[data-profile-delete]');
      const id=use?.dataset.profileUse||edit?.dataset.profileEdit||del?.dataset.profileDelete;if(!id)return;const profile=auraProfiles.find(p=>p.id===id);if(!profile)return;
      if(use){if(typeof current==='undefined'||!current)return toast('Open a conversation first.',true);try{await request(`${api}/threads/${encodeURIComponent(current)}/profile`,{method:'PUT',body:JSON.stringify({profile_id:id,apply_default_mode:true})});profileSelect.value=id;if(window.__auraSyncReasoningMode)await window.__auraSyncReasoningMode();toast(`${profile.name} is now active.`)}catch(error){toast(error.message,true)}return}
      if(edit){editingProfileId=id;document.getElementById('auraProfileName').value=profile.name||'';document.getElementById('auraProfileDescription').value=profile.description||'';document.getElementById('auraProfileInstructions').value=profile.instructions||'';document.getElementById('auraProfileMode').value=profile.default_mode||'auto';document.getElementById('auraSaveProfile').textContent='Update profile';profilePanel.scrollTop=0;return}
      if(del){if(!confirm(`Delete Aura Profile “${profile.name}”?`))return;try{await request(`${api}/profiles/${encodeURIComponent(id)}`,{method:'DELETE'});toast('Aura Profile deleted.');await loadProfiles();await syncProfile()}catch(error){toast(error.message,true)}}
    });
  }

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

  // Keep mode/profile selectors synchronized when the base portal opens another thread.
  if(typeof openThread==='function'){
    const baseOpenThread=openThread;
    openThread=async function(id){const result=await baseOpenThread(id);if(window.__auraSyncReasoningMode)await window.__auraSyncReasoningMode();await syncProfile();return result};
  }
  loadProfiles().then(()=>syncProfile());
  setTimeout(()=>{if(window.__auraSyncReasoningMode)window.__auraSyncReasoningMode()},400);
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
