from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from .esp_social_approval_inbox import router as approval_router
from .esp_social_creative_launch import router as creative_launch_router
from .esp_social_publish_queue_portal import router as publish_queue_portal_router
from .social_management_portal import social_house as base_social_house

router = APIRouter()
router.include_router(approval_router)
router.include_router(creative_launch_router)
router.include_router(publish_queue_portal_router)

# The mature Social Management portal remains the source UI. These overlays add
# runtime-aware publishing, cross-platform composition and Aura growth planning
# without forking the underlying Social House data model.
PLATFORM_AWARE_UI = r"""
<script id="espPlatformAwarePostEditor">
(()=>{
  const endpoint='/command-center/api/social/platforms';let caps=null,loading=null;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pretty=v=>String(v||'').replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
  async function registry(){if(caps)return caps;if(loading)return loading;loading=fetch(endpoint,{credentials:'same-origin'}).then(async r=>{let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Platform registry failed (${r.status})`);caps=b.capabilities||{};return caps}).finally(()=>loading=null);return loading}
  function sync(platform,type,info){const spec=caps?.[platform.value]||{},types=Array.isArray(spec.content_types)?spec.content_types:[],old=type.value;type.innerHTML=types.map(v=>`<option value="${esc(v)}">${esc(pretty(v))}</option>`).join('');if(types.includes(old))type.value=old;const caption=spec.caption_limit==null?'No registry caption limit':`${Number(spec.caption_limit).toLocaleString()} character caption limit`,media=spec.max_media==null?'No registry media limit':`up to ${spec.max_media} media`,implemented=Array.isArray(spec.auto_publish_content_types)?spec.auto_publish_content_types:[],publish=implemented.length?`runtime publishing: ${implemented.map(pretty).join(', ')}`:'planning only in this runtime';info.textContent=`${caption} · ${media} · ${publish}`}
  async function enhance(){const platform=document.getElementById('postPlatform'),type=document.getElementById('postType');if(!platform||!type||platform.dataset.registryReady==='1')return;try{await registry();const keys=Object.keys(caps||{}).filter(k=>caps[k]?.planning!==false);if(!keys.length)return;const selected=caps[platform.value]?platform.value:keys[0];platform.innerHTML=keys.map(v=>`<option value="${esc(v)}">${esc(pretty(v))}</option>`).join('');platform.value=selected;platform.dataset.registryReady='1';let info=document.getElementById('postPlatformRules');if(!info){info=document.createElement('div');info.id='postPlatformRules';info.className='muted';info.style.cssText='font-size:.68rem;line-height:1.4;margin-top:-2px';type.closest('.row')?.insertAdjacentElement('afterend',info)}const update=()=>sync(platform,type,info);platform.addEventListener('change',update);update()}catch(error){console.warn('ESP platform-aware planner unavailable',error)}}
  const observer=new MutationObserver(enhance);observer.observe(document.documentElement,{subtree:true,childList:true});enhance();
})();
</script>
"""

