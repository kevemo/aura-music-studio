from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_niche import require_esp_hub_member
from .shared_sky_control_room import (
    StudioConflict,
    StudioInvariantError,
    normalize_transform,
    studio_repo,
    utc_now,
    validate_no_secrets,
)
from .shared_sky_streaming_studios import SourceCreate, SourceUpdate, shared_sky

router = APIRouter(tags=["Shared Sky Professional Canvas"])


class ProfilePatch(BaseModel):
    profile_key: Literal["landscape-1080", "portrait-1080", "square-1080"]
    expected_version: int = Field(ge=1)


class StudioSourceCreate(BaseModel):
    source_type: Literal[
        "camera", "microphone", "screen", "text", "image", "video", "audio", "shape", "gradient"
    ]
    name: str = Field(default="Studio Source", min_length=1, max_length=120)
    visible: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class StudioSourceDelete(BaseModel):
    expected_session_version: int = Field(ge=1)


class SourceZPatch(BaseModel):
    z_index: int = Field(ge=-10000, le=10000)
    expected_session_version: int = Field(ge=1)


def _member(request: Request):
    return require_esp_hub_member(request)


def _raise(exc: Exception) -> None:
    if isinstance(exc, KeyError):
        raise HTTPException(404, "Shared Sky studio resource not found") from exc
    if isinstance(exc, StudioConflict):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, StudioInvariantError):
        raise HTTPException(400, str(exc)) from exc
    raise HTTPException(500, "Shared Sky professional canvas operation failed") from exc


def update_profile(user_id: str, session_id: str, body: ProfilePatch) -> dict[str, Any]:
    current = studio_repo.get_session(user_id, session_id)
    return studio_repo._mutate(
        user_id,
        session_id,
        body.expected_version,
        {
            "profile_key": body.profile_key,
            "autosave_state": {
                "reason": "profile_change",
                "profile_key": body.profile_key,
                "saved_at": utc_now(),
            },
        },
    )


def create_studio_source(user_id: str, session_id: str, body: StudioSourceCreate) -> dict[str, Any]:
    current = studio_repo.get_session(user_id, session_id)
    if current["transition_state"] != "idle":
        raise StudioConflict("Source changes are locked while a transition is in progress")
    scene_id = current.get("preview_scene_id")
    if not scene_id:
        raise StudioInvariantError("Select a Preview scene before adding a source")
    config = dict(body.config)
    config.setdefault("privacy", "programme_safe")
    config["transform"] = normalize_transform(config.get("transform"))
    if body.source_type in {"camera", "microphone", "screen"}:
        config["browser_capture"] = True
        config["capture_state"] = "detached"
    validate_no_secrets(config)
    existing = shared_sky.scene(user_id, scene_id).get("sources", [])
    z_index = max([int(row.get("z_index", 0)) for row in existing] + [-1]) + 1
    source = shared_sky.create_source(
        user_id,
        scene_id,
        SourceCreate(
            source_type=body.source_type,
            name=body.name,
            config=config,
            visible=body.visible,
            locked=False,
            z_index=z_index,
        ),
    )
    session = studio_repo.set_autosave_state(
        user_id,
        session_id,
        current["version"],
        {"reason": "source_created", "source_id": source["id"], "saved_at": utc_now()},
    )
    return {"source": source, "session": session, "programme_unchanged": True}


def delete_studio_source(
    user_id: str, session_id: str, source_id: str, expected_version: int
) -> dict[str, Any]:
    current = studio_repo.get_session(user_id, session_id)
    if current["version"] != expected_version:
        raise StudioConflict("Studio state changed in another tab/operator")
    if current["transition_state"] != "idle":
        raise StudioConflict("Source changes are locked while a transition is in progress")
    source = shared_sky.source(user_id, source_id)
    if source.get("project_id") != current["project_id"]:
        raise StudioInvariantError("Source does not belong to this studio project")
    if source.get("scene_id") != current.get("preview_scene_id"):
        raise StudioInvariantError("Only a source in the current Preview scene can be removed here")
    shared_sky.delete_source(user_id, source_id)
    session = studio_repo.set_autosave_state(
        user_id,
        session_id,
        current["version"],
        {"reason": "source_deleted", "source_id": source_id, "saved_at": utc_now()},
    )
    return {"deleted": True, "source_id": source_id, "session": session, "programme_unchanged": True}


def patch_source_z(
    user_id: str, session_id: str, source_id: str, body: SourceZPatch
) -> dict[str, Any]:
    current = studio_repo.get_session(user_id, session_id)
    if current["version"] != body.expected_session_version:
        raise StudioConflict("Studio state changed in another tab/operator")
    source = shared_sky.source(user_id, source_id)
    if source.get("project_id") != current["project_id"]:
        raise StudioInvariantError("Source does not belong to this studio project")
    updated = shared_sky.update_source(user_id, source_id, SourceUpdate(z_index=body.z_index))
    session = studio_repo.set_autosave_state(
        user_id,
        session_id,
        current["version"],
        {"reason": "source_reordered", "source_id": source_id, "saved_at": utc_now()},
    )
    return {"source": updated, "session": session, "programme_unchanged": True}


