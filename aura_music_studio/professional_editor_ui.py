from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

router = APIRouter(tags=["Professional Creative Editor UI"])


PROFESSIONAL_EDITOR_SCRIPT = r"""
(()=>{
  const $=id=>document.getElementById(id);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=(v,fallback=0)=>{const n=Number(v);return Number.isFinite(n)?n:fallback};
  let editorState=null;
  let selectedSequenceId=null;
  let selectedItemId=null;

  function projectId(){const box=$('projectName');return box?box.value.trim():''}
  function base(){return `/creative/projects/${encodeURIComponent(projectId())}/editor`}
  function apiNotice(message,error=false){
    if(typeof notice==='function')return notice(message,error);
    console[error?'error':'log'](message);
  }
  async function request(path,opt={}){
    const res=await fetch(path,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});
    let body={};try{body=await res.json()}catch(_){}
    if(!res.ok)throw new Error(body.detail||`Request failed (${res.status})`);
    return body;
  }
  function branch(){return editorState?.branch||null}
  function sequences(){return branch()?.sequences||[]}
  function tracks(){return branch()?.tracks||[]}
  function items(){return branch()?.items||[]}
  function selectedSequence(){return sequences().find(v=>v.id===selectedSequenceId)||sequences()[0]||null}
  function selectedItem(){return items().find(v=>v.id===selectedItemId)||null}
  function trackForItem(id){return tracks().find(t=>(t.item_ids||[]).includes(id))||null}
  function itemById(id){return items().find(v=>v.id===id)||null}
  function setEditor(value){
    editorState=value?.editor||value||editorState;
    const sequence=selectedSequence();
    if(!selectedSequenceId&&sequence)selectedSequenceId=sequence.id;
    if(selectedSequenceId&&!sequences().some(v=>v.id===selectedSequenceId))selectedSequenceId=sequences()[0]?.id||null;
    if(selectedItemId&&!items().some(v=>v.id===selectedItemId))selectedItemId=null;
    renderEditor();
  }

  function ensureStyles(){
    if($('professionalEditorStyles'))return;
    const style=document.createElement('style');style.id='professionalEditorStyles';style.textContent=`
#professionalEditor{position:fixed;inset:18px;z-index:120;background:#050711f7;border:1px solid #ffffff26;border-radius:22px;box-shadow:0 24px 90px #000d;display:none;overflow:hidden;color:#fff}
#professionalEditor.open{display:grid;grid-template-rows:auto 1fr auto}.pe-top{display:flex;align-items:center;gap:8px;padding:12px 14px;border-bottom:1px solid #ffffff1d;background:#0b0e19;flex-wrap:wrap}.pe-top .grow{flex:1}.pe-title{font-weight:950}.pe-sub{font-size:.68rem;color:#aeb5ca}.pe-body{display:grid;grid-template-columns:250px minmax(0,1fr) 310px;min-height:0}.pe-side,.pe-inspector{overflow:auto;background:#080a14}.pe-side{border-right:1px solid #ffffff17}.pe-inspector{border-left:1px solid #ffffff17}.pe-section{padding:12px;border-bottom:1px solid #ffffff12}.pe-section h3{font-size:.78rem;text-transform:uppercase;letter-spacing:.08em;color:#f3c76d;margin:0 0 9px}.pe-seq,.pe-track,.pe-itemrow{border:1px solid #ffffff16;border-radius:10px;padding:8px;margin:5px 0;background:#ffffff05;cursor:pointer}.pe-seq.active,.pe-itemrow.active{border-color:#a86cff88;background:#a86cff18}.pe-trackhead{display:flex;align-items:center;gap:6px;font-size:.72rem}.pe-trackhead b{flex:1}.pe-center{min-width:0;min-height:0;display:grid;grid-template-rows:minmax(230px,1fr) auto minmax(180px,.78fr);background:#03050b}.pe-viewer{position:relative;display:grid;place-items:center;overflow:hidden;background:radial-gradient(circle,#182238,#020309 68%)}.pe-viewer img,.pe-viewer video{max-width:92%;max-height:92%;object-fit:contain;border-radius:10px;box-shadow:0 12px 45px #000b}.pe-placeholder{text-align:center;color:#9da5bb;max-width:460px;padding:22px}.pe-toolbar{display:flex;gap:7px;align-items:center;flex-wrap:wrap;padding:8px 10px;border-block:1px solid #ffffff16;background:#0a0c16}.pe-toolbar input{width:90px}.pe-timeline{overflow:auto;padding:12px;background:#050711}.pe-ruler{height:22px;position:relative;margin-left:120px;border-bottom:1px solid #ffffff22;color:#858ca1;font-size:.61rem}.pe-lane{display:grid;grid-template-columns:112px minmax(520px,1fr);gap:8px;min-height:42px;margin:6px 0}.pe-lab{font-size:.68rem;padding:7px;border:1px solid #ffffff12;border-radius:9px;background:#ffffff04;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.pe-lanebody{position:relative;border:1px solid #ffffff12;border-radius:9px;background:repeating-linear-gradient(90deg,#ffffff04 0,#ffffff04 1px,transparent 1px,transparent 10%);overflow:hidden}.pe-clip{position:absolute;top:5px;height:30px;border:1px solid #a86cff88;background:linear-gradient(110deg,#552d83,#1d6680);border-radius:7px;padding:6px 7px;font-size:.62rem;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;cursor:pointer;min-width:10px}.pe-clip.active{outline:2px solid #f3c76d;outline-offset:1px}.pe-field{display:grid;gap:4px;margin:7px 0}.pe-field label{font-size:.64rem;color:#aeb5ca}.pe-field input,.pe-field select,.pe-field textarea{width:100%;border:1px solid #ffffff1d;background:#050711;color:#fff;border-radius:9px;padding:8px}.pe-grid2{display:grid;grid-template-columns:1fr 1fr;gap:7px}.pe-grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:5px}.pe-actions{display:flex;gap:6px;flex-wrap:wrap}.pe-btn{border:1px solid #ffffff1d;background:#ffffff08;color:#fff;padding:7px 9px;border-radius:9px;font-weight:800;font-size:.69rem;cursor:pointer}.pe-btn.gold{background:linear-gradient(110deg,#f5df94,#c89aff);color:#160d20;border:0}.pe-btn.danger{border-color:#ff8e9e55;color:#ffb6c1}.pe-btn:disabled{opacity:.4;cursor:not-allowed}.pe-empty{padding:12px;border:1px dashed #ffffff25;border-radius:10px;color:#949caf;font-size:.7rem;text-align:center}.pe-status{padding:8px 12px;border-top:1px solid #ffffff16;background:#080a14;color:#9fa7bb;font-size:.66rem;display:flex;gap:12px;flex-wrap:wrap}.pe-chip{border:1px solid #ffffff1c;border-radius:999px;padding:3px 6px;font-size:.6rem}.pe-good{color:#78dda7}.pe-pro{color:#d0a7ff}.pe-modalform{display:grid;gap:6px}
@media(max-width:1000px){#professionalEditor{inset:8px}.pe-body{grid-template-columns:190px minmax(0,1fr)}.pe-inspector{position:absolute;right:8px;top:68px;bottom:36px;width:min(330px,92vw);z-index:4;border:1px solid #ffffff22;box-shadow:-15px 0 40px #000c}.pe-inspector.collapsed{display:none}}
@media(max-width:700px){.pe-body{grid-template-columns:1fr}.pe-side{display:none}.pe-center{grid-template-rows:minmax(180px,.8fr) auto minmax(220px,1fr)}.pe-top .pe-sub{display:none}.pe-grid4{grid-template-columns:1fr 1fr}}
`;
    document.head.append(style);
  }

  function ensureEditor(){
    ensureStyles();let root=$('professionalEditor');if(root)return root;
    root=document.createElement('section');root.id='professionalEditor';root.setAttribute('aria-label','Professional Video and Image Editor');
    root.innerHTML=`<div class="pe-top"><div><div class="pe-title">✦ Professional Video / Image Editor</div><div class="pe-sub">Non-destructive timeline, layers, inspector, history and Pro automation.</div></div><div class="grow"></div><button class="pe-btn" data-pe="sync">↻ Sync Creative DNA</button><button class="pe-btn" data-pe="undo">↶ Undo</button><button class="pe-btn" data-pe="redo">↷ Redo</button><button class="pe-btn" data-pe="inspector">Inspector</button><button class="pe-btn" data-pe="close">✕</button></div><div class="pe-body"><aside class="pe-side"><div class="pe-section"><h3>Sequences</h3><div id="peSequences"></div><button class="pe-btn" data-pe="add-sequence">+ Sequence</button></div><div class="pe-section"><h3>Branches · Pro</h3><div id="peBranches"></div><button class="pe-btn" data-pe="add-branch">+ A/B Branch</button></div></aside><main class="pe-center"><div id="peViewer" class="pe-viewer"></div><div class="pe-toolbar"><button class="pe-btn" data-pe="duplicate">Duplicate</button><label>Split <input id="peSplitTime" class="field" type="number" min="0" step="0.01" value="1"></label><button class="pe-btn" data-pe="split">Split</button><label>Slip <input id="peSlipDelta" class="field" type="number" step="0.01" value="0.1"></label><button class="pe-btn" data-pe="slip">Slip</button><button class="pe-btn danger" data-pe="ripple-delete">Ripple delete</button></div><div id="peTimeline" class="pe-timeline"></div></main><aside id="peInspector" class="pe-inspector"><div id="peInspectorBody"></div></aside></div><div id="peStatus" class="pe-status"></div>`;
    document.body.append(root);
    root.addEventListener('click',onClick);
    root.addEventListener('change',event=>{if(event.target.matches('[data-pe-branch-select]'))checkoutBranch(event.target.value)});
    return root;
  }

  function renderSequences(){
    const box=$('peSequences');if(!box)return;
    box.innerHTML=sequences().length?sequences().map(s=>`<div class="pe-seq ${s.id===selectedSequenceId?'active':''}" data-pe-sequence="${esc(s.id)}"><b>${esc(s.name)}</b><div class="pe-sub">${esc(s.kind)} · ${s.width}×${s.height}${s.kind==='video'?` · ${s.fps}fps · ${Number(s.duration).toFixed(2)}s`:''}</div></div>`).join(''):'<div class="pe-empty">No sequence yet.</div>';
  }
  function renderBranches(){
    const box=$('peBranches');if(!box)return;
    const values=editorState?.branches||[];
    box.innerHTML=values.length?`<select class="pe-field" data-pe-branch-select>${values.map(b=>`<option value="${esc(b.id)}" ${b.is_active?'selected':''}>${esc(b.name)}${b.is_active?' · active':''}</option>`).join('')}</select><div class="pe-sub">A/B branches duplicate metadata only, never the media files.</div>`:'<div class="pe-empty">Initialize the editor first.</div>';
  }
  function renderViewer(){
    const box=$('peViewer');if(!box)return;const item=selectedItem();
    if(!item){box.innerHTML='<div class="pe-placeholder"><b>Select a clip or layer.</b><br>Registered Creative Elements preview here. Editor changes remain non-destructive metadata until a compositor/export render is explicitly produced.</div>';return}
    if(!item.source_element_id){box.innerHTML=`<div class="pe-placeholder"><b>${esc(item.name)}</b><br>This item has editable metadata but no registered Creative Element preview source.</div>`;return}
    const src=`/creative/projects/${encodeURIComponent(projectId())}/elements/${encodeURIComponent(item.source_element_id)}/media`;
    if(item.kind==='video_clip')box.innerHTML=`<video controls playsinline preload="metadata" src="${src}"></video>`;
    else if(item.kind==='image_layer')box.innerHTML=`<img src="${src}" alt="${esc(item.name)}">`;
    else box.innerHTML=`<div class="pe-placeholder"><b>${esc(item.name)}</b><br>${esc(item.kind)} is editable on the timeline but has no visual preview surface.</div>`;
  }
  function renderTimeline(){
    const box=$('peTimeline');if(!box)return;const seq=selectedSequence();
    if(!seq){box.innerHTML='<div class="pe-empty">Create or sync a sequence to begin editing.</div>';return}
    const duration=Math.max(.01,num(seq.duration,1));const seqTracks=(seq.track_ids||[]).map(id=>tracks().find(t=>t.id===id)).filter(Boolean);
    const ruler=Array.from({length:11},(_,i)=>`<span style="position:absolute;left:${i*10}%;transform:translateX(-50%)">${(duration*i/10).toFixed(duration<10?1:0)}s</span>`).join('');
    const lanes=seqTracks.map(track=>{const clips=(track.item_ids||[]).map(itemById).filter(Boolean).map(item=>{const left=Math.max(0,Math.min(100,num(item.start)/duration*100));const width=Math.max(.7,Math.min(100-left,num(item.duration,1)/duration*100));return `<button class="pe-clip ${item.id===selectedItemId?'active':''}" style="left:${left}%;width:${width}%" data-pe-item="${esc(item.id)}" title="${esc(item.name)} · ${num(item.start).toFixed(2)}s → ${(num(item.start)+num(item.duration)).toFixed(2)}s">${esc(item.name)}</button>`}).join('');return `<div class="pe-lane"><div class="pe-lab"><b>${esc(track.name)}</b><br>${esc(track.kind)}${track.locked?' · 🔒':''}</div><div class="pe-lanebody">${clips}</div></div>`}).join('');
    box.innerHTML=`<div class="pe-ruler">${ruler}</div>${lanes||'<div class="pe-empty">This sequence has no tracks yet.</div>'}<div class="pe-actions" style="margin-top:8px"><button class="pe-btn" data-pe="add-track">+ Track</button></div>`;
  }
  function input(id,label,value,type='number',step='0.01'){return `<div class="pe-field"><label for="${id}">${label}</label><input id="${id}" type="${type}" step="${step}" value="${esc(value)}"></div>`}
  function renderInspector(){
    const box=$('peInspectorBody');if(!box)return;const item=selectedItem();
    if(!item){box.innerHTML='<div class="pe-section"><h3>Inspector</h3><div class="pe-empty">Select an item to edit its timing, transform, crop, colour and opacity.</div></div>';return}
    const t=item.transform||{},c=item.crop||{},color=item.color||{};
    box.innerHTML=`<div class="pe-section"><h3>Item</h3>${input('peName','Name',item.name,'text')}
      <div class="pe-grid2">${input('peStart','Start',item.start)}${input('peDuration','Duration',item.duration)}</div>
      <div class="pe-grid2">${input('peOpacity','Opacity 0–1',item.opacity)}${input('peSpeed','Speed',item.speed)}</div>
      <div class="pe-actions"><button class="pe-btn gold" data-pe="save-item">Apply changes</button><button class="pe-btn" data-pe="lock-item">${item.locked?'Unlock':'Lock'}</button></div></div>
      <div class="pe-section"><h3>Transform</h3><div class="pe-grid2">${input('peX','X',t.x||0)}${input('peY','Y',t.y||0)}${input('peScaleX','Scale X',t.scale_x??1)}${input('peScaleY','Scale Y',t.scale_y??1)}${input('peRotation','Rotation',t.rotation||0)}</div></div>
      <div class="pe-section"><h3>Crop</h3><div class="pe-grid4">${input('peCropL','L',c.left||0)}${input('peCropT','T',c.top||0)}${input('peCropR','R',c.right||0)}${input('peCropB','B',c.bottom||0)}</div></div>
      <div class="pe-section"><h3>Colour</h3><div class="pe-grid2">${input('peExposure','Exposure',color.exposure||0)}${input('peContrast','Contrast',color.contrast??1)}${input('peSaturation','Saturation',color.saturation??1)}${input('peBrightness','Brightness',color.brightness||0)}</div></div>
      <div class="pe-section"><h3>Pro effects / keyframes</h3><div class="pe-field"><label>Effect</label><select id="peEffectType"><option>blur</option><option>sharpen</option><option>glow</option><option>vignette</option><option>chroma_key</option><option>denoise</option><option>lut</option></select></div><button class="pe-btn" data-pe="add-effect">Add effect</button><div class="pe-grid2" style="margin-top:7px">${input('peKeyframeParameter','Parameter','transform.x','text')}${input('peKeyframeTime','Time',item.start)}</div>${input('peKeyframeValue','Value',t.x||0)}<button class="pe-btn" data-pe="add-keyframe">Set keyframe</button><div class="pe-sub" style="margin-top:7px">Advanced effects, masks, keyframes and A/B branches are server-gated to Pro.</div></div>`;
  }
  function renderStatus(){
    const box=$('peStatus');if(!box)return;const b=branch();
    box.innerHTML=`<span class="pe-chip pe-good">Non-destructive</span><span>Source media mutated: <b>${editorState?.source_media_mutated?'YES':'No'}</b></span><span>Undo ${b?.undo_stack?.length||0}</span><span>Redo ${b?.redo_stack?.length||0}</span><span>Sequences ${sequences().length}</span><span>Tracks ${tracks().length}</span><span>Items ${items().length}</span><span class="pe-pro">Pro: automation · effects · branches</span>`;
  }
  function renderEditor(){if(!$('professionalEditor'))return;renderSequences();renderBranches();renderViewer();renderTimeline();renderInspector();renderStatus()}

  async function openEditor(){
    if(!projectId()){apiNotice('Load or initialize a Creative House project first.',true);return}
    ensureEditor().classList.add('open');
    try{const data=await request(base());setEditor(data)}catch(error){
      if(String(error.message).toLowerCase().includes('not initialized')){try{const data=await request(`${base()}/initialize`,{method:'POST',body:JSON.stringify({sync_creative_manifest:true})});setEditor(data.editor);apiNotice('Professional editor initialized from the active Creative DNA.')}catch(inner){apiNotice(inner.message,true)}}else apiNotice(error.message,true)
    }
  }
  function closeEditor(){ensureEditor().classList.remove('open')}
  async function sync(){try{const data=await request(`${base()}/sync-manifest`,{method:'POST',body:'{}'});setEditor(data.editor);apiNotice(`Creative DNA synced: ${data.sync?.imported||0} new media element(s).`)}catch(e){apiNotice(e.message,true)}}
  async function undo(){try{setEditor((await request(`${base()}/undo`,{method:'POST',body:'{}'})).editor)}catch(e){apiNotice(e.message,true)}}
  async function redo(){try{setEditor((await request(`${base()}/redo`,{method:'POST',body:'{}'})).editor)}catch(e){apiNotice(e.message,true)}}
  async function saveItem(){const item=selectedItem();if(!item)return;const changes={name:$('peName').value,start:num($('peStart').value),duration:num($('peDuration').value,1),opacity:num($('peOpacity').value,1),speed:num($('peSpeed').value,1),transform:{x:num($('peX').value),y:num($('peY').value),scale_x:num($('peScaleX').value,1),scale_y:num($('peScaleY').value,1),rotation:num($('peRotation').value)},crop:{left:num($('peCropL').value),top:num($('peCropT').value),right:num($('peCropR').value),bottom:num($('peCropB').value)},color:{exposure:num($('peExposure').value),contrast:num($('peContrast').value,1),saturation:num($('peSaturation').value,1),brightness:num($('peBrightness').value)}};try{setEditor((await request(`${base()}/items/${encodeURIComponent(item.id)}`,{method:'PATCH',body:JSON.stringify({changes})})).editor);apiNotice('Item changes applied non-destructively.')}catch(e){apiNotice(e.message,true)}}
  async function lockItem(){const item=selectedItem();if(!item)return;try{setEditor((await request(`${base()}/items/${encodeURIComponent(item.id)}`,{method:'PATCH',body:JSON.stringify({changes:{locked:!item.locked}})})).editor)}catch(e){apiNotice(e.message,true)}}
  async function split(){const item=selectedItem();if(!item)return;try{setEditor((await request(`${base()}/items/${encodeURIComponent(item.id)}/split`,{method:'POST',body:JSON.stringify({time:num($('peSplitTime').value)})})).editor)}catch(e){apiNotice(e.message,true)}}
  async function slip(){const item=selectedItem();if(!item)return;try{setEditor((await request(`${base()}/items/${encodeURIComponent(item.id)}/slip`,{method:'POST',body:JSON.stringify({delta:num($('peSlipDelta').value)})})).editor)}catch(e){apiNotice(e.message,true)}}
  async function duplicate(){const item=selectedItem();if(!item)return;try{const data=await request(`${base()}/items/${encodeURIComponent(item.id)}/duplicate`,{method:'POST',body:JSON.stringify({start:null})});selectedItemId=data.item?.id||selectedItemId;setEditor(data.editor)}catch(e){apiNotice(e.message,true)}}
  async function rippleDelete(){const item=selectedItem();if(!item||!confirm(`Ripple delete “${item.name}” and close the gap? Source media will not be deleted.`))return;try{selectedItemId=null;setEditor((await request(`${base()}/items/${encodeURIComponent(item.id)}/ripple-delete`,{method:'POST',body:'{}'})).editor)}catch(e){apiNotice(e.message,true)}}
  async function addEffect(){const item=selectedItem();if(!item)return;try{setEditor((await request(`${base()}/item/${encodeURIComponent(item.id)}/effects`,{method:'POST',body:JSON.stringify({type:$('peEffectType').value,enabled:true,mix:1,parameters:{},keyframes:{},metadata:{source:'professional_editor_ui'}})})).editor)}catch(e){apiNotice(e.message,true)}}
  async function addKeyframe(){const item=selectedItem();if(!item)return;const parameter=$('peKeyframeParameter').value.trim();try{setEditor((await request(`${base()}/items/${encodeURIComponent(item.id)}/keyframes`,{method:'POST',body:JSON.stringify({parameter,keyframes:[{time:num($('peKeyframeTime').value),value:num($('peKeyframeValue').value),interpolation:'smooth'}]})})).editor)}catch(e){apiNotice(e.message,true)}}
  async function addBranch(){const name=prompt('Branch name, e.g. Vertical Cut');if(!name?.trim())return;try{setEditor((await request(`${base()}/branches`,{method:'POST',body:JSON.stringify({name:name.trim()})})).editor)}catch(e){apiNotice(e.message,true)}}
  async function checkoutBranch(id){if(!id)return;try{setEditor((await request(`${base()}/branches/${encodeURIComponent(id)}/checkout`,{method:'POST',body:'{}'})).editor);selectedItemId=null}catch(e){apiNotice(e.message,true)}}
  async function addSequence(){const kind=(prompt('Sequence kind: video or image','video')||'').trim().toLowerCase();if(!['video','image'].includes(kind))return apiNotice('Sequence kind must be video or image.',true);const name=(prompt('Sequence name',kind==='video'?'Main Video':'Artwork')||'').trim();if(!name)return;const body={kind,name,width:kind==='video'?1920:2048,height:kind==='video'?1080:2048,fps:kind==='video'?24:1,duration:kind==='video'?30:1};try{const data=await request(`${base()}/sequences`,{method:'POST',body:JSON.stringify(body)});selectedSequenceId=data.sequence.id;setEditor(data.editor)}catch(e){apiNotice(e.message,true)}}
  async function addTrack(){const seq=selectedSequence();if(!seq)return;const kind=(prompt('Track kind: video, audio, image, text or adjustment',seq.kind==='image'?'image':'video')||'').trim().toLowerCase();if(!['video','audio','image','text','adjustment','group'].includes(kind))return apiNotice('Unsupported track kind.',true);const name=(prompt('Track name',`${kind[0].toUpperCase()+kind.slice(1)} Track`)||'').trim();if(!name)return;try{setEditor((await request(`${base()}/sequences/${encodeURIComponent(seq.id)}/tracks`,{method:'POST',body:JSON.stringify({kind,name,role:kind})})).editor)}catch(e){apiNotice(e.message,true)}}

  function onClick(event){
    const seq=event.target.closest('[data-pe-sequence]');if(seq){selectedSequenceId=seq.dataset.peSequence;selectedItemId=null;renderEditor();return}
    const item=event.target.closest('[data-pe-item]');if(item){selectedItemId=item.dataset.peItem;const value=itemById(selectedItemId);if(value)$('peSplitTime').value=(num(value.start)+num(value.duration)/2).toFixed(2);renderEditor();return}
    const action=event.target.closest('[data-pe]')?.dataset.pe;if(!action)return;
    ({close:closeEditor,sync,undo,redo,'save-item':saveItem,'lock-item':lockItem,split,slip,duplicate,'ripple-delete':rippleDelete,'add-effect':addEffect,'add-keyframe':addKeyframe,'add-branch':addBranch,'add-sequence':addSequence,'add-track':addTrack,inspector:()=>{$('peInspector')?.classList.toggle('collapsed')}}[action]||(()=>{}))();
  }

  function installButton(){
    const bar=document.querySelector('.rendererbar');if(!bar||$('professionalEditorButton'))return;
    const button=document.createElement('button');button.id='professionalEditorButton';button.className='btn small';button.textContent='✂ Professional Editor';button.title='Open the non-destructive Video/Image timeline and layer editor';button.onclick=openEditor;bar.append(button);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',installButton);else installButton();
  window.ProfessionalCreativeEditor={open:openEditor,close:closeEditor,refresh:async()=>{if(projectId())try{setEditor(await request(base()))}catch(_){}}};
})();
"""


@router.get("/creative/professional-editor-ui.js", include_in_schema=False)
def professional_editor_ui_script():
    return Response(
        content=PROFESSIONAL_EDITOR_SCRIPT,
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


class ProfessionalEditorUIMiddleware(BaseHTTPMiddleware):
    """Attach the professional editor client only to the authenticated Creative House page."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.method.upper() != "GET" or request.url.path != "/creative-house":
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
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                background=response.background,
            )
        marker = "<script src='/creative/professional-editor-ui.js'></script>"
        if marker not in text:
            text = text.replace("</body>", marker + "</body>")
        encoded = text.encode("utf-8")
        migrated = Response(content=encoded, status_code=response.status_code, background=response.background)
        raw_headers = [(key, value) for key, value in response.raw_headers if key.lower() != b"content-length"]
        raw_headers.append((b"content-length", str(len(encoded)).encode("ascii")))
        migrated.raw_headers = raw_headers
        return migrated


__all__ = ["router", "ProfessionalEditorUIMiddleware", "PROFESSIONAL_EDITOR_SCRIPT"]