MULTI_PLATFORM_UI = r"""
<script id="espMultiPlatformComposer">
(()=>{
  const root='/command-center/api/social';let registry=null,drawer=null;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pretty=v=>String(v||'').replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
  async function api(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b}
  function toast(m,bad=false){try{notice(m,bad)}catch(_){console[bad?'error':'log'](m)}}
  function current(){try{return typeof house!=='undefined'?house:null}catch(_){return null}}
  async function capabilities(){if(registry)return registry;registry=(await api(root+'/platforms')).capabilities||{};return registry}
  function ensureButton(){const tabs=document.querySelector('.tabs');if(!tabs||document.getElementById('espMultiPlatformButton'))return;const b=document.createElement('button');b.id='espMultiPlatformButton';b.className='tab';b.textContent='＋ Multi-platform Post';b.onclick=open;tabs.append(b)}
  function card(name,spec){const types=Array.isArray(spec.content_types)?spec.content_types:[];return `<div class="card" style="margin:8px 0"><label style="display:flex;gap:8px;align-items:center"><input type="checkbox" data-multi-enable="${esc(name)}"><b>${esc(pretty(name))}</b></label><div id="multi_${esc(name)}" style="display:none;margin-top:9px" class="stack"><select class="field" data-multi-type="${esc(name)}">${types.map(t=>`<option value="${esc(t)}">${esc(pretty(t))}</option>`).join('')}</select><textarea class="field" data-multi-caption="${esc(name)}" placeholder="${esc(pretty(name))} caption"></textarea><input class="field" data-multi-tags="${esc(name)}" placeholder="Hashtags, separated by spaces or commas"><div class="row"><input class="field" type="datetime-local" data-multi-schedule="${esc(name)}" aria-label="Schedule"><input class="field" data-multi-zone="${esc(name)}" value="${esc(Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC')}" aria-label="Timezone"></div></div></div>`}
  function ensureDrawer(){if(drawer)return drawer;drawer=document.createElement('aside');drawer.id='espMultiPlatformDrawer';drawer.style.cssText='position:fixed;right:0;top:0;bottom:0;width:min(760px,100%);z-index:99;transform:translateX(105%);transition:.2s;background:#080b16fc;border-left:1px solid #ffffff20;padding:18px;overflow:auto;box-shadow:-24px 0 70px #000a';drawer.innerHTML=`<div style="display:flex;gap:9px"><div style="flex:1"><div class="eyebrow">Cross-platform planning</div><h2>Multi-platform Post</h2><p class="muted">One content item, independently validated per destination. Provider-authorised publishing remains fail-closed until a supported connection is ready.</p></div><button class="btn small" id="espMultiClose">✕</button></div><div class="formbox"><div class="stack"><input id="espMultiTitle" class="field" maxlength="300" placeholder="Content title"><label class="muted"><input id="espMultiApproval" type="checkbox"> Require approval before publishing</label><div id="espMultiPlatforms"></div><button id="espMultiSave" class="btn primary">Add multi-platform content</button><div class="muted" style="font-size:.68rem">This composer saves <code>auto_publish:false</code>. Provider-authorised publishing is enabled separately only for runtime-supported surfaces.</div></div></div>`;document.body.append(drawer);document.getElementById('espMultiClose').onclick=()=>drawer.style.transform='translateX(105%)';document.getElementById('espMultiSave').onclick=save;drawer.addEventListener('change',e=>{const cb=e.target.closest('[data-multi-enable]');if(cb){const row=document.getElementById(`multi_${cb.dataset.multiEnable}`);if(row)row.style.display=cb.checked?'grid':'none'}});return drawer}
  async function open(){const h=current();if(!h?.id)return toast('Choose or create a Social House first.',true);try{await capabilities();ensureDrawer();document.getElementById('espMultiPlatforms').innerHTML=Object.entries(registry).filter(([,s])=>s.planning!==false).map(([n,s])=>card(n,s)).join('');document.getElementById('espMultiTitle').value='';document.getElementById('espMultiApproval').checked=false;drawer.style.transform='translateX(0)'}catch(e){toast(e.message,true)}}
  const tags=v=>String(v||'').split(/[\s,]+/).map(x=>x.trim().replace(/^#+/,'')).filter(Boolean).slice(0,100);
  const scheduled=input=>{if(!input?.value)return null;const d=new Date(input.value);return Number.isNaN(d.getTime())?null:d.toISOString()};
  async function save(){const h=current(),title=document.getElementById('espMultiTitle')?.value.trim();if(!h?.id)return toast('Choose a Social House first.',true);if(!title)return toast('Enter a content title.',true);const selected=[...document.querySelectorAll('[data-multi-enable]:checked')].map(x=>x.dataset.multiEnable);if(!selected.length)return toast('Select at least one platform.',true);const variants=selected.map(name=>({platform:name,content_type:document.querySelector(`[data-multi-type="${CSS.escape(name)}"]`).value,caption:document.querySelector(`[data-multi-caption="${CSS.escape(name)}"]`).value.trim(),hashtags:tags(document.querySelector(`[data-multi-tags="${CSS.escape(name)}"]`).value),scheduled_at:scheduled(document.querySelector(`[data-multi-schedule="${CSS.escape(name)}"]`)),timezone:document.querySelector(`[data-multi-zone="${CSS.escape(name)}"]`).value.trim()||null,media_refs:[],auto_publish:false})),approval=document.getElementById('espMultiApproval').checked,anyScheduled=variants.some(v=>v.scheduled_at),status=approval?'pending_approval':(anyScheduled?'scheduled':'draft');try{const d=await api(`${root}/spaces/${encodeURIComponent(h.id)}/content`,{method:'POST',body:JSON.stringify({title,status,approval_required:approval,content_pillars:[],variants})});try{house=d.house;renderSpaces();renderTab()}catch(_){}drawer.style.transform='translateX(105%)';toast(`${variants.length} platform variant${variants.length===1?'':'s'} added.`)}catch(e){toast(e.message,true)}}
  const observer=new MutationObserver(ensureButton);observer.observe(document.documentElement,{subtree:true,childList:true});ensureButton();
})();
</script>
"""