@router.patch("/shared-sky/studio/api/sessions/{session_id}/profile")
def patch_profile(session_id: str, body: ProfilePatch, request: Request):
    member, _ = _member(request)
    try:
        return {"session": update_profile(member.user_id, session_id, body)}
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/sessions/{session_id}/sources")
def add_studio_source(session_id: str, body: StudioSourceCreate, request: Request):
    member, _ = _member(request)
    try:
        return create_studio_source(member.user_id, session_id, body)
    except Exception as exc:
        _raise(exc)


@router.delete("/shared-sky/studio/api/sessions/{session_id}/sources/{source_id}")
def remove_studio_source(
    session_id: str, source_id: str, body: StudioSourceDelete, request: Request
):
    member, _ = _member(request)
    try:
        return delete_studio_source(
            member.user_id, session_id, source_id, body.expected_session_version
        )
    except Exception as exc:
        _raise(exc)


@router.patch("/shared-sky/studio/api/sessions/{session_id}/sources/{source_id}/z")
def reorder_studio_source(
    session_id: str, source_id: str, body: SourceZPatch, request: Request
):
    member, _ = _member(request)
    try:
        return patch_source_z(member.user_id, session_id, source_id, body)
    except Exception as exc:
        _raise(exc)


PRO_CSS = """
:root{--bg:#041016;--panel:#0a1c25;--line:#ffffff25;--text:#f4fbff;--muted:#9eb4bf;--aqua:#52e6d1;--red:#ff687d;--amber:#f6cf67;--green:#78e8a0}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,system-ui,sans-serif}
.app{height:100vh;display:grid;grid-template-columns:245px 1fr 310px;grid-template-rows:62px minmax(0,1fr) 220px;gap:8px;padding:8px}
.panel,.top,.monitor{background:var(--panel);border:1px solid var(--line);border-radius:12px}.top{grid-column:1/-1;display:flex;align-items:center;gap:9px;padding:8px 12px;overflow:auto}
.left{grid-column:1;grid-row:2;padding:10px;overflow:auto}.center{grid-column:2;grid-row:2;min-width:0;display:grid;grid-template-rows:minmax(0,1fr) auto;gap:8px}.right{grid-column:3;grid-row:2;padding:10px;overflow:auto}.bottom{grid-column:1/-1;grid-row:3;padding:10px;overflow:auto}
.monitors{display:grid;grid-template-columns:1fr 1fr;gap:8px;min-height:0}.monitor{position:relative;overflow:hidden;min-height:220px}.monitor.preview{outline:2px solid var(--green)}.monitor.programme{outline:2px solid var(--red)}
.label{position:absolute;z-index:30;left:8px;top:8px;background:#000c;padding:4px 7px;border-radius:6px;font-weight:900;font-size:.72rem}.viewport{position:absolute;inset:0;overflow:hidden;background:#02070a}.canvas{position:absolute;left:50%;top:50%;width:100%;height:100%;transform-origin:center center}
.safe{position:absolute;inset:5%;border:1px dashed #ffffff55;pointer-events:none;z-index:20}.safe.title{inset:10%;border-color:#f6cf6755}.guide-v,.guide-h{position:absolute;background:#52e6d166;pointer-events:none;z-index:21}.guide-v{top:0;bottom:0;left:50%;width:1px}.guide-h{left:0;right:0;top:50%;height:1px}
.source{position:absolute;overflow:visible;background:#15303e;border:1px solid transparent;user-select:none;touch-action:none}.source.selected{border-color:var(--aqua);box-shadow:0 0 0 1px var(--aqua)}.source.locked{border-style:dashed}.source>video,.source>img{width:100%;height:100%;object-fit:cover;overflow:hidden}.placeholder{width:100%;height:100%;display:grid;place-items:center;text-align:center;color:var(--muted);padding:6px;overflow:hidden}.resize,.rotate{position:absolute;width:14px;height:14px;background:var(--aqua);border:2px solid #041016;z-index:40}.resize{right:-7px;bottom:-7px;cursor:nwse-resize}.rotate{right:-7px;top:-7px;border-radius:50%;cursor:grab}
.graphic{width:100%;height:100%;display:flex;flex-direction:column;justify-content:center;overflow:hidden;line-height:1.08}.graphic strong,.graphic span{display:block;white-space:pre-wrap;overflow-wrap:anywhere}.graphic span{font-size:.62em;opacity:.9;margin-top:.18em}
.scene,.source-row{display:block;width:100%;text-align:left;background:#ffffff08;border:1px solid var(--line);color:var(--text);padding:8px;border-radius:9px;margin:5px 0}.scene.active,.source-row.active{border-color:var(--aqua)}button,input,select{font:inherit}button,.btn{background:#102d39;border:1px solid var(--line);color:var(--text);border-radius:8px;padding:7px 10px;font-weight:750}.cut{background:var(--amber);color:#1a1200}.take{background:var(--red);color:#190108}.row{display:flex;gap:6px;align-items:center;flex-wrap:wrap}.muted{color:var(--muted);font-size:.8rem}.status{font-size:.78rem}.field{display:grid;grid-template-columns:82px 1fr;gap:7px;align-items:center;margin:6px 0}.field input,.field select{width:100%;background:#041016;border:1px solid var(--line);color:var(--text);padding:6px;border-radius:7px}.section{border-top:1px solid var(--line);padding-top:9px;margin-top:9px}
.mixer{display:flex;gap:8px;overflow:auto}.channel{min-width:165px;border:1px solid var(--line);border-radius:9px;padding:8px}.meter{height:7px;background:#0008;border-radius:5px;overflow:hidden;margin:6px 0}.meter>i{display:block;width:0;height:100%;background:currentColor}.offline{opacity:.55}.danger{color:#ff9cab}.history-ready{color:var(--green)}:focus-visible{outline:3px solid var(--aqua);outline-offset:2px}
@media(max-width:1050px){.app{grid-template-columns:200px 1fr;grid-template-rows:62px minmax(0,1fr) auto 220px}.right{grid-column:1/-1;grid-row:3}.bottom{grid-row:4}.monitors{grid-template-columns:1fr}}
@media(max-width:700px){.app{height:auto;display:block}.top,.panel,.center{margin-bottom:8px}.monitors{display:block}.monitor{min-height:240px;margin-bottom:8px}.right{display:block}.bottom{min-height:180px}}
"""

