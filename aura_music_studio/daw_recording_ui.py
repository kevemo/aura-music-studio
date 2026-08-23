from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter(tags=["Aura DAW Recording UI"])

JAVASCRIPT = r"""
(() => {
  let recStream = null, rec = null, recChunks = [], recBlob = null;
  let recCtx = null, recSource = null, recMonitorNodes = [], recMeter = null, recRAF = null;
  let recStarted = 0, recTimer = null, recAutoStop = null, recBusy = false;

  const style = document.createElement('style');
  style.textContent = `
    #dawRecordCard .rec-row{display:grid;grid-template-columns:1fr 1fr;gap:7px}
    #dawRecordCard .meter{height:10px;background:#2b1735;border-radius:999px;overflow:hidden;margin:8px 0}
    #dawRecordCard .meter i{display:block;height:100%;width:0;background:linear-gradient(90deg,#6f2aa8,#d12a9f,#e7b953)}
    #dawRecordCard .rec-live{color:#ff8298;font-weight:900}#dawRecordCard .rec-ok{color:#78dda1}
    #dawRecordCard .rec-clock{font-size:1.25rem;color:#f3cd77;font-weight:900;text-align:center;margin:7px 0}
    #dawRecordCard .small-check{display:flex;gap:7px;align-items:center;font-size:.73rem;color:#c9bad1}
    #dawRecordCard .small-check input{width:auto}
  `;
  document.head.appendChild(style);

  function el(id){ return document.getElementById(id); }
  function isPro(){ return !!(window.FLAGS ? window.FLAGS.multitrack : (typeof FLAGS !== 'undefined' && FLAGS.multitrack)); }
  function project(){ return typeof currentProject !== 'undefined' ? currentProject : null; }
  function sess(){ return typeof session !== 'undefined' ? session : null; }
  function playTime(){ return typeof playheadTime !== 'undefined' ? playheadTime : 0; }
  function selected(){ return typeof selectedTrack !== 'undefined' ? selectedTrack : null; }
  function apiCall(url,opt={}){ return typeof api === 'function' ? api(url,opt) : fetch(url,{credentials:'same-origin',...opt}).then(async r=>{const b=await r.json();if(!r.ok)throw new Error(b.detail||JSON.stringify(b));return b}); }
  function say(text, ok=true){ const node=el('dawRecState'); if(node){node.textContent=text;node.className=ok?'rec-ok':'rec-live';} if(typeof status==='function')status(text,ok); }
  function mime(){ for(const m of ['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4','audio/webm']) if(window.MediaRecorder && MediaRecorder.isTypeSupported(m)) return m; return ''; }
  function ext(){ const t=(recBlob&&recBlob.type)||''; return t.includes('ogg')?'.ogg':t.includes('mp4')?'.m4a':'.webm'; }
  function secondsLabel(v){ const n=Math.max(0,Number(v)||0),m=Math.floor(n/60),s=n-m*60;return `${m}:${s.toFixed(2).padStart(5,'0')}`; }

  function buildPanel(){
    const side=document.querySelector('.side'); if(!side || el('dawRecordCard')) return;
    const card=document.createElement('div'); card.className='card'; card.id='dawRecordCard';
    card.innerHTML=`<h3>🎙 Record into Timeline</h3>
      <p class="muted">Dry capture goes to the project. Monitoring FX are heard only while recording and are never printed into the take. Use headphones when monitoring or playing the bounce.</p>
      <div class="field"><label>Input</label><select id="dawRecDevice"></select></div>
      <div class="rec-row"><div class="field"><label>Role</label><select id="dawRecRole"><option value="vocals">Lead vocal</option><option value="backing_vocals">Backing vocal</option><option value="guitar">Guitar</option><option value="bass">Bass</option><option value="drums">Drums</option><option value="piano">Piano</option><option value="keyboard">Keyboard</option><option value="synth">Synth</option><option value="strings">Strings</option><option value="other">Other</option></select></div><div class="field"><label>Track</label><select id="dawRecTrack"><option value="">New / Base lane</option></select></div></div>
      <div class="field"><label>Take name</label><input id="dawRecTitle" value="DAW Take"></div>
      <div class="rec-row"><div class="field"><label>Mode</label><select id="dawRecMode"><option value="append">Record at playhead</option>${isPro()?'<option value="new_take">New take lane</option><option value="punch">Punch region</option>':''}</select></div><div class="field"><label>Count-in</label><select id="dawRecCount"><option value="0">0 beats</option><option value="2">2 beats</option><option value="4" selected>4 beats</option><option value="8">8 beats</option></select></div></div>
      <div class="rec-row"><div class="field"><label>Punch start</label><input id="dawPunchStart" type="number" step="0.001" value="0"></div><div class="field"><label>Punch end</label><input id="dawPunchEnd" type="number" step="0.001" value="4"></div></div>
      ${isPro()?'<div class="field"><label>Loop passes (Pro)</label><input id="dawLoopPasses" type="number" min="2" max="8" value="3"></div>':''}
      <div class="field"><label>Monitor FX (not recorded)</label><select id="dawMonitorPreset"><option value="off">Off</option><option value="clean">Clean monitor</option><option value="vocal_bright">Vocal Bright</option><option value="guitar_clean">Guitar Clean</option></select></div>
      <label class="small-check"><input id="dawPlayBounce" type="checkbox" checked> Play current DAW bounce while recording</label>
      <div class="meter"><i id="dawRecLevel"></i></div><div id="dawRecClock" class="rec-clock">00:00.0</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap"><button id="dawRecEnable">Enable Input</button><button id="dawRecStart" class="primary" disabled>● Record</button><button id="dawRecStop" class="danger" disabled>■ Stop</button>${isPro()?'<button id="dawRecLoop" disabled>⟳ Record Loop Takes</button>':''}</div>
      <p id="dawRecState" class="muted">Input not enabled.</p>`;
    side.prepend(card);
    el('dawRecEnable').onclick=enableInput; el('dawRecStart').onclick=()=>recordOne(false); el('dawRecStop').onclick=stopCapture;
    if(el('dawRecLoop')) el('dawRecLoop').onclick=recordLoopPasses;
    el('dawMonitorPreset').onchange=rebuildMonitor;
    el('dawRecMode').onchange=syncPunchFromSession;
    refreshTrackList(); syncPunchFromSession();
  }

  async function refreshDevices(){
    if(!navigator.mediaDevices?.enumerateDevices) return;
    const rows=await navigator.mediaDevices.enumerateDevices(), sel=el('dawRecDevice'), old=sel?.value||''; if(!sel)return;
    sel.innerHTML=''; rows.filter(x=>x.kind==='audioinput').forEach(d=>{const o=document.createElement('option');o.value=d.deviceId;o.textContent=d.label||'Audio input';sel.appendChild(o)});
    if([...sel.options].some(o=>o.value===old))sel.value=old;
  }

  function refreshTrackList(){
    const sel=el('dawRecTrack'); if(!sel)return; const old=sel.value; sel.innerHTML='<option value="">New / Base lane</option>';
    const s=sess(); if(s) s.tracks.filter(t=>t.role!=='master').forEach(t=>{const o=document.createElement('option');o.value=t.id;o.textContent=`${t.name} · ${t.role}`;sel.appendChild(o)});
    const chosen=selected(); if(chosen && [...sel.options].some(o=>o.value===chosen.id))sel.value=chosen.id; else if([...sel.options].some(o=>o.value===old))sel.value=old;
  }

  function syncPunchFromSession(){
    const s=sess(); if(!s)return; const start=s.loop_start!=null?s.loop_start:playTime(), beat=60/(s.bpm||120), end=s.loop_end!=null?s.loop_end:start+beat*4;
    if(el('dawPunchStart'))el('dawPunchStart').value=start.toFixed(3); if(el('dawPunchEnd'))el('dawPunchEnd').value=end.toFixed(3); refreshTrackList();
  }

  async function enableInput(){
    try{
      if(recStream)recStream.getTracks().forEach(t=>t.stop());
      const id=el('dawRecDevice').value; recStream=await navigator.mediaDevices.getUserMedia({audio:{deviceId:id?{exact:id}:undefined,echoCancellation:false,noiseSuppression:false,autoGainControl:false,channelCount:2}});
      recCtx=recCtx||new (window.AudioContext||window.webkitAudioContext)(); await recCtx.resume(); recSource=recCtx.createMediaStreamSource(recStream); recMeter=recCtx.createAnalyser();recMeter.fftSize=512;recSource.connect(recMeter);drawMeter();await refreshDevices();rebuildMonitor();
      el('dawRecStart').disabled=false;if(el('dawRecLoop'))el('dawRecLoop').disabled=false;say('Input ready · dry recording path active.');
    }catch(e){say('Input error: '+e.message,false)}
  }

  function disconnectMonitor(){ recMonitorNodes.forEach(n=>{try{n.disconnect()}catch{}});recMonitorNodes=[]; }
  function rebuildMonitor(){
    disconnectMonitor(); if(!recSource||!recCtx)return; const preset=el('dawMonitorPreset')?.value||'off'; if(preset==='off')return;
    let first=null,last=null; const add=node=>{if(last)last.connect(node);else first=node;last=node;recMonitorNodes.push(node)};
    const hp=recCtx.createBiquadFilter();hp.type='highpass';hp.frequency.value=preset==='guitar_clean'?55:75;add(hp);
    if(preset==='vocal_bright'){const presence=recCtx.createBiquadFilter();presence.type='peaking';presence.frequency.value=3800;presence.Q.value=.7;presence.gain.value=2.5;add(presence)}
    if(preset==='guitar_clean'){const comp=recCtx.createDynamicsCompressor();comp.threshold.value=-20;comp.ratio.value=2.2;comp.attack.value=.008;comp.release.value=.12;add(comp)}
    const gain=recCtx.createGain();gain.gain.value=.75;add(gain);recSource.connect(first);last.connect(recCtx.destination);
  }

  function drawMeter(){cancelAnimationFrame(recRAF);if(!recMeter)return;const data=new Uint8Array(recMeter.frequencyBinCount);const tick=()=>{if(!recMeter)return;recMeter.getByteTimeDomainData(data);let sum=0;for(const x of data){const v=(x-128)/128;sum+=v*v}const rms=Math.sqrt(sum/data.length);if(el('dawRecLevel'))el('dawRecLevel').style.width=Math.min(100,rms*330)+'%';recRAF=requestAnimationFrame(tick)};tick()}
  function clickBeat(accent=false){if(!recCtx)return;const o=recCtx.createOscillator(),g=recCtx.createGain();o.frequency.value=accent?1100:760;g.gain.setValueAtTime(.13,recCtx.currentTime);g.gain.exponentialRampToValueAtTime(.001,recCtx.currentTime+.05);o.connect(g);g.connect(recCtx.destination);o.start();o.stop(recCtx.currentTime+.055)}
  async function countIn(){const s=sess(),n=Number(el('dawRecCount').value)||0,bpm=s?.bpm||120,ms=60000/bpm;for(let i=0;i<n;i++){say(`Count-in ${i+1}/${n}`);clickBeat(i===0);await new Promise(r=>setTimeout(r,ms))}}
  function startClock(){recStarted=performance.now();clearInterval(recTimer);recTimer=setInterval(()=>{const v=(performance.now()-recStarted)/1000;if(el('dawRecClock'))el('dawRecClock').textContent=secondsLabel(v)},50)}
  function stopClock(){clearInterval(recTimer);recTimer=null;clearTimeout(recAutoStop);recAutoStop=null}

  async function capture(durationSeconds=null){
    if(!recStream)throw new Error('Enable an input first'); recChunks=[];recBlob=null;const m=mime();rec=m?new MediaRecorder(recStream,{mimeType:m,audioBitsPerSecond:256000}):new MediaRecorder(recStream);
    return new Promise((resolve,reject)=>{rec.ondataavailable=e=>{if(e.data.size)recChunks.push(e.data)};rec.onerror=e=>reject(e.error||new Error('Recorder failed'));rec.onstop=()=>{stopClock();recBlob=new Blob(recChunks,{type:rec.mimeType||'audio/webm'});resolve(recBlob)};rec.start(250);startClock();if(durationSeconds!=null)recAutoStop=setTimeout(()=>{if(rec?.state!=='inactive')rec.stop()},Math.max(50,durationSeconds*1000))})
  }
  function stopCapture(){if(rec&&rec.state!=='inactive')rec.stop()}

  function startBounceAt(time,duration=null){const p=el('mixPlayer');if(!p||!el('dawPlayBounce')?.checked||!p.src)return;try{p.currentTime=Math.max(0,time);p.play();if(duration!=null)setTimeout(()=>p.pause(),duration*1000+80)}catch{}}
  async function uploadTake(blob,mode,start,end=null,pass=null){
    const p=project();if(!p)throw new Error('Open a DAW project first');const form=new FormData();form.append('audio',blob,'capture'+ext());form.append('role',el('dawRecRole').value);form.append('title',(el('dawRecTitle').value||'DAW Take')+(pass?` ${pass}`:''));form.append('timeline_start',String(start));form.append('track_id',el('dawRecTrack').value||'');form.append('mode',mode);if(end!=null)form.append('punch_end',String(end));form.append('notes',pass?`Loop recording pass ${pass}`:'Recorded directly in ESP Visual DAW');form.append('rights_confirmation','I confirm this is my recording or I have permission to use it.');return apiCall(`/projects/${encodeURIComponent(p)}/daw/recording`,{method:'POST',body:form})
  }

  async function recordOne(loopPass=false){
    if(recBusy)return;recBusy=true;try{const mode=el('dawRecMode').value;let start=playTime(),end=null;if(mode==='punch'){start=Number(el('dawPunchStart').value);end=Number(el('dawPunchEnd').value);if(!(end>start))throw new Error('Punch end must be after punch start');if(!el('dawRecTrack').value)throw new Error('Choose the Pro track that should receive the punch take')}
      if(mode==='new_take'&&!el('dawRecTrack').value)throw new Error('Choose the Pro track that should receive the new take');
      el('dawRecStart').disabled=true;el('dawRecStop').disabled=false;await countIn();const duration=mode==='punch'?end-start:null;startBounceAt(start,duration);say(mode==='punch'?`Punch recording ${secondsLabel(start)} → ${secondsLabel(end)}`:'Recording dry input at playhead…');const blob=await capture(duration);el('dawRecStop').disabled=true;say('Normalizing and placing take in timeline…');const result=await uploadTake(blob,mode,start,end);if(typeof reloadSession==='function')await reloadSession();refreshTrackList();say(`Saved ${result.normalized_format} · Take lane ${Number(result.take_lane)+1}`);return result;
    }catch(e){say(e.message||String(e),false)}finally{recBusy=false;el('dawRecStart').disabled=!recStream;el('dawRecStop').disabled=true}}

  async function recordLoopPasses(){
    if(recBusy)return; if(!isPro())return say('Loop takes require Pro.',false);const start=Number(el('dawPunchStart').value),end=Number(el('dawPunchEnd').value),passes=Math.max(2,Math.min(8,Number(el('dawLoopPasses').value)||3));if(!(end>start))return say('Set a valid loop/punch range first.',false);if(!el('dawRecTrack').value)return say('Choose the Pro track that should receive loop takes.',false);recBusy=true;
    try{el('dawRecStart').disabled=true;el('dawRecLoop').disabled=true;for(let i=1;i<=passes;i++){say(`Loop pass ${i}/${passes} · count-in`);await countIn();startBounceAt(start,end-start);say(`Recording loop pass ${i}/${passes}`);const blob=await capture(end-start);say(`Saving loop pass ${i}/${passes}…`);await uploadTake(blob,'punch',start,end,i)}if(typeof reloadSession==='function')await reloadSession();refreshTrackList();say(`${passes} Pro loop takes saved as separate take lanes.`)}catch(e){say(e.message||String(e),false)}finally{recBusy=false;el('dawRecStart').disabled=!recStream;el('dawRecLoop').disabled=!recStream;el('dawRecStop').disabled=true}
  }

  const observer=new MutationObserver(()=>{refreshTrackList();syncPunchFromSession()});
  function boot(){buildPanel();refreshDevices().catch(()=>{});const tracks=el('tracks');if(tracks)observer.observe(tracks,{childList:true,subtree:false});window.addEventListener('beforeunload',()=>{disconnectMonitor();if(recStream)recStream.getTracks().forEach(t=>t.stop());cancelAnimationFrame(recRAF)});}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();
"""


@router.get("/daw/recording-ui.js")
def daw_recording_ui():
    return Response(
        content=JAVASCRIPT,
        media_type="application/javascript",
        headers={"Cache-Control": "private, max-age=300"},
    )
