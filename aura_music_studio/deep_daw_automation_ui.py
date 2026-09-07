from __future__ import annotations


DEEP_DAW_AUTOMATION_MARKER = "ESP_DEEP_DAW_AUTOMATION_UI_V1"

DEEP_DAW_AUTOMATION_JS = r"""
/* ESP_DEEP_DAW_AUTOMATION_UI_V1 */
;(() => {
  const byId=id=>document.getElementById(id);
  const enabled=()=>!!(typeof FLAGS!=='undefined'&&FLAGS.automation);
  const project=()=>typeof currentProject!=='undefined'?currentProject:null;
  const studio=()=>typeof session!=='undefined'?session:null;
  const call=(url,opt={})=>typeof api==='function'?api(url,opt):fetch(url,{credentials:'same-origin',...opt}).then(async response=>{let body={};try{body=await response.json()}catch(_){}if(!response.ok)throw new Error(body.detail||`Request failed (${response.status})`);return body});
  const say=(message,good=true)=>{if(typeof status==='function')status(message,good)};

  let points=[];
  let drag=-1;
  let catalog=null;
  let observer=null;

  function card(){return byId('deepAutomationCard')}
  function trackSelect(){return byId('deepAutoTrack')}
  function parameterSelect(){return byId('deepAutoParameter')}
  function interpolationSelect(){return byId('deepAutoInterpolation')}

  function activeTrack(){const s=studio(),id=trackSelect()?.value;return s?.tracks?.find?.(track=>track.id===id)||null}
  function specs(){
    if(!catalog)return [];
    return [
      ...(catalog.track||[]).map(value=>({...value,group:'Track'})),
      ...(catalog.clips||[]).map(value=>({...value,group:'Clips'})),
      ...(catalog.sends||[]).map(value=>({...value,group:'Sends'})),
      ...(catalog.effects||[]).map(value=>({...value,group:'Effects'})),
    ];
  }
  function activeSpec(){const parameter=parameterSelect()?.value;return specs().find(value=>value.parameter===parameter)||null}
  function bounds(){const spec=activeSpec();return [Number(spec?.min??-60),Number(spec?.max??18)]}
  function maxTime(){const s=studio();return Math.max(1,Number(s?.duration||0),Number(s?.loop_end||0))}

  function build(){
    if(!enabled()||card())return;
    const side=document.querySelector('.side');
    if(!side)return;
    const legacy=byId('graphAutoCard');if(legacy)legacy.style.display='none';
    const node=document.createElement('div');
    node.className='card';node.id='deepAutomationCard';
    node.innerHTML=`<h3>📈 Deep DAW Automation</h3><p class="muted">Real rendered automation for track, clip, send and effect parameters. Source audio is never overwritten.</p><div class="field"><label>Track / Bus</label><select id="deepAutoTrack"></select></div><div class="field"><label>Automation target</label><select id="deepAutoParameter"></select></div><div class="field"><label>Interpolation</label><select id="deepAutoInterpolation"><option value="linear">Linear</option><option value="hold">Hold</option><option value="smooth">Smooth</option></select></div><canvas id="deepAutoCanvas" width="560" height="340" aria-label="Deep automation curve editor"></canvas><div id="deepAutoLegend" class="muted"></div><div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:7px"><button id="deepAutoSave" class="primary">Save Rendered Curve</button><button id="deepAutoRemove">Remove Lane</button></div><div id="deepAutoTruth" class="muted" style="margin-top:6px">Automation is baked into rendered waveform audio; source files remain unchanged.</div>`;
    side.appendChild(node);
    const style=document.createElement('style');
    style.textContent=`#deepAutoCanvas{width:100%;height:180px;background:#09060d;border:1px solid #50305d;border-radius:9px;touch-action:none;cursor:crosshair}#deepAutoLegend{font-size:.68rem;margin-top:4px}#deepAutomationCard .field select{width:100%}`;
    document.head.appendChild(style);
    trackSelect().onchange=loadCatalog;
    parameterSelect().onchange=loadLane;
    interpolationSelect().onchange=draw;
    byId('deepAutoSave').onclick=save;
    byId('deepAutoRemove').onclick=removeLane;
    const canvas=byId('deepAutoCanvas');
    canvas.addEventListener('pointerdown',pointerDown);
    canvas.addEventListener('pointermove',pointerMove);
    canvas.addEventListener('pointerup',()=>drag=-1);
    canvas.addEventListener('pointerleave',()=>drag=-1);
    canvas.addEventListener('dblclick',deletePoint);
    syncTracks();
    const tracks=byId('tracks');
    if(tracks){observer=new MutationObserver(()=>syncTracks(false));observer.observe(tracks,{childList:true,subtree:false})}
  }

  async function refreshSession(){
    if(typeof reloadSession==='function')await reloadSession();
    else if(typeof loadProject==='function')await loadProject();
  }

  function syncTracks(forceCatalog=true){
    const s=studio(),select=trackSelect();if(!select)return;
    const previous=select.value;
    select.innerHTML='';
    for(const track of s?.tracks||[]){const option=document.createElement('option');option.value=track.id;option.textContent=`${track.name} · ${track.role}`;select.appendChild(option)}
    if([...select.options].some(option=>option.value===previous))select.value=previous;
    if(forceCatalog||!catalog)loadCatalog();else loadLane();
  }

  async function loadCatalog(){
    const p=project(),trackId=trackSelect()?.value;if(!p||!trackId){catalog=null;populateParameters();return}
    try{
      catalog=await call(`/projects/${encodeURIComponent(p)}/daw/tracks/${encodeURIComponent(trackId)}/automation-catalog`);
      populateParameters();loadLane();
    }catch(error){catalog=null;populateParameters();points=[];draw();say(error.message,false)}
  }

  function populateParameters(){
    const select=parameterSelect();if(!select)return;
    const previous=select.value;select.innerHTML='';
    const grouped=new Map();
    for(const spec of specs()){if(!grouped.has(spec.group))grouped.set(spec.group,[]);grouped.get(spec.group).push(spec)}
    for(const [label,rows] of grouped){const group=document.createElement('optgroup');group.label=label;for(const spec of rows){const option=document.createElement('option');option.value=spec.parameter;option.textContent=spec.label||spec.parameter;group.appendChild(option)}select.appendChild(group)}
    if([...select.options].some(option=>option.value===previous))select.value=previous;
  }

  function loadLane(){
    const track=activeTrack(),parameter=parameterSelect()?.value;
    const lane=track?.automation?.find?.(value=>value.parameter===parameter);
    points=(lane?.points||[]).map(point=>({time:Number(point.time),value:Number(point.value)})).sort((a,b)=>a.time-b.time);
    if(interpolationSelect())interpolationSelect().value=lane?.interpolation||'linear';
    draw();
  }

  function pointFromEvent(event){
    const canvas=byId('deepAutoCanvas'),rect=canvas.getBoundingClientRect(),x=(event.clientX-rect.left)/rect.width,y=(event.clientY-rect.top)/rect.height,[low,high]=bounds(),duration=maxTime();
    return {time:Math.max(0,Math.min(duration,x*duration)),value:Math.max(low,Math.min(high,high-y*(high-low)))};
  }
  function nearest(event){
    const canvas=byId('deepAutoCanvas'),rect=canvas.getBoundingClientRect(),px=(event.clientX-rect.left)/rect.width*canvas.width,py=(event.clientY-rect.top)/rect.height*canvas.height,[low,high]=bounds(),duration=maxTime();
    let best=-1,distance=18;points.forEach((point,index)=>{const x=point.time/duration*canvas.width,y=(high-point.value)/(high-low)*canvas.height,delta=Math.hypot(x-px,y-py);if(delta<distance){distance=delta;best=index}});return best;
  }
  function pointerDown(event){const index=nearest(event);if(index>=0)drag=index;else{points.push(pointFromEvent(event));points.sort((a,b)=>a.time-b.time);drag=nearest(event)}draw();event.target.setPointerCapture?.(event.pointerId)}
  function pointerMove(event){if(drag<0)return;points[drag]=pointFromEvent(event);points.sort((a,b)=>a.time-b.time);drag=nearest(event);draw()}
  function deletePoint(event){const index=nearest(event);if(index>=0){points.splice(index,1);draw()}}

  function draw(){
    const canvas=byId('deepAutoCanvas');if(!canvas)return;
    const context=canvas.getContext('2d'),width=canvas.width,height=canvas.height,[low,high]=bounds(),duration=maxTime(),spec=activeSpec();
    context.clearRect(0,0,width,height);context.fillStyle='#09060d';context.fillRect(0,0,width,height);context.strokeStyle='#3a2943';context.lineWidth=1;
    for(let index=0;index<=8;index++){const x=index/8*width;context.beginPath();context.moveTo(x,0);context.lineTo(x,height);context.stroke()}
    for(let index=0;index<=4;index++){const y=index/4*height;context.beginPath();context.moveTo(0,y);context.lineTo(width,y);context.stroke()}
    if(points.length){context.strokeStyle='#e9be61';context.lineWidth=3;context.beginPath();points.forEach((point,index)=>{const x=point.time/duration*width,y=(high-point.value)/(high-low)*height;index?context.lineTo(x,y):context.moveTo(x,y)});context.stroke();for(const point of points){const x=point.time/duration*width,y=(high-point.value)/(high-low)*height;context.fillStyle='#ef4aa8';context.beginPath();context.arc(x,y,6,0,Math.PI*2);context.fill()}}
    const legend=byId('deepAutoLegend');if(legend)legend.textContent=`${spec?.label||'Automation'} · ${low} → ${high} ${spec?.unit||''} · 0 → ${duration.toFixed(2)} s · ${points.length} point${points.length===1?'':'s'} · ${interpolationSelect()?.value||'linear'}`;
  }

  async function save(){
    const p=project(),trackId=trackSelect()?.value,parameter=parameterSelect()?.value;if(!p||!trackId||!parameter)return say('Choose a DAW track and automation target first.',false);
    if(!points.length)return say('Add at least one automation point, or use Remove Lane.',false);
    try{
      const response=await call(`/projects/${encodeURIComponent(p)}/daw/tracks/${encodeURIComponent(trackId)}/automation-v2`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({parameter,interpolation:interpolationSelect()?.value||'linear',points})});
      await refreshSession();syncTracks(false);say(`Deep automation saved · ${response.lane?.parameter||parameter} · rendered into real audio.`);
    }catch(error){say(error.message,false)}
  }

  async function removeLane(){
    const p=project(),trackId=trackSelect()?.value,parameter=parameterSelect()?.value;if(!p||!trackId||!parameter)return say('Choose an automation lane first.',false);
    try{
      await call(`/projects/${encodeURIComponent(p)}/daw/tracks/${encodeURIComponent(trackId)}/automation-v2?parameter=${encodeURIComponent(parameter)}`,{method:'DELETE'});
      points=[];await refreshSession();syncTracks(false);say(`Automation removed · ${parameter}. Source audio remains unchanged.`);
    }catch(error){say(error.message,false)}
  }

  function boot(){build()}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>setTimeout(boot,0));else setTimeout(boot,0);
})();
"""


def enhance_daw_mixer_javascript(base: str) -> str:
    """Append the deep automation UI exactly once to the existing DAW mixer script."""
    text = str(base or "")
    if DEEP_DAW_AUTOMATION_MARKER in text:
        return text
    return text.rstrip() + "\n\n" + DEEP_DAW_AUTOMATION_JS.strip() + "\n"


__all__ = ["DEEP_DAW_AUTOMATION_JS", "DEEP_DAW_AUTOMATION_MARKER", "enhance_daw_mixer_javascript"]