PRO_JS = r"""
const qs=new URLSearchParams(location.search),projectId=qs.get('project_id'),initialProfile=qs.get('profile_key')||'landscape-1080';
const state={session:null,project:null,transport:null,selected:new Set(),streams:new Map(),zoom:1,history:{can_undo:false,can_redo:false},sceneMeta:{}};
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
function esc(v){return String(v??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));}
async function api(url,opt={}){const r=await fetch(url,{headers:{'Content-Type':'application/json'},...opt});let d={};try{d=await r.json()}catch{}if(!r.ok)throw new Error(d.detail||`Request failed (${r.status})`);return d;}
function preview(){return state.project?.scenes?.find(s=>s.id===state.session?.preview_scene_id);}function sourceById(id){return preview()?.sources?.find(s=>s.id===id);}function transformOf(src){return src.config?.transform||{x:0,y:0,width:1,height:1,rotation:0,opacity:1};}function effectsOf(src){return src.config?.effects||{brightness:1,contrast:1,saturation:1,hue:0,blur:0,rounded:0};}
function sourceStyle(src){const x=transformOf(src),e=effectsOf(src);return `left:${x.x*100}%;top:${x.y*100}%;width:${x.width*100}%;height:${x.height*100}%;opacity:${x.opacity??1};transform:rotate(${x.rotation||0}deg);z-index:${src.z_index||0};filter:brightness(${e.brightness??1}) contrast(${e.contrast??1}) saturate(${e.saturation??1}) hue-rotate(${e.hue??0}deg) blur(${e.blur??0}px);border-radius:${(e.rounded??0)*100}%`;}
function rgba(hex,a){const h=String(hex||'#07131c').replace('#','');if(!/^[0-9a-fA-F]{6}$/.test(h))return `rgba(7,19,28,${a})`;return `rgba(${parseInt(h.slice(0,2),16)},${parseInt(h.slice(2,4),16)},${parseInt(h.slice(4,6),16)},${a})`;}
function graphicMedia(src){const g=src.config?.graphic;if(!g)return null;const s=g.style||{},kind=g.kind||'custom_text';return `<div class='graphic graphic-${esc(kind)}' style='font-size:${Number(s.font_size||42)}px;font-weight:${Number(s.font_weight||700)};text-align:${esc(s.align||'left')};color:${esc(s.text_color||'#ffffff')};background:${rgba(s.background_color,s.background_opacity??.78)};padding:${Number(s.padding||18)}px;border-radius:${Number(s.corner_radius||14)}px'><strong>${esc(g.text||src.config?.text||src.name)}</strong>${g.secondary_text?`<span>${esc(g.secondary_text)}</span>`:''}</div>`;}
function sourceMedia(src){const stream=state.streams.get(src.id);if(stream&&src.source_type!=='microphone')return `<video data-video='${src.id}' autoplay muted playsinline></video>`;const graphic=graphicMedia(src);if(graphic)return graphic;if(src.source_type==='text')return `<div class=placeholder>${esc(src.config?.text||src.name)}</div>`;return `<div class=placeholder>${esc(src.name)}<br><small>${src.config?.browser_capture?'Device/media detached':src.source_type}</small></div>`;}
function sourceLayer(src,programme=false){if(!src.visible)return'';const selected=!programme&&state.selected.has(src.id);return `<div class='source ${selected?'selected':''} ${src.locked?'locked':''}' data-source='${esc(src.id)}' style='${sourceStyle(src)}'>${sourceMedia(src)}${!programme&&!src.locked?'<span class=rotate data-rotate=1 title="Rotate"></span><span class=resize data-resize=1 title="Resize"></span>':''}</div>`;}
function previewChrome(){return '<div class=safe></div><div class="safe title"></div><div class=guide-v></div><div class=guide-h></div>';}
function sceneMeta(id){return state.sceneMeta?.[id]||{locked:false,notes:'',folder:''};}
function assign(d){if(d.session)state.session=d.session;if(d.project)state.project=d.project;if(d.transport)state.transport=d.transport;if(d.history)state.history=d.history;if(d.scene_meta)state.sceneMeta=d.scene_meta;pruneStreams();render();}
function pruneStreams(){if(!state.project)return;const ids=new Set(state.project.scenes.flatMap(s=>(s.sources||[]).map(x=>x.id)));for(const [id,stream] of [...state.streams])if(!ids.has(id)){stream.getTracks().forEach(t=>t.stop());state.streams.delete(id);stopMeter(id);}}
function sizeCanvases(){if(!state.session)return;const aspect=state.session.profile?.width/state.session.profile?.height||16/9;$$('.canvas').forEach(c=>{const host=c.parentElement,r=host.getBoundingClientRect();let w=r.width,h=w/aspect;if(h>r.height){h=r.height;w=h*aspect;}c.style.width=`${w}px`;c.style.height=`${h}px`;c.style.transform=`translate(-50%,-50%) scale(${state.zoom})`;});}
function render(){if(!state.session||!state.project)return;$('#version').textContent=`v${state.session.version}`;$('#transport').textContent=`Transport ${state.transport?.state||'unknown'}`;$('#profile').value=state.session.profile_key;$('#undo').disabled=!state.history.can_undo;$('#redo').disabled=!state.history.can_redo;$('#historyState').textContent=`Undo ${state.history.undo_depth||0} · Redo ${state.history.redo_depth||0}`;$('#scenes').innerHTML=state.project.scenes.map(s=>{const m=sceneMeta(s.id);return `<button class='scene ${s.id===state.session.preview_scene_id?'active':''}' data-scene='${s.id}'>${m.locked?'🔒 ':''}${esc(s.name)}${m.folder?` <small>· ${esc(m.folder)}</small>`:''}</button>`}).join('');const p=preview();$('#previewCanvas').innerHTML=previewChrome()+(p?.sources||[]).map(s=>sourceLayer(s)).join('');const prog=state.session.programme_snapshot||{};$('#programmeCanvas').innerHTML=(prog.sources||[]).map(s=>sourceLayer(s,true)).join('')||'<div class=placeholder>OFF AIR / no committed Programme snapshot</div>';$('#sources').innerHTML=(p?.sources||[]).slice().sort((a,b)=>(b.z_index||0)-(a.z_index||0)).map(s=>`<button class='source-row ${state.selected.has(s.id)?'active':''}' data-source-row='${s.id}'>${s.locked?'🔒 ':''}${esc(s.name)} <small>${esc(s.source_type)}</small></button>`).join('');sizeCanvases();bindCanvas();attachStreams();renderInspector();renderMixer();renderSceneControls();}
function bindCanvas(){$$('[data-scene]').forEach(b=>b.onclick=()=>selectScene(b.dataset.scene));$$('[data-source-row]').forEach(b=>b.onclick=e=>selectSource(b.dataset.sourceRow,e.shiftKey));$$('#previewCanvas [data-source]').forEach(n=>n.addEventListener('pointerdown',pointerStart));}
function selectSource(id,multi){if(!multi)state.selected.clear();if(multi&&state.selected.has(id))state.selected.delete(id);else state.selected.add(id);render();}
async function loadHistory(){if(!state.session)return;try{const d=await api(`/shared-sky/studio/api/sessions/${state.session.id}/history`);state.history=d.history||state.history;state.sceneMeta=d.scene_meta||state.sceneMeta;render();}catch(e){handle(e);}}
async function selectScene(id){try{assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/preview`,{method:'POST',body:JSON.stringify({scene_id:id,expected_version:state.session.version})}));state.selected.clear();await loadHistory();}catch(e){await handle(e);}}
function snapPos(v,size){for(const target of [0,(1-size)/2,1-size])if(Math.abs(v-target)<.012)return target;return Math.max(-4,Math.min(4,v));}
function updateSourceNodes(){for(const id of state.selected){const src=sourceById(id),node=$(`#previewCanvas [data-source='${CSS.escape(id)}']`);if(src&&node)node.style.cssText=sourceStyle(src);}}
function pointerStart(e){const id=e.currentTarget.dataset.source,src=sourceById(id);if(!src||src.locked)return;if(!state.selected.has(id)){state.selected.clear();state.selected.add(id);render();return;}const target=e.currentTarget,mode=e.target.dataset.rotate?'rotate':e.target.dataset.resize?'resize':'move',base=new Map([...state.selected].map(sid=>[sid,{...transformOf(sourceById(sid))}])),rect=$('#previewCanvas').getBoundingClientRect(),sx=e.clientX,sy=e.clientY,bounds=target.getBoundingClientRect(),cx=bounds.left+bounds.width/2,cy=bounds.top+bounds.height/2,startAngle=Math.atan2(e.clientY-cy,e.clientX-cx);target.setPointerCapture(e.pointerId);function move(ev){const dx=(ev.clientX-sx)/rect.width,dy=(ev.clientY-sy)/rect.height,angle=Math.atan2(ev.clientY-cy,ev.clientX-cx),degrees=(angle-startAngle)*180/Math.PI;for(const sid of state.selected){const s=sourceById(sid),b=base.get(sid);if(!s||s.locked)continue;const next={...b};if(mode==='move'){next.x=snapPos(b.x+dx,b.width);next.y=snapPos(b.y+dy,b.height);}else if(mode==='resize'){next.width=Math.max(.02,Math.min(8,b.width+dx));next.height=Math.max(.02,Math.min(8,b.height+dy));}else{next.rotation=b.rotation+degrees;if(ev.shiftKey)next.rotation=Math.round(next.rotation/15)*15;}s.config={...(s.config||{}),transform:next};}updateSourceNodes();}async function up(){target.removeEventListener('pointermove',move);target.removeEventListener('pointerup',up);await persistSelected();}target.addEventListener('pointermove',move);target.addEventListener('pointerup',up);}
async function persistSelected(){const items=[...state.selected].map(id=>sourceById(id)).filter(Boolean).filter(s=>!s.locked).map(src=>({source_id:src.id,transform:transformOf(src),effects:effectsOf(src)}));if(!items.length)return;try{assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/sources/batch-transform`,{method:'PATCH',body:JSON.stringify({expected_version:state.session.version,items})}));}catch(e){await handle(e);}}
async function refreshProject(){if(!state.session)return;assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}`));await loadHistory();}
async function handle(e){$('#notice').textContent=e.message;if(/version conflict|another tab/i.test(e.message)&&state.session)await refreshProject();}
async function take(kind){try{if(kind==='cut'){assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/cut`,{method:'POST',body:JSON.stringify({expected_version:state.session.version})}));return;}assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/transition`,{method:'POST',body:JSON.stringify({expected_version:state.session.version,transition_key:$('#transition').value,duration_ms:Number($('#duration').value),reduced_motion:matchMedia('(prefers-reduced-motion: reduce)').matches})}));const token=state.session.transition.transition_id,ms=state.session.transition.duration_ms||0;setTimeout(async()=>{try{assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/transition/complete`,{method:'POST',body:JSON.stringify({expected_version:state.session.version,transition_id:token})}));}catch(e){handle(e);}},ms);}catch(e){handle(e);}}
async function captureFor(type){if(type==='camera')return navigator.mediaDevices.getUserMedia({video:true,audio:false});if(type==='microphone')return navigator.mediaDevices.getUserMedia({audio:true,video:false});if(type==='screen')return navigator.mediaDevices.getDisplayMedia({video:true,audio:false});return null;}
async function addSource(type){let stream=null;try{stream=await captureFor(type);const name=type[0].toUpperCase()+type.slice(1),result=await api(`/shared-sky/studio/api/sessions/${state.session.id}/sources/tracked`,{method:'POST',body:JSON.stringify({source_type:type,name,visible:true,config:type==='text'?{text:'Text'}:{},expected_version:state.session.version})});assign(result);if(stream&&result.source_id){state.streams.set(result.source_id,stream);watchStream(result.source_id,name,stream);render();}}catch(e){stream?.getTracks().forEach(t=>t.stop());handle(e);}}
function watchStream(id,name,stream){for(const track of stream.getTracks())track.addEventListener('ended',()=>{state.streams.delete(id);stopMeter(id);$('#notice').textContent=`${name} disconnected or permission ended`;render();},{once:true});}
async function deleteSelected(){const ids=[...state.selected];if(!ids.length)return;try{const result=await api(`/shared-sky/studio/api/sessions/${state.session.id}/sources/batch-delete`,{method:'POST',body:JSON.stringify({source_ids:ids,expected_version:state.session.version})});for(const id of ids){state.streams.get(id)?.getTracks().forEach(t=>t.stop());state.streams.delete(id);stopMeter(id);}state.selected.clear();assign(result);}catch(e){handle(e);}}
function attachStreams(){for(const [id,stream] of state.streams){const video=$(`[data-video='${CSS.escape(id)}']`);if(video&&video.srcObject!==stream)video.srcObject=stream;}}
function renderInspector(){const ids=[...state.selected],src=ids.length===1?sourceById(ids[0]):null;if(!src){$('#inspector').innerHTML=ids.length>1?`<p>${ids.length} sources selected.</p><div class=row><button data-align=left>Align Left</button><button data-align=center>Centre</button><button data-align=top>Align Top</button><button id=distribute>Distribute H</button></div>`:'<p class=muted>Select a Preview source. Drag/resize/rotate affects Preview only.</p>';$$('[data-align]').forEach(b=>b.onclick=()=>align(b.dataset.align));if($('#distribute'))$('#distribute'].onclick=distribute;return;}const x=transformOf(src),fx=effectsOf(src);$('#inspector').innerHTML=`<h3>${esc(src.name)}</h3>${['x','y','width','height','rotation','opacity'].map(k=>`<label class=field>${k}<input data-t='${k}' type=number step='${k==='rotation'?1:.01}' value='${x[k]??(k==='opacity'?1:0)}'></label>`).join('')}<div class=section><b>Real effects</b>${['brightness','contrast','saturation','hue','blur','rounded'].map(k=>`<label class=field>${k}<input data-fx='${k}' type=number step='${k==='hue'?1:.01}' value='${fx[k]??0}'></label>`).join('')}</div><div class=row><button id=saveInspector>Save</button><button id=centerSource>Centre</button><button id=fitSource>Fit</button>${src.config?.browser_capture&&!state.streams.has(src.id)?`<button id=reconnect>Reconnect ${esc(src.source_type)}</button>`:''}</div>`;$('#saveInspector').onclick=()=>saveInspector(src);$('#centerSource').onclick=()=>{src.config.transform={...x,x:(1-x.width)/2,y:(1-x.height)/2};persistSelected();};$('#fitSource').onclick=()=>{src.config.transform={...x,x:0,y:0,width:1,height:1,rotation:0};persistSelected();};if($('#reconnect'))$('#reconnect').onclick=()=>reconnect(src);}
async function saveInspector(src){for(const n of $$('[data-t]'))src.config.transform[n.dataset.t]=Number(n.value);src.config.effects={...effectsOf(src)};for(const n of $$('[data-fx]'))src.config.effects[n.dataset.fx]=Number(n.value);await persistSelected();}
async function reconnect(src){let stream=null;try{stream=await captureFor(src.source_type);if(!stream)return;state.streams.get(src.id)?.getTracks().forEach(t=>t.stop());state.streams.set(src.id,stream);watchStream(src.id,src.name,stream);render();}catch(e){stream?.getTracks().forEach(t=>t.stop());handle(e);}}
async function align(kind){const list=[...state.selected].map(sourceById).filter(Boolean).filter(s=>!s.locked);if(!list.length)return;for(const s of list){const x=transformOf(s);if(kind==='left')x.x=0;if(kind==='top')x.y=0;if(kind==='center')x.x=(1-x.width)/2;s.config.transform=x;}await persistSelected();}
async function distribute(){const list=[...state.selected].map(sourceById).filter(Boolean).filter(s=>!s.locked).sort((a,b)=>transformOf(a).x-transformOf(b).x);if(list.length<3)return;const first=transformOf(list[0]).x,last=transformOf(list[list.length-1]).x,step=(last-first)/(list.length-1);list.forEach((s,i)=>s.config.transform={...transformOf(s),x:first+i*step});await persistSelected();}
function renderSceneControls(){const scene=preview(),m=scene?sceneMeta(scene.id):{};$('#sceneState').textContent=scene?`${m.locked?'Locked':'Editable'}${m.folder?` · ${m.folder}`:''}${m.notes?` · note saved`:''}`:'No Preview scene';$('#lockScene').textContent=m.locked?'Unlock':'Lock';}
async function newScene(){const name=prompt('Scene name','New Scene');if(!name)return;try{assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/scenes/tracked`,{method:'POST',body:JSON.stringify({name,layout_key:'solo',transition_key:'fade',transition_ms:350,expected_version:state.session.version})}));}catch(e){handle(e);}}
async function duplicateScene(){if(!preview())return;try{assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/scenes/${preview().id}/duplicate-tracked`,{method:'POST',body:JSON.stringify({expected_version:state.session.version})}));}catch(e){handle(e);}}
async function patchScene(fields){if(!preview())return;try{assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/scenes/${preview().id}/tracked`,{method:'PATCH',body:JSON.stringify({...fields,expected_version:state.session.version})}));}catch(e){handle(e);}}
async function renameScene(){if(!preview())return;const name=prompt('Rename scene',preview().name);if(name)patchScene({name});}
async function noteScene(){const m=sceneMeta(preview()?.id);const notes=prompt('Operator notes',m?.notes||'');if(notes!==null)patchScene({notes});}
async function folderScene(){const m=sceneMeta(preview()?.id);const folder=prompt('Scene folder/group',m?.folder||'');if(folder!==null)patchScene({folder});}
async function toggleSceneLock(){if(!preview())return;patchScene({locked:!sceneMeta(preview().id).locked});}
async function deleteScene(){if(!preview())return;if(!confirm(`Delete scene “${preview().name}”?`))return;const programme=state.session.programme_scene_id===preview().id;let confirmed=false;if(programme){confirmed=confirm('This scene is the identity of the current Programme snapshot. Programme pixels remain unchanged, but delete this editable scene anyway?');if(!confirmed)return;}try{assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/scenes/${preview().id}/tracked`,{method:'DELETE',body:JSON.stringify({expected_version:state.session.version,confirm_programme_reference:confirmed})}));}catch(e){handle(e);}}
async function moveScene(direction){if(!preview())return;const ids=state.project.scenes.map(s=>s.id),i=ids.indexOf(preview().id),j=i+direction;if(j<0||j>=ids.length)return;[ids[i],ids[j]]=[ids[j],ids[i]];try{assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/scenes/reorder-tracked`,{method:'POST',body:JSON.stringify({scene_ids:ids,expected_version:state.session.version})}));}catch(e){handle(e);}}
async function addGraphic(kind){const text=prompt(kind==='lower_third'?'Primary lower-third text':'Graphic text','');if(text===null)return;let secondary='';if(['lower_third','sponsor_card'].includes(kind)){const v=prompt('Secondary text (optional)','');secondary=v===null?'':v;}try{assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/graphics`,{method:'POST',body:JSON.stringify({kind,name:`Shared Sky ${kind.replaceAll('_',' ')}`,text,secondary_text:secondary,style:{},expected_version:state.session.version})}));}catch(e){handle(e);}}
async function historyAction(kind){try{assign(await api(`/shared-sky/studio/api/sessions/${state.session.id}/${kind}`,{method:'POST',body:JSON.stringify({expected_version:state.session.version})}));state.selected.clear();}catch(e){handle(e);}}
const meterNodes=new Map();function stopMeter(id){const stop=meterNodes.get(id);if(stop)stop();}
function renderMixer(){const audio=(preview()?.sources||[]).filter(s=>['microphone','audio','video'].includes(s.source_type));$('#mixer').innerHTML=audio.map(s=>`<div class=channel data-channel='${s.id}'><b>${esc(s.name)}</b><div class='meter ${state.streams.has(s.id)?'':'offline'}'><i></i></div><small class=muted>${state.streams.has(s.id)?'Measured browser signal':'Signal unavailable'}</small></div>`).join('')||'<span class=muted>No audio sources.</span>';for(const s of audio){const stream=state.streams.get(s.id);if(stream)startMeter(s.id,stream);}}
function startMeter(id,stream){if(meterNodes.has(id))return;try{const ctx=new AudioContext(),source=ctx.createMediaStreamSource(stream),analyser=ctx.createAnalyser(),data=new Float32Array(512);analyser.fftSize=512;source.connect(analyser);let stopped=false;function tick(){if(stopped)return;analyser.getFloatTimeDomainData(data);let sum=0;for(const v of data)sum+=v*v;const rms=Math.sqrt(sum/data.length),db=20*Math.log10(Math.max(rms,1e-7)),pct=Math.max(0,Math.min(100,(db+60)/60*100)),bar=$(`[data-channel='${CSS.escape(id)}'] .meter i`);if(bar)bar.style.width=`${pct}%`;requestAnimationFrame(tick);}tick();const stop=()=>{if(stopped)return;stopped=true;ctx.close();meterNodes.delete(id);};stream.getTracks().forEach(t=>t.addEventListener('ended',stop,{once:true}));meterNodes.set(id,stop);}catch(e){$('#notice').textContent=`Audio meter unavailable: ${e.message}`;}}
async function setProfile(){try{const r=await api(`/shared-sky/studio/api/sessions/${state.session.id}/profile`,{method:'PATCH',body:JSON.stringify({profile_key:$('#profile').value,expected_version:state.session.version})});state.session=r.session;await refreshProject();}catch(e){handle(e);}}
function keySafe(e){return !(e.target?.matches?.('input,textarea,select,[contenteditable=true]')||e.target?.closest?.('[contenteditable=true]'));}
document.addEventListener('keydown',async e=>{if(!keySafe(e))return;if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();historyAction(e.shiftKey?'redo':'undo');return;}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='y'){e.preventDefault();historyAction('redo');return;}if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)&&state.selected.size){e.preventDefault();const delta=e.shiftKey?0.01:0.0025;for(const id of state.selected){const s=sourceById(id);if(!s||s.locked)continue;const x=transformOf(s);if(e.key==='ArrowLeft')x.x-=delta;if(e.key==='ArrowRight')x.x+=delta;if(e.key==='ArrowUp')x.y-=delta;if(e.key==='ArrowDown')x.y+=delta;s.config.transform=x;}updateSourceNodes();await persistSelected();}if(e.altKey&&e.key.toLowerCase()==='c'){e.preventDefault();take('cut');}if(e.ctrlKey&&e.key==='Enter'){e.preventDefault();take('transition');}});
window.addEventListener('resize',sizeCanvases);window.addEventListener('beforeunload',()=>{for(const stream of state.streams.values())stream.getTracks().forEach(t=>t.stop());for(const id of [...meterNodes.keys()])stopMeter(id);});
$('#cut').onclick=()=>take('cut');$('#take').onclick=()=>take('transition');$('#profile').onchange=setProfile;$('#zoom').oninput=e=>{state.zoom=Number(e.target.value);sizeCanvases();};$('#addCamera').onclick=()=>addSource('camera');$('#addMic').onclick=()=>addSource('microphone');$('#addScreen').onclick=()=>addSource('screen');$('#addText').onclick=()=>addSource('text');$('#deleteSource').onclick=deleteSelected;$('#undo').onclick=()=>historyAction('undo');$('#redo').onclick=()=>historyAction('redo');$('#newScene').onclick=newScene;$('#duplicateScene').onclick=duplicateScene;$('#renameScene').onclick=renameScene;$('#deleteScene').onclick=deleteScene;$('#lockScene').onclick=toggleSceneLock;$('#noteScene').onclick=noteScene;$('#folderScene').onclick=folderScene;$('#sceneUp').onclick=()=>moveScene(-1);$('#sceneDown').onclick=()=>moveScene(1);$('#addLowerThird').onclick=()=>addGraphic('lower_third');$('#addTitle').onclick=()=>addGraphic('title');$('#addBanner').onclick=()=>addGraphic('banner');
(async()=>{if(!projectId)throw new Error('project_id is required');assign(await api('/shared-sky/studio/api/sessions',{method:'POST',body:JSON.stringify({project_id:projectId,profile_key:initialProfile})}));await loadHistory();})().catch(handle);
"""


