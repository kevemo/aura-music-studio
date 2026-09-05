from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter(tags=["Professional Editor Track Groups"])

TRACK_GROUP_UI_MARKER = "ESP_PRO_TRACK_GROUP_UI_V1"

TRACK_GROUP_UI_JS = r"""
/* ESP_PRO_TRACK_GROUP_UI_V1 */
(()=>{
  const byId=id=>document.getElementById(id);
  const isPro=document.body.dataset.pro==='true';

  function item(){try{return selected||null}catch(_){return null}}
  function currentTrack(){
    const current=item();if(!current)return null;
    try{return (branch().tracks||[]).find(track=>(track.item_ids||[]).includes(current.id))||null}catch(_){return null}
  }
  function root(){return byId('proLifecycleStacks')||byId('proInspectorControls')}

  function build(){
    const host=root();if(!host||byId('proTrackGroupControls'))return false;
    const node=document.createElement('div');node.className='prostack';node.id='proTrackGroupControls';
    node.innerHTML=`<h4>Track group</h4>
      <div class="problendrow">
        <label class="label">Track blend<select class="field" id="proTrackBlend" ${isPro?'':'disabled'}>
          <option value="normal">Normal</option><option value="multiply">Multiply</option><option value="screen">Screen</option><option value="overlay">Overlay</option><option value="soft_light">Soft Light</option><option value="hard_light">Hard Light</option><option value="darken">Darken</option><option value="lighten">Lighten</option><option value="difference">Difference</option>
        </select></label>
        <label class="label">Track opacity<input class="field" id="proTrackOpacity" type="number" min="0" max="1" step="0.01" value="1" ${isPro?'':'disabled'}></label>
      </div>
      <div class="row" style="margin-top:6px"><button class="btn" id="proApplyTrackGroup" ${isPro?'':'disabled'}>Apply track group</button></div>
      <div id="proTrackGroupMeta" class="prostackmeta">Select an item to inspect its containing visual track.</div>`;
    host.append(node);
    byId('proApplyTrackGroup').onclick=apply;
    sync();
    return true;
  }

  function sync(){
    const track=currentTrack(),blend=byId('proTrackBlend'),opacity=byId('proTrackOpacity'),meta=byId('proTrackGroupMeta');
    if(!blend||!opacity||!meta)return;
    if(!track){blend.value='normal';opacity.value='1';meta.textContent='Select an item to inspect its containing visual track.';return}
    blend.value=track.blend_mode||'normal';
    opacity.value=Number(track.opacity??1).toFixed(2);
    meta.textContent=`${track.name||'Track'} · ${track.kind||'visual'} · blend ${String(track.blend_mode||'normal').replaceAll('_',' ')} · opacity ${Number(track.opacity??1).toFixed(2)} · reversible graph state.`;
  }

  async function apply(){
    const current=item(),track=currentTrack();
    if(!current||!track)return status('Select an item on a visual track first.',true);
    if(!isPro)return status('Professional track grouping controls require Pro.',true);
    const opacity=Math.max(0,Math.min(1,Number(byId('proTrackOpacity').value||1)));
    const blend_mode=byId('proTrackBlend').value;
    try{
      const response=await api(endpoint(`/tracks/${encodeURIComponent(track.id)}`),{method:'PATCH',body:JSON.stringify({changes:{opacity,blend_mode}})});
      if(response?.editor)state=response.editor;
      try{selected=branch().items.find(row=>row.id===current.id)||selected;render(false);fillInspector();renderPreview()}catch(_){}
      sync();
      status(`Track group saved · ${blend_mode.replaceAll('_',' ')} · opacity ${opacity.toFixed(2)}. Undo can restore it.`);
    }catch(error){status(error.message,true)}
  }

  function boot(){
    if(build())return;
    let attempts=0;const timer=setInterval(()=>{attempts+=1;if(build()||attempts>20)clearInterval(timer)},50);
  }
  document.addEventListener('click',event=>{if(event.target?.closest?.('.clip,.asset'))setTimeout(sync,0)});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();
"""


@router.get("/creative/editor-track-group-controls.js", include_in_schema=False)
def editor_track_group_controls_js():
    return Response(TRACK_GROUP_UI_JS, media_type="application/javascript", headers={"Cache-Control": "no-store"})


__all__ = ["TRACK_GROUP_UI_JS", "TRACK_GROUP_UI_MARKER", "editor_track_group_controls_js", "router"]