NICHE_COACH_UI = r"""
<script id="espAuraNicheCoach">
(()=>{
  const social='/command-center/api/social',intelligence='/command-center/api/social-intelligence';
  async function api(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b}
  function toast(m,bad=false){try{notice(m,bad)}catch(_){console[bad?'error':'log'](m)}}
  function current(){try{return typeof house!=='undefined'?house:null}catch(_){return null}}
  function title(){return `Aura Niche Growth Sprint · ${new Date().toISOString().slice(0,10)}`}
  const clean=v=>String(v||'').trim().replace(/\s+/g,' ').slice(0,300);
  async function build(){const h=current();if(!h?.id)return toast('Choose or create a Social House first.',true);const button=document.getElementById('auraNicheCoachButton');if(button){button.disabled=true;button.textContent='Building growth plan…'}try{const insight=await api(`${intelligence}/spaces/${encodeURIComponent(h.id)}/aura-insights`),recommendations=[...new Set((Array.isArray(insight.recommendations)?insight.recommendations:[]).map(clean).filter(Boolean))].slice(0,8);if(!recommendations.length)throw new Error('Aura could not derive a safe niche growth action from the current profile and analytics.');const projectName=title(),existingProject=(h.projects||[]).find(p=>p.name===projectName),description=['Aura-generated ESP niche growth sprint based on the member’s selected niche, ESP training priorities and authorised social analytics.','',...recommendations.map((item,index)=>`${index+1}. ${item}`)].join('\n').slice(0,2000),project=existingProject||(await api(`${social}/spaces/${encodeURIComponent(h.id)}/projects`,{method:'POST',body:JSON.stringify({name:projectName,description,tags:['aura-plan','niche-growth']})})).project;if(!project?.id)throw new Error('Aura could not persist the growth campaign.');const existing=new Set((h.tasks||[]).filter(t=>t.project_id===project.id).map(t=>clean(t.title)));let created=0;for(const recommendation of recommendations){if(existing.has(recommendation))continue;await api(`${social}/spaces/${encodeURIComponent(h.id)}/tasks`,{method:'POST',body:JSON.stringify({title:recommendation,description:'Aura action generated from this ESP niche profile and authorised social-performance context.',status:'todo',priority:'normal',project_id:project.id,tags:['aura','niche-growth']})});created++}try{await loadHouse(h.id)}catch(_){}toast(created?`Aura added ${created} actionable growth task${created===1?'':'s'} to “${project.name}”.`:`“${project.name}” is already up to date; no duplicate tasks were created.`);try{showTab('campaigns',[...document.querySelectorAll('.tab')].find(x=>x.textContent.trim()==='Campaigns'))}catch(_){}}catch(e){toast(e.message||'Aura growth planning failed.',true)}finally{if(button){button.disabled=false;button.textContent='Build Aura growth plan'}}}
  window.runAuraNicheCoach=build;
})();
</script>
"""