def professional_html(project_id: str) -> str:
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Shared Sky Professional Studio</title><style>{PRO_CSS}</style></head><body><main class='app'>
<header class='top'><strong>Shared Sky · Professional Studio Mode</strong><span id='version'>v—</span><span id='transport'>Transport checking</span><button id='undo' disabled>Undo</button><button id='redo' disabled>Redo</button><span id='historyState' class='status history-ready'>Undo 0 · Redo 0</span><label>Profile <select id='profile'><option value='landscape-1080'>16:9 1080p</option><option value='portrait-1080'>9:16 1080p</option><option value='square-1080'>1:1 1080</option></select></label><label>Zoom <input id='zoom' type='range' min='.5' max='2' step='.05' value='1'></label><span id='notice' class='status' role='status' aria-live='polite'>Preview edits are isolated from Programme.</span></header>
<aside class='left panel'><h2>Scenes</h2><div class='row'><button id='newScene'>+ Scene</button><button id='duplicateScene'>Duplicate</button><button id='renameScene'>Rename</button></div><div class='row'><button id='sceneUp'>↑</button><button id='sceneDown'>↓</button><button id='lockScene'>Lock</button><button id='noteScene'>Notes</button><button id='folderScene'>Group</button></div><div id='sceneState' class='muted'>No Preview scene</div><div id='scenes'></div><button id='deleteScene' class='danger'>Delete scene</button><h2>Sources</h2><div class='row'><button id='addCamera'>+ Camera</button><button id='addMic'>+ Mic</button><button id='addScreen'>+ Screen</button><button id='addText'>+ Text</button></div><div class='row section'><button id='addLowerThird'>Lower Third</button><button id='addTitle'>Title</button><button id='addBanner'>Banner</button></div><div id='sources'></div><button id='deleteSource' class='danger'>Remove selected</button><p class='muted'>Capture permissions stay in the browser. Restored capture sources reconnect explicitly after Undo/refresh.</p></aside>
<section class='center'><div class='monitors'><section class='monitor preview' aria-label='Preview monitor'><span class='label'>PREVIEW</span><div class='viewport'><div id='previewCanvas' class='canvas'></div></div></section><section class='monitor programme' aria-label='Programme monitor'><span class='label'>PROGRAMME</span><div class='viewport'><div id='programmeCanvas' class='canvas'></div></div></section></div><div class='top'><label>Transition <select id='transition'><option>fade</option><option>dip_to_colour</option><option>slide</option><option>push</option><option>zoom</option></select></label><label>ms <input id='duration' type='number' min='0' max='20000' value='350' style='width:88px'></label><button id='cut' class='cut'>CUT</button><button id='take' class='take'>TRANSITION</button><span class='muted'>Alt+C CUT · Ctrl+Enter transition · Ctrl/Cmd+Z undo · arrows nudge · Shift while rotating snaps 15°</span></div></section>
<aside class='right panel'><h2>Inspector</h2><div id='inspector'><p class='muted'>Select a Preview source.</p></div><hr><p class='muted'>A drag, resize, rotation, alignment or multi-selection move is persisted as one atomic history action. Programme remains the last authoritative committed snapshot.</p></aside>
<section class='bottom panel'><h2>Audio Mixer</h2><div id='mixer' class='mixer'></div></section></main><script>{PRO_JS}</script></body></html>"""


@router.get(
    "/shared-sky/studio/professional", response_class=HTMLResponse, include_in_schema=False
)
def professional_canvas_page(project_id: str, request: Request):
    member, _ = _member(request)
    try:
        shared_sky.project(member.user_id, project_id)
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky project not found") from exc
    return HTMLResponse(professional_html(project_id), headers={"Cache-Control": "no-store"})


def install_shared_sky_professional_canvas(app: Any) -> None:
    existing = {getattr(route, "path", "") for route in app.router.routes}
    if "/shared-sky/studio/professional" not in existing:
        app.include_router(router)


__all__ = [
    "PRO_JS",
    "ProfilePatch",
    "SourceZPatch",
    "StudioSourceCreate",
    "StudioSourceDelete",
    "create_studio_source",
    "delete_studio_source",
    "install_shared_sky_professional_canvas",
    "patch_source_z",
    "professional_html",
    "router",
    "update_profile",
]
