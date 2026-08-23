from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter(tags=["Aura DAW Mixer UI"])

JAVASCRIPT = r"""
(() => {
  const el=id=>document.getElementById(id);
  const isPro=()=>!!(typeof FLAGS!=='undefined'&&FLAGS.multitrack);
  const canAuto=()=>!!(typeof FLAGS!=='undefined'&&FLAGS.automation);
  const proj=()=>typeof currentProject!=='undefined'?currentProject:null;
  const sess=()=>typeof session!=='undefined'?session:null;
  const apiCall=(url,opt={})=>typeof api==='function'?api(url,opt):fetch(url,{credentials:'same-origin',...opt}).then(async r=>{const b=await r.json();if(!r.ok)throw new Error(b.detail||JSON.stringify(b));return b});
  const say=(v,ok=true)=>{if(typeof status==='function')status(typeof v==='string'?v:JSON.stringify(v,null,2),ok)};
  async function refresh(){if(typeof reloadSession==='function')await reloadSession();else if(typeof loadProject==='function')await loadProject();syncAll();}

  let mixerOpen=false, meterCtx=null, meterAnalyser=null, meterRAF=null, autoPoints=[],dragIndex=-1;

  function buildMixer(){
    if(el('dawMixerDock'))return;
    const toggle=document.createElement('button');toggle.id='dawMixerToggle';toggle.textContent='🎛 Mixer';toggle.style.cssText='position:fixed;right:18px;bottom:18px;z-index:160;background:linear-gradient(135deg,#f5d887,#bd8430);color:#180b18;border:0;border-radius:999px;padding:11px 16px;font-weight:950;box-shadow:0 8px 28px #0009';document.body.appendChild(toggle);
    const dock=document.createElement('div');dock.id='dawMixerDock';dock.innerHTML=`<div class="mixdock-head"><b>ESP CHANNEL MIXER</b><span id="dawMasterMeter"><i></i></span><span id="dawMeterText">Master meter</span><button id="dawMixerClose">×</button></div><div id="dawMixerStrips" class="mixdock-strips"></div>`;document.body.appendChild(dock);
    const style=document.createElement('style');style.textContent=`
      #dawMixerDock{position:fixed;left:0;right:0;bottom:0;height:285px;z-index:150;background:#0b0710f5;border-top:1px solid #70447f;transform:translateY(100%);transition:transform .2s ease;box-shadow:0 -14px 40px #000a;padding:8px 12px 12px}#dawMixerDock.open{transform:translateY(0)}
      .mixdock-head{height:35px;display:flex;align-items:center;gap:10px;color:#e9be61}.mixdock-head #dawMixerClose{margin-left:auto;padding:4px 10px}.mixdock-strips{display:flex;gap:8px;overflow-x:auto;height:225px;padding:4px}.channel-strip{min-width:105px;width:105px;background:#17101d;border:1px solid #50305d;border-radius:11px;padding:7px;text-align:center;display:grid;grid-template-rows:34px 1fr 29px 27px;gap:3px}.channel-strip.bus{border-color:#8b5a99;background:#201027}.channel-strip.master{border-color:#d5a94f;background:#241b10}.channel-name{font-size:.68rem;font-weight:900;overflow:hidden;text-overflow:ellipsis}.channel-name small{display:block;color:#ae9bb7;font-weight:600}.channel-controls{display:grid;grid-template-columns:35px 1fr;align-items:center;justify-content:center}.vfader{writing-mode:vertical-lr;direction:rtl;height:120px;width:28px;margin:auto}.panmini{width:100%;font-size:.62rem}.msrow{display:flex;gap:3px}.msrow button{flex:1;padding:3px;font-size:.65rem}.msrow button.on{background:#bb7a31;color:#130916}.faderread{font-size:.62rem;color:#d6c5dc}#dawMasterMeter{display:inline-block;width:150px;height:9px;background:#2c1a35;border-radius:999px;overflow:hidden}#dawMasterMeter i{display:block;height:100%;width:0;background:linear-gradient(90deg,#5bc781,#e7be5f,#ed526c)}
      #graphAutoCanvas{width:100%;height:170px;background:#09060d;border:1px solid #50305d;border-radius:9px;touch-action:none;cursor:crosshair}#graphAutoLegend{font-size:.68rem;color:#bfaec8;margin-top:4px}
    `;document.head.appendChild(style);
    toggle.onclick=()=>setMixer(!mixerOpen);el('dawMixerClose').onclick=()=>setMixer(false);syncMixer();attachMasterMeter();
  }
  function setMixer(open){mixerOpen=open;el('dawMixerDock')?.classList.toggle('open',open);if(el('dawMixerToggle'))el('dawMixerToggle').style.bottom=open?'300px':'18px'}

  function syncMixer(){
    const s=sess(),root=el('dawMixerStrips');if(!s||!root)return;root.innerHTML='';
    for(const track of s.tracks){const strip=document.createElement('div');strip.className='channel-strip '+(track.role==='bus'?'bus':track.role==='master'?'master':'');strip.innerHTML=`<div class="channel-name">${esc(track.name)}<small>${esc(track.role)}${track.frozen?' · frozen':''}</small></div><div class="channel-controls"><input class="vfader" data-kind="fader" type="range" min="-60" max="18" step="0.1" value="${Number(track.volume_db||0)}"><div><input class="panmini" data-kind="pan" type="range" min="-1" max="1" step="0.01" value="${Number(track.pan||0)}"><div class="faderread">${Number(track.volume_db||0).toFixed(1)} dB<br>Pan ${Number(track.pan||0).toFixed(2)}</div></div></div><div class="msrow"><button data-kind="mute" class="${track.mute?'on':''}">M</button><button data-kind="solo" class="${track.solo?'on':''}">S</button></div><div class="faderread">${track.effect_count||0} FX · ${(track.sends||[]).length} sends</div>`;
      strip.querySelector('[data-kind=fader]').oninput=e=>{strip.querySelector('.faderread').innerHTML=`${Number(e.target.value).toFixed(1)} dB<br>Pan ${Number(strip.querySelector('[data-kind=pan]').value).toFixed(2)}`};
      strip.querySelector('[data-kind=pan]').oninput=e=>{strip.querySelector('.faderread').innerHTML=`${Number(strip.querySelector('[data-kind=fader]').value).toFixed(1)} dB<br>Pan ${Number(e.target.value).toFixed(2)}`};
      strip.querySelector('[data-kind=fader]').onchange=e=>setMix(track,{volume_db:Number(e.target.value)});strip.querySelector('[data-kind=pan]').onchange=e=>setMix(track,{pan:Number(e.target.value)});strip.querySelector('[data-kind=mute]').onclick=()=>setMix(track,{mute:!track.mute});strip.querySelector('[data-kind=solo]').onclick=()=>setMix(track,{solo:!track.solo});root.appendChild(strip)}
  }
  async function setMix(track,payload){const p=proj();if(!p)return;try{await apiCall(`/projects/${encodeURIComponent(p)}/daw/tracks/${encodeURIComponent(track.id)}/mix`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});await refresh()}catch(e){say(e.message,false)}}
  function esc(v){return String(v??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]))}

  function attachMasterMeter(){const player=el('mixPlayer');if(!player)return;player.addEventListener('play',()=>{try{if(!meterCtx){meterCtx=new (window.AudioContext||window.webkitAudioContext)();const src=meterCtx.createMediaElementSource(player);meterAnalyser=meterCtx.createAnalyser();meterAnalyser.fftSize=1024;src.connect(meterAnalyser);meterAnalyser.connect(meterCtx.destination)}meterCtx.resume();drawMasterMeter()}catch{}});player.addEventListener('pause',()=>{if(meterRAF)cancelAnimationFrame(meterRAF)})}
  function drawMasterMeter(){if(!meterAnalyser)return;const data=new Uint8Array(meterAnalyser.frequencyBinCount),bar=el('dawMasterMeter')?.querySelector('i'),txt=el('dawMeterText');const tick=()=>{meterAnalyser.getByteTimeDomainData(data);let sum=0,peak=0;for(const x of data){const v=Math.abs((x-128)/128);sum+=v*v;peak=Math.max(peak,v)}const rms=Math.sqrt(sum/data.length),db=rms>0?20*Math.log10(rms):-90;if(bar)bar.style.width=Math.min(100,peak*100)+'%';if(txt)txt.textContent=`${db.toFixed(1)} dBFS RMS`;meterRAF=requestAnimationFrame(tick)};tick()}

  function buildAutomation(){if(!canAuto()||el('graphAutoCard'))return;const side=document.querySelector('.side');if(!side)return;const card=document.createElement('div');card.className='card';card.id='graphAutoCard';card.innerHTML=`<h3>📈 Graphical Automation</h3><p class="muted">Click to add points · drag to move · double-click a point to delete.</p><div class="field"><label>Track</label><select id="graphAutoTrack"></select></div><div class="field"><label>Parameter</label><select id="graphAutoParam"><option value="volume_db">Volume dB</option><option value="pan">Pan</option></select></div><canvas id="graphAutoCanvas" width="560" height="340"></canvas><div id="graphAutoLegend"></div><button id="graphAutoSave" class="primary">Save Curve</button><button id="graphAutoClear">Clear Curve</button>`;side.appendChild(card);el('graphAutoTrack').onchange=loadAutomation;el('graphAutoParam').onchange=loadAutomation;el('graphAutoSave').onclick=saveAutomationCurve;el('graphAutoClear').onclick=()=>{autoPoints=[];drawAutomation()};const c=el('graphAutoCanvas');c.addEventListener('pointerdown',autoDown);c.addEventListener('pointermove',autoMove);c.addEventListener('pointerup',()=>dragIndex=-1);c.addEventListener('pointerleave',()=>dragIndex=-1);c.addEventListener('dblclick',autoDelete);syncAutomationTracks()}
  function syncAutomationTracks(){const s=sess(),sel=el('graphAutoTrack');if(!s||!sel)return;const old=sel.value;sel.innerHTML=s.tracks.map(t=>`<option value="${t.id}">${esc(t.name)} · ${esc(t.role)}</option>`).join('');if([...sel.options].some(o=>o.value===old))sel.value=old;loadAutomation()}
  function range(){return el('graphAutoParam')?.value==='pan'?[-1,1]:[-60,18]}
  function maxTime(){return Math.max(1,Number(sess()?.duration||0),Number(sess()?.loop_end||0))}
  function pointFromEvent(e){const c=el('graphAutoCanvas'),r=c.getBoundingClientRect(),x=(e.clientX-r.left)/r.width,y=(e.clientY-r.top)/r.height,[lo,hi]=range();return{time:Math.max(0,Math.min(maxTime(),x*maxTime())),value:Math.max(lo,Math.min(hi,hi-y*(hi-lo)))}}
  function nearest(e){const c=el('graphAutoCanvas'),r=c.getBoundingClientRect(),px=(e.clientX-r.left)/r.width*c.width,py=(e.clientY-r.top)/r.height*c.height,[lo,hi]=range(),mt=maxTime();let best=-1,dist=18;autoPoints.forEach((p,i)=>{const x=p.time/mt*c.width,y=(hi-p.value)/(hi-lo)*c.height,d=Math.hypot(x-px,y-py);if(d<dist){dist=d;best=i}});return best}
  function autoDown(e){const i=nearest(e);if(i>=0){dragIndex=i}else{autoPoints.push(pointFromEvent(e));autoPoints.sort((a,b)=>a.time-b.time);dragIndex=nearest(e)}drawAutomation();e.target.setPointerCapture?.(e.pointerId)}
  function autoMove(e){if(dragIndex<0)return;autoPoints[dragIndex]=pointFromEvent(e);autoPoints.sort((a,b)=>a.time-b.time);dragIndex=nearest(e);drawAutomation()}
  function autoDelete(e){const i=nearest(e);if(i>=0){autoPoints.splice(i,1);drawAutomation()}}
  function loadAutomation(){const s=sess(),tid=el('graphAutoTrack')?.value,param=el('graphAutoParam')?.value;if(!s||!tid||!param)return;const t=s.tracks.find(x=>x.id===tid),lane=t?.automation?.find(x=>x.parameter===param);autoPoints=(lane?.points||[]).map(p=>({time:Number(p.time),value:Number(p.value)}));drawAutomation()}
  function drawAutomation(){const c=el('graphAutoCanvas');if(!c)return;const ctx=c.getContext('2d'),w=c.width,h=c.height,[lo,hi]=range(),mt=maxTime();ctx.clearRect(0,0,w,h);ctx.fillStyle='#09060d';ctx.fillRect(0,0,w,h);ctx.strokeStyle='#3a2943';ctx.lineWidth=1;for(let i=0;i<=8;i++){const x=i/8*w;ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,h);ctx.stroke()}for(let i=0;i<=4;i++){const y=i/4*h;ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke()}if(autoPoints.length){ctx.strokeStyle='#e9be61';ctx.lineWidth=3;ctx.beginPath();autoPoints.forEach((p,i)=>{const x=p.time/mt*w,y=(hi-p.value)/(hi-lo)*h;i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();for(const p of autoPoints){const x=p.time/mt*w,y=(hi-p.value)/(hi-lo)*h;ctx.fillStyle='#ef4aa8';ctx.beginPath();ctx.arc(x,y,6,0,Math.PI*2);ctx.fill()}}if(el('graphAutoLegend'))el('graphAutoLegend').textContent=`0 → ${mt.toFixed(2)} s · ${lo} → ${hi} · ${autoPoints.length} points`}
  async function saveAutomationCurve(){const p=proj(),tid=el('graphAutoTrack')?.value,param=el('graphAutoParam')?.value;if(!p||!tid)return;try{await apiCall(`/projects/${encodeURIComponent(p)}/daw/tracks/${encodeURIComponent(tid)}/automation`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({parameter:param,points:autoPoints})});await refresh();say('Automation curve saved.')}catch(e){say(e.message,false)}}

  function installShortcuts(){document.addEventListener('keydown',e=>{const tag=(e.target?.tagName||'').toLowerCase();if(['input','textarea','select'].includes(tag)||e.target?.isContentEditable)return;if(e.code==='Space'){e.preventDefault();if(typeof togglePlayback==='function')togglePlayback()}else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();if(typeof renderMix==='function')renderMix()}else if(e.key==='Delete'||e.key==='Backspace'){if(typeof selectedClip!=='undefined'&&selectedClip&&typeof deleteSelected==='function'){e.preventDefault();deleteSelected()}}else if(e.key.toLowerCase()==='s'&&!e.shiftKey){if(typeof splitAtPlayhead==='function')splitAtPlayhead()}else if(e.key==='['){if(typeof setLoopStart==='function')setLoopStart()}else if(e.key===']'){if(typeof setLoopEnd==='function')setLoopEnd()}else if(e.key==='='||e.key==='+'){const z=el('zoom');if(z){z.value=Math.min(Number(z.max),Number(z.value)+10);z.dispatchEvent(new Event('input'))}}else if(e.key==='-'||e.key==='_'){const z=el('zoom');if(z){z.value=Math.max(Number(z.min),Number(z.value)-10);z.dispatchEvent(new Event('input'))}}})}

  function syncAll(){syncMixer();syncAutomationTracks()}
  const observer=new MutationObserver(syncAll);function boot(){buildMixer();buildAutomation();installShortcuts();const tracks=el('tracks');if(tracks)observer.observe(tracks,{childList:true,subtree:false});document.addEventListener('click',e=>{if(e.target.closest?.('.clip'))setTimeout(syncAll,0)})}if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();
"""


@router.get("/daw/mixer-ui.js")
def daw_mixer_ui():
    return Response(content=JAVASCRIPT, media_type="application/javascript", headers={"Cache-Control":"private, max-age=300"})