TRUTH_REPLACEMENTS = {
    "This truthful calendar is the surface the external Calendar interfaces and later provider-authorised publishing adapters build on.":
        "This calendar reflects saved content variants and schedules. Provider-authorised publishing is enabled only for runtime-supported surfaces with an authorised connection.",
    "Aura will be able to layer recommendations on this structure without bypassing approvals.":
        "Aura growth planning writes recommendations into this campaign structure while preserving approval and publishing controls.",
    "The API already supports task creation/update; this view exposes the operational foundation.":
        "Tasks are persisted operational work items. Aura growth planning can create them automatically, and the Social API supports their ongoing status updates.",
    "Roadmap layer: future approval-link workflows still remain approval-state based.":
        "Approval state is enforced before publishing; Approve and Reject actions are available in the private Approval Inbox.",
    "Future authorised Social Inbox (comments/DMs via platform APIs only after permission)":
        "Provider-limited comments and messages are available only where the platform API and granted permissions expose them; unsupported channels remain unavailable rather than simulated.",
    "Powered by Aura AI Systems": "Powered by Aura AI",
}


@router.get("/command-center/social", response_class=HTMLResponse, include_in_schema=False)
def social_house_with_intelligence(request: Request):
    """Serve the mature Social workspace with fully wired Chat Two enhancements."""
    response = base_social_house(request)
    if not isinstance(response, Response) or not getattr(response, "body", None):
        return response
    try:
        html = response.body.decode("utf-8")
    except Exception:
        return response

    marker = "<a class='btn optional' href='/command-center/niche'>Change Niche</a>"
    if marker in html:
        missing = []
        for path, label, css in (
            ("/command-center/social-insights", "Analytics & Aura Insights", "btn primary"),
            ("/command-center/social/connections", "Connections", "btn"),
            ("/command-center/social/creative-launch", "Creative → Social Launch", "btn"),
            ("/command-center/social/approvals", "Approval Inbox", "btn"),
            ("/command-center/social/publish-queue", "Publish Queue", "btn"),
        ):
            if path not in html:
                missing.append(f"<a class='{css}' href='{path}'>{label}</a>")
        if missing:
            html = html.replace(marker, marker + "".join(missing), 1)

    old_placeholder = (
        "<button class=\"btn\" onclick=\"notice('Aura niche campaign generation is in the next integration stage.')\">"
        "Plan niche campaign</button>"
    )
    live_coach = (
        "<button class=\"btn\" id=\"auraNicheCoachButton\" onclick=\"runAuraNicheCoach()\">"
        "Build Aura growth plan</button>"
    )
    html = html.replace(old_placeholder, live_coach, 1)
    for old, new in TRUTH_REPLACEMENTS.items():
        html = html.replace(old, new)

    additions = ""
    if 'id="espPlatformAwarePostEditor"' not in html:
        additions += PLATFORM_AWARE_UI
    if 'id="espMultiPlatformComposer"' not in html:
        additions += MULTI_PLATFORM_UI
    if 'id="espAuraNicheCoach"' not in html:
        additions += NICHE_COACH_UI
    if additions and "</body>" in html:
        html = html.replace("</body>", additions + "</body>", 1)
    return HTMLResponse(html, status_code=response.status_code)


__all__ = [
    "router",
    "PLATFORM_AWARE_UI",
    "MULTI_PLATFORM_UI",
    "NICHE_COACH_UI",
    "TRUTH_REPLACEMENTS",
]
