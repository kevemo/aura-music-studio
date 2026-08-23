from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter(tags=["Aura DAW Routing UI"])

JAVASCRIPT = r"""
(() => {
  function isPro(){ return !!(typeof FLAGS !== 'undefined' && FLAGS.multitrack); }
  if(!isPro()) return;
  const el=id=>document.getElementById(id);
  const proj=()=>typeof currentProject!=='undefined'?currentProject:null;
  const sess=()=>typeof session!=='undefined'?session:null;
  const apiCall=(url,opt={})=>typeof api==='function'?api(url,opt):fetch(url,{credentials:'same-origin',...opt}).then(async r=>{const b=await r.json();if(!r.ok)throw new Error(b.detail||JSON.stringify(b));return b});
  const say=(v,ok=true)=>{if(typeof status==='function')status(typeof v==='string'?v:JSON.stringify(v,null,2),ok)};
  async function refresh(){if(typeof reloadSession==='function')await reloadSession();else if(typeof loadProject==='function')await loadProject();sync();}

  function build(){
    const side=document.querySelector('.side');if(!side||el('dawRoutingCard'))return;
    const card=document.createElement('div');card.className='card';card.id='dawRoutingCard';card.innerHTML=`
      <h3>🎚 Pro Routing + Freeze</h3><p class="muted">Parallel aux sends, clip crossfades and reversible CPU-saving track freeze.</p>
      <div class="grid2"><div class="field"><label>Bus preset</label><select id="routePreset"><option value="reverb">Aura Reverb</option><option value="delay">Aura Delay</option><option value="clean">Clean Bus</option></select></div><div class="field"><label>Bus name</label><input id="routeBusName" value="Aura Reverb Bus"></div></div>
      <button id="routeCreateBus">+ Create Bus</button><button id="routeDeleteBus" class="danger">Delete Selected Bus</button>
      <div class="field"><label>Source track</label><select id="routeSource"></select></div><div class="field"><label>Destination bus</label><select id="routeBus"></select></div>
      <div class="grid2"><div class="field"><label>Send level dB</label><input id="routeLevel" type="number" min="-60" max="12" step="0.5" value="-18"></div><div class="field"><label>Send active</label><select id="routeEnabled"><option value="true">On</option><option value="false">Off</option></select></div></div>
      <button id="routeSetSend">Set / Update Send</button>
      <hr style="border:0;border-top:1px solid #50305d;margin:12px 0"><div class="field"><label>Track processing</label><select id="routeTrack"></select></div><button id="routeFreeze">❄ Freeze</button><button id="routeThaw">☀ Thaw</button><button id="routeBounce">↧ Bounce Track</button>
      <hr style="border:0;border-top:1px solid #50305d;margin:12px 0"><div class="field"><label>Crossfade from selected clip to</label><select id="routeCrossRight"></select></div><div class="field"><label>Crossfade seconds</label><input id="routeCrossDuration" type="number" min="0.01" max="120" step="0.01" value="0.25"></div><button id="routeCrossfade">Apply Crossfade</button>
      <audio id="routeBouncePlayer" controls style="margin-top:8px"></audio>`;
    side.appendChild(card);
    el('routePreset').onchange=()=>{el('routeBusName').value=el('routePreset').value==='reverb'?'Aura Reverb Bus':el('routePreset').value==='delay'?'Aura Delay Bus':'Aura Clean Bus'};
    el('routeCreateBus').onclick=createBus;el('routeDeleteBus').onclick=deleteBus;el('routeSetSend').onclick=setSend;
    el('routeFreeze').onclick=()=>trackAction('freeze');el('routeThaw').onclick=()=>trackAction('thaw');el('routeBounce').onclick=()=>trackAction('bounce');el('routeCrossfade').onclick=applyCrossfade;sync();
  }

  function sync(){
    const s=sess();if(!s)return;const ordinary=s.tracks.filter(t=>!['master','bus'].includes(t.role)),buses=s.tracks.filter(t=>t.role==='bus');
    const options=ordinary.map(t=>`<option value="${t.id}">${t.name}${t.frozen?' · FROZEN':''}</option>`).join('');
    for(const id of ['routeSource','routeTrack'])if(el(id)){const old=el(id).value;el(id).innerHTML=options;if([...el(id).options].some(o=>o.value===old))el(id).value=old}
    if(el('routeBus')){const old=el('routeBus').value;el('routeBus').innerHTML=buses.map(t=>`<option value="${t.id}">${t.name} · ${t.bus_preset||'clean'}</option>`).join('');if([...el('routeBus').options].some(o=>o.value===old))el('routeBus').value=old}
    if(el('routeCrossRight')){const left=typeof selectedClip!=='undefined'?selectedClip:null;const track=left?s.tracks.find(t=>t.clips.some(c=>c.id===left.id)):null;const rows=track?track.clips.filter(c=>c.kind==='audio'&&c.id!==left.id&&c.take_lane===left.take_lane):[];el('routeCrossRight').innerHTML=rows.map(c=>`<option value="${c.id}">${c.name} @ ${Number(c.start).toFixed(2)}s</option>`).join('')}
  }

  async function createBus(){const p=proj();if(!p)return;try{await apiCall(`/projects/${encodeURIComponent(p)}/daw/buses`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:el('routeBusName').value,preset:el('routePreset').value})});await refresh();say('Auxiliary bus created.')}catch(e){say(e.message,false)}}
  async function deleteBus(){const p=proj(),id=el('routeBus')?.value;if(!p||!id)return say('Choose a bus.',false);try{await apiCall(`/projects/${encodeURIComponent(p)}/daw/buses/${encodeURIComponent(id)}`,{method:'DELETE'});await refresh();say('Bus and its sends removed.')}catch(e){say(e.message,false)}}
  async function setSend(){const p=proj(),track=el('routeSource')?.value,bus=el('routeBus')?.value;if(!p||!track||!bus)return say('Choose a source track and bus.',false);try{await apiCall(`/projects/${encodeURIComponent(p)}/daw/tracks/${encodeURIComponent(track)}/sends`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bus_track_id:bus,level_db:Number(el('routeLevel').value),enabled:el('routeEnabled').value==='true'})});await refresh();say('Parallel send updated.')}catch(e){say(e.message,false)}}
  async function trackAction(action){const p=proj(),track=el('routeTrack')?.value;if(!p||!track)return say('Choose a track.',false);try{const r=await apiCall(`/projects/${encodeURIComponent(p)}/daw/tracks/${encodeURIComponent(track)}/${action}`,{method:'POST'});if(action==='bounce'&&r.stream_url){el('routeBouncePlayer').src=r.stream_url;el('routeBouncePlayer').play().catch(()=>{})}else await refresh();say(r)}catch(e){say(e.message,false)}}
  async function applyCrossfade(){const p=proj(),left=typeof selectedClip!=='undefined'?selectedClip:null,right=el('routeCrossRight')?.value;if(!p||!left||!right)return say('Select the left clip in the timeline, then choose the right clip.',false);try{const r=await apiCall(`/projects/${encodeURIComponent(p)}/daw/crossfade`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({left_clip_id:left.id,right_clip_id:right,duration:Number(el('routeCrossDuration').value)})});await refresh();say(r)}catch(e){say(e.message,false)}}

  const observer=new MutationObserver(sync);function boot(){build();const tracks=el('tracks');if(tracks)observer.observe(tracks,{childList:true,subtree:false});document.addEventListener('click',e=>{if(e.target.closest?.('.clip'))setTimeout(sync,0)})}if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();
"""


@router.get("/daw/routing-ui.js")
def daw_routing_ui():
    return Response(content=JAVASCRIPT, media_type="application/javascript", headers={"Cache-Control":"private, max-age=300"})
