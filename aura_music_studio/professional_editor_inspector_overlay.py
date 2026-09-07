from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from .professional_editor_lifecycle_inspector import router as professional_editor_lifecycle_inspector_router
from .professional_editor_track_group_ui import router as professional_editor_track_group_router
from .professional_editor_workspace import professional_editor_workspace as base_workspace

router = APIRouter(tags=["Professional Editor Inspector"])
router.include_router(professional_editor_lifecycle_inspector_router)
router.include_router(professional_editor_track_group_router)

PRO_EDITOR_CONTROLS_JS = r"""
(()=>{
  const byId=id=>document.getElementById(id);
  const panel=byId('addMask')?.closest('.section');
  if(!panel||byId('proInspectorControls'))return;
  const isPro=document.body.dataset.pro==='true';
  byId('addMask').style.display='none';
  byId('addEffect').style.display='none';
  const intro=panel.querySelector('p.muted');
  if(intro)intro.textContent='Professional masks, effects, keyframes and non-destructive A/B branches are available here. Unsupported renderer state remains fail-closed rather than being silently omitted.';
  const style=document.createElement('style');
  style.textContent=`.progrid{display:grid;grid-template-columns:1fr 1fr;gap:6px}.probox{border:1px solid var(--line);border-radius:10px;padding:8px;margin:8px 0;background:#ffffff04}.probox h4{margin:0 0 6px;font-size:.7rem;color:var(--cyan)}.probox textarea{width:100%;min-height:62px;resize:vertical}.prowide{grid-column:1/-1}.exportrow{display:flex;gap:6px;flex-wrap:wrap;align-items:end}.exportrow .label{min-width:82px;flex:1}.exportresult{font-size:.68rem;margin-top:7px;word-break:break-word}`;
  document.head.append(style);
  const root=document.createElement('div');root.id='proInspectorControls';
  root.innerHTML=`
    <div class="probox"><h4>Mask</h4><div class="progrid">
      <label class="label">Shape<select class="field" id="proMaskShape"><option>rectangle</option><option>ellipse</option><option>polygon</option><option>path</option></select></label>
      <label class="label">Mode<select class="field" id="proMaskMode"><option>add</option><option>subtract</option><option>intersect</option></select></label>
      <label class="label">Feather px<input class="field" id="proMaskFeather" type="number" min="0" max="250" step="1" value="0"></label>
      <label class="label">Expansion px<input class="field" id="proMaskExpansion" type="number" min="-31" max="31" step="1" value="0"></label>
      <label class="label prowide">Normalized points JSON<textarea class="field" id="proMaskPoints">[[0.1,0.1],[0.9,0.9]]</textarea></label>
      <label class="label"><span><input id="proMaskInvert" type="checkbox"> Invert</span></label><button class="btn" id="proAddMask">Add mask</button>
    </div></div>
    <div class="probox"><h4>Effect</h4><div class="progrid">
      <label class="label">Type<select class="field" id="proEffectType"><option>gaussian_blur</option><option>sharpen</option><option>grayscale</option><option>invert</option><option>sepia</option><option>brightness</option><option>contrast</option><option>saturation</option><option>vignette</option><option>pixelate</option></select></label>
      <label class="label">Wet / dry<input class="field" id="proEffectMix" type="number" min="0" max="1" step=".01" value="1"></label>
      <label class="label prowide">Parameters JSON<textarea class="field" id="proEffectParams">{}</textarea></label>
      <button class="btn prowide" id="proAddEffect">Add effect</button>
    </div></div>
    <div class="probox"><h4>Keyframe point</h4><div class="progrid">
      <label class="label prowide">Parameter<select class="field" id="proKeyParam"><option>transform.x</option><option>transform.y</option><option>transform.scale_x</option><option>transform.scale_y</option><option>transform.rotation</option><option>opacity</option><option>crop.left</option><option>crop.right</option><option>crop.top</option><option>crop.bottom</option><option>color.exposure</option><option>color.brightness</option><option>color.contrast</option><option>color.saturation</option><option>color.gamma</option></select></label>
      <label class="label">Time s<input class="field" id="proKeyTime" type="number" min="0" step=".01" value="0"></label>
      <label class="label">Value<input class="field" id="proKeyValue" type="number" step=".01" value="0"></label>
      <label class="label">Curve<select class="field" id="proKeyCurve"><option>linear</option><option>hold</option><option>smooth</option><option>bezier</option></select></label>
      <button class="btn" id="proAddKey">Add / replace</button>
    </div><div id="proKeySummary" class="muted" style="font-size:.64rem;margin-top:6px"></div></div>
    <div class="probox"><h4>Render / Preview</h4><div class="exportrow">
      <label class="label">Format<select class="field" id="proExportFormat"><option>png</option><option>webp</option><option>jpeg</option><option>mp4</option></select></label>
      <label class="label">Quality<input class="field" id="proExportQuality" type="number" min="1" max="100" value="92"></label>
      <label class="label">Still frame s<input class="field" id="proExportTime" type="number" min="0" step=".01" value="0"></label>
      <button class="btn primary" id="proRender">Render</button>
    </div><div id="proExportResult" class="exportresult muted">Exports flatten the selected editor state; source media remains unchanged.</div></div>`;
  panel.append(root);
  root.querySelectorAll('input,select,textarea,button').forEach(el=>{if(!isPro&&el.id!=='proRender'&&el.id!=='proExportFormat'&&el.id!=='proExportQuality'&&el.id!=='proExportTime')el.disabled=true});

  function current(){try{return selected||null}catch(_){return null}}
  function currentSeq(){try{return selectedSequence||null}catch(_){return null}}
  function parseJSON(id,fallback){const raw=byId(id).value.trim();if(!raw)return fallback;return JSON.parse(raw)}
  function refresh(response,itemId){
    try{if(response.editor)state=response.editor;if(itemId){selected=branch().items.find(row=>row.id===itemId)||selected}render(false);fillInspector();renderPreview();summary()}catch(_){}
  }
  function summary(){const item=current(),target=byId('proKeySummary');if(!target)return;if(!item){target.textContent='Select an item first.';return}const lanes=item.keyframes||{};target.textContent=Object.keys(lanes).length?Object.entries(lanes).map(([name,pts])=>`${name}: ${pts.length} point${pts.length===1?'':'s'}`).join(' · '):'No keyframe lanes on this item.'}
  document.addEventListener('click',event=>{if(event.target?.closest?.('.clip,.asset'))setTimeout(summary,0)});

  byId('proAddMask').onclick=async()=>{const item=current();if(!item)return status('Select an image/video item first.',true);try{const points=parseJSON('proMaskPoints',[]);if(!Array.isArray(points))throw new Error('Mask points must be a JSON array.');const body={name:`${byId('proMaskShape').value} mask`,shape:byId('proMaskShape').value,mode:byId('proMaskMode').value,feather:Number(byId('proMaskFeather').value||0),expansion:Number(byId('proMaskExpansion').value||0),inverted:byId('proMaskInvert').checked,points};const response=await api(endpoint(`/items/${encodeURIComponent(item.id)}/masks`),{method:'POST',body:JSON.stringify(body)});refresh(response,item.id);status('Mask added to the non-destructive edit graph.')}catch(error){status(error.message,true)}};
  byId('proAddEffect').onclick=async()=>{const item=current();if(!item)return status('Select an item first.',true);try{const parameters=parseJSON('proEffectParams',{});if(!parameters||Array.isArray(parameters)||typeof parameters!=='object')throw new Error('Effect parameters must be a JSON object.');const body={type:byId('proEffectType').value,mix:Number(byId('proEffectMix').value||1),parameters};const response=await api(endpoint(`/item/${encodeURIComponent(item.id)}/effects`),{method:'POST',body:JSON.stringify(body)});refresh(response,item.id);status('Effect added. Unsupported render effects remain fail-closed.')}catch(error){status(error.message,true)}};
  byId('proAddKey').onclick=async()=>{const item=current();if(!item)return status('Select an item first.',true);try{const parameter=byId('proKeyParam').value,time=Number(byId('proKeyTime').value||0),value=Number(byId('proKeyValue').value||0),interpolation=byId('proKeyCurve').value;const existing=[...((item.keyframes||{})[parameter]||[])].filter(point=>Math.abs(Number(point.time)-time)>.000001);existing.push({time,value,interpolation});existing.sort((a,b)=>Number(a.time)-Number(b.time));const response=await api(endpoint(`/items/${encodeURIComponent(item.id)}/keyframes`),{method:'POST',body:JSON.stringify({parameter,keyframes:existing})});refresh(response,item.id);status(`Keyframe saved · ${parameter} @ ${time.toFixed(2)}s`)}catch(error){status(error.message,true)}};
  byId('proRender').onclick=async()=>{let sequence=currentSeq();try{if(!sequence&&typeof branch==='function')sequence=branch().sequences?.[0]}catch(_){}if(!sequence)return status('Select or create a sequence first.',true);const format=sequence.kind==='video'?'mp4':byId('proExportFormat').value;byId('proExportFormat').value=format;try{status('Rendering non-destructive editor state…');const response=await api(endpoint(`/sequences/${encodeURIComponent(sequence.id)}/render`),{method:'POST',body:JSON.stringify({format,quality:Number(byId('proExportQuality').value||92),frame_time:sequence.kind==='image'?Number(byId('proExportTime').value||0):0})});const url=response.download_url;byId('proExportResult').innerHTML=`Rendered · <a href="${url}" style="color:var(--gold);text-decoration:underline">${response.export.filename}</a> · source media unchanged`;status(`Rendered ${response.export.filename}`)}catch(error){status(error.message,true)}};
  summary();
})();
"""


@router.get("/creative/editor-pro-controls.js", include_in_schema=False)
def editor_pro_controls_js():
    return Response(PRO_EDITOR_CONTROLS_JS, media_type="application/javascript", headers={"Cache-Control": "no-store"})


@router.get("/creative/editor-workspace", response_class=HTMLResponse, include_in_schema=False)
def enhanced_professional_editor_workspace(request: Request, project: str = ""):
    response = base_workspace(request, project=project)
    if not isinstance(response, HTMLResponse) or response.status_code >= 300:
        return response
    text = response.body.decode("utf-8")
    markers = (
        "<script src='/creative/editor-pro-controls.js'></script>",
        "<script src='/creative/editor-pro-lifecycle-controls.js'></script>",
        "<script src='/creative/editor-track-group-controls.js'></script>",
    )
    for marker in markers:
        if marker not in text:
            text = text.replace("</body>", marker + "</body>")
    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in {"content-length", "content-type"}
    }
    headers["Cache-Control"] = "private, no-store"
    return HTMLResponse(text, status_code=response.status_code, headers=headers)


__all__ = ["PRO_EDITOR_CONTROLS_JS", "editor_pro_controls_js", "enhanced_professional_editor_workspace", "router"]
