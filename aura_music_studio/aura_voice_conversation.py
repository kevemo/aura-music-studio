from __future__ import annotations

from fastapi import APIRouter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .aura_realtime_voice import router as realtime_voice_router
from .aura_realtime_voice_ui import router as realtime_voice_ui_router
from .rhiannon_turn_state import router as rhiannon_turn_router
from .rhiannon_voice_timing import router as rhiannon_timing_router

router = APIRouter(include_in_schema=False)
router.include_router(realtime_voice_router)
router.include_router(realtime_voice_ui_router)
router.include_router(rhiannon_turn_router)
router.include_router(rhiannon_timing_router)

VOICE_SCRIPT = r"""
(()=>{
  const api='/aura-intelligence/api';
  let enabled=false,stream=null,media=null,audioCtx=null,analyser=null,raf=null,activeAudio=null,starting=false;
  let baselineSamples=[],heardVoice=false,lastVoiceAt=0,startedAt=0,activeSpeechJob=null,activePlaybackResolve=null;

  function toast(message,bad=false){try{note(message,bad)}catch(_){console[bad?'error':'log'](message)}}
  function legacyHostState(state,detail={}){try{window.AuraHost?.setState(state,detail)}catch(_){}}
  function turnHost(){return window.RhiannonTurnHost}
  function timingHost(){return window.RhiannonTimingHost}
  function turnState(state,detail={}){
    const host=turnHost();if(host?.transition)return host.transition(state,detail);
    const fallback={idle:'idle',ready:'welcoming',listening:'listening',processing:'thinking',thinking:'thinking',responding:'thinking',speaking:'speaking',interrupted:'warning',awaiting_permission:'warning',degraded:'warning',error:'warning',recovery:'thinking'};
    legacyHostState(fallback[state]||'idle',{...detail,rhiannon_turn_state:state});return true
  }
  function beginSpeech(jobId,detail={}){const host=turnHost();if(host?.beginSpeech)return host.beginSpeech(jobId,detail);turnState('speaking',{...detail,job_id:jobId});return true}
  function speechFrame(detail={}){const host=turnHost();if(host?.speechFrame)return host.speechFrame(detail);try{window.AuraHost?.performance?.speechFrame(detail)}catch(_){}return true}
  function finishSpeech(jobId,next='listening',detail={}){const host=turnHost();if(host?.finishSpeech)return host.finishSpeech(jobId,next,detail);try{window.AuraHost?.performance?.speechFrame({speaking:false,viseme:'sil',job_id:jobId})}catch(_){}turnState(next,detail);return true}
  function interruptTurn(reason='user_interruption',next='listening',detail={}){const host=turnHost();if(host?.interrupt)return host.interrupt(reason,next,detail);try{window.AuraHost?.performance?.speechFrame({speaking:false,viseme:'sil'})}catch(_){}turnState('interrupted',{...detail,reason});if(next)turnState(next,{...detail,reason:`${reason}:resume`});return null}
  function stopTiming(reason){try{return timingHost()?.stop?.(reason)}catch(_){return false}}
  function button(){return document.getElementById('auraHandsFree')}
  function setButton(){const b=button();if(!b)return;b.textContent=(activeAudio||activeSpeechJob)?'■ Interrupt Rhiannon':(enabled?'■ Stop Rhiannon Voice':'◉ Rhiannon Voice');b.style.borderColor=enabled?'#73e2aa66':''}
  function cleanupCapture(){
    if(raf)cancelAnimationFrame(raf);raf=null;
    if(media&&media.state!=='inactive'){try{media.stop()}catch(_){}}
    if(stream){stream.getTracks().forEach(t=>t.stop());stream=null}
    if(audioCtx){try{audioCtx.close()}catch(_){}}audioCtx=null;analyser=null;media=null;
  }
  function stopVoice(finalState='idle'){
    enabled=false;starting=false;
    const job=activeSpeechJob;
    stopTiming('voice_mode_stopped');
    if(activeAudio){try{activeAudio.pause();activeAudio.currentTime=0}catch(_){}activeAudio=null}
    if(activePlaybackResolve){activePlaybackResolve('stopped');activePlaybackResolve=null}
    if(job)interruptTurn('voice_mode_stopped',finalState,{job_id:job});else turnState(finalState,{reason:'voice_mode_stopped'});
    activeSpeechJob=null;cleanupCapture();setButton();toast('Rhiannon Voice Conversation stopped.');
  }
  async function interruptCurrentSpeech(){
    const job=activeSpeechJob;
    if(!activeAudio&&!job)return false;
    stopTiming('user_interruption');
    if(activeAudio){try{activeAudio.pause();activeAudio.currentTime=0}catch(_){}activeAudio=null}
    if(activePlaybackResolve){activePlaybackResolve('interrupted');activePlaybackResolve=null}
    interruptTurn('user_interruption','listening',{job_id:job});
    activeSpeechJob=null;setButton();toast('Rhiannon stopped speaking. Listening…');
    if(enabled)await listen();
    return true
  }

  function rmsLevel(){
    if(!analyser)return 0;const data=new Uint8Array(analyser.fftSize);analyser.getByteTimeDomainData(data);let total=0;
    for(const value of data){const normalized=(value-128)/128;total+=normalized*normalized}
    return Math.sqrt(total/data.length);
  }

  async function transcribeAndSend(blob){
    if(!enabled||!current)return;
    turnState('processing',{phase:'transcription'});
    const fd=new FormData();fd.append('file',blob,'rhiannon-handsfree.webm');
    try{
      const response=await fetch(`${api}/threads/${encodeURIComponent(current)}/voice-transcribe`,{method:'POST',credentials:'same-origin',body:fd});
      const body=await response.json();if(!response.ok)throw new Error(body.detail||'Voice transcription failed');
      const transcript=String(body.transcript||'').trim();
      if(!transcript){toast('I did not catch speech. Listening again…');return listen()}
      if(!enabled)return;
      turnState('thinking',{phase:'reasoning'});
      turnState('responding',{phase:'assistant_response'});
      await send(transcript);
      if(!enabled)return;
      await speakLatestThenListen();
    }catch(error){turnState('error',{error:String(error),reason:'voice_turn_failed'});toast(error.message,true);stopVoice('error')}
  }

  async function speakLatestThenListen(){
    if(!enabled||!current)return;
    const message=[...(messagesCache||[])].reverse().find(row=>row.role==='assistant'&&row.id&&!String(row.id).startsWith('local_'));
    if(!message){turnState('listening',{reason:'no_assistant_message'});return listen()}
    const jobId=`speech_${String(message.id).slice(0,140)}`;
    try{
      activeSpeechJob=jobId;
      if(!beginSpeech(jobId,{message_id:message.id,reason:'tts_playback_started'}))throw new Error('Rhiannon speech job could not start');
      setButton();
      // This generic TTS endpoint returns completed audio only. The lifecycle is job-bound,
      // but precise phoneme/viseme frames remain unavailable until a timing-capable runtime supplies them.
      speechFrame({speaking:true,source:'tts_lifecycle',message_id:message.id,job_id:jobId});
      activeAudio=new Audio(`${api}/threads/${encodeURIComponent(current)}/messages/${encodeURIComponent(message.id)}/speech`);
      const fallbackStarted=await timingHost()?.startAmplitudeFallback?.(activeAudio,jobId);
      if(fallbackStarted===false)toast('Precise lip timing is unavailable; Rhiannon will speak without phoneme-accurate mouth motion.');
      const outcome=await new Promise((resolve,reject)=>{
        activePlaybackResolve=resolve;
        activeAudio.onended=()=>resolve('ended');
        activeAudio.onerror=()=>reject(new Error('Rhiannon speech output is unavailable'));
        activeAudio.play().catch(reject);
      });
      activePlaybackResolve=null;
      if(outcome==='interrupted'||outcome==='stopped')return;
      stopTiming('speech_finished');
      activeAudio=null;
      finishSpeech(jobId,'listening',{message_id:message.id});
      activeSpeechJob=null;setButton();
      if(enabled)await listen();else turnState('idle',{reason:'voice_disabled_after_speech'});
    }catch(error){
      stopTiming('speech_playback_failed');
      activePlaybackResolve=null;activeAudio=null;
      if(activeSpeechJob===jobId)interruptTurn('speech_playback_failed','error',{job_id:jobId,error:String(error)});
      activeSpeechJob=null;setButton();toast(error.message,true);stopVoice('error')
    }
  }

  async function listen(){
    if(!enabled||starting||controller)return;starting=true;cleanupCapture();setButton();turnState('listening',{reason:'microphone_capture'});
    try{
      stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
      const mime=MediaRecorder.isTypeSupported('audio/webm;codecs=opus')?'audio/webm;codecs=opus':'audio/webm';
      media=new MediaRecorder(stream,{mimeType:mime});const chunks=[];media.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)};
      media.onstop=()=>{const blob=new Blob(chunks,{type:media?.mimeType||mime});cleanupCapture();if(enabled&&heardVoice&&blob.size>500)transcribeAndSend(blob);else if(enabled)setTimeout(()=>listen(),250)};
      audioCtx=new (window.AudioContext||window.webkitAudioContext)();const source=audioCtx.createMediaStreamSource(stream);analyser=audioCtx.createAnalyser();analyser.fftSize=1024;source.connect(analyser);
      baselineSamples=[];heardVoice=false;lastVoiceAt=0;startedAt=performance.now();media.start(250);starting=false;toast('Rhiannon is listening…');
      const monitor=()=>{
        if(!enabled||!media||media.state==='inactive')return;
        const now=performance.now(),level=rmsLevel();
        if(now-startedAt<850){baselineSamples.push(level)}
        const baseline=baselineSamples.length?baselineSamples.reduce((a,b)=>a+b,0)/baselineSamples.length:0.008;
        const threshold=Math.max(0.015,baseline*2.6);
        if(level>threshold){heardVoice=true;lastVoiceAt=now}
        const silence=heardVoice&&lastVoiceAt&&now-lastVoiceAt>1250;
        const noSpeech=!heardVoice&&now-startedAt>12000;
        const maxTurn=now-startedAt>45000;
        if(silence||noSpeech||maxTurn){try{media.stop()}catch(_){}return}
        raf=requestAnimationFrame(monitor);
      };
      raf=requestAnimationFrame(monitor);
    }catch(error){starting=false;cleanupCapture();turnState('awaiting_permission',{error:String(error),reason:'microphone_permission_or_capture_failed'});toast('Microphone access or Rhiannon speech is unavailable.',true);stopVoice('awaiting_permission')}
  }

  async function startVoice(){
    if(enabled){
      if(activeAudio||activeSpeechJob)return interruptCurrentSpeech();
      return stopVoice();
    }
    if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined')return toast('Hands-free voice capture is not supported in this browser.',true);
    if(typeof current==='undefined'||!current)return toast('Open a Rhiannon conversation first.',true);
    try{
      const status=await fetch(`${api}/status`,{credentials:'same-origin'}).then(r=>r.json());
      if(!status.speech?.available_to_plan)throw new Error('Rhiannon Voice is not enabled for this membership.');
      if(!status.speech?.stt_configured)throw new Error('Rhiannon speech-to-text is not configured on this host.');
      if(!status.speech?.tts_configured)throw new Error('Rhiannon text-to-speech is not configured on this host.');
      const snapshot=turnHost()?.snapshot?.();
      if(snapshot?.state==='error'||snapshot?.state==='degraded'||snapshot?.state==='awaiting_permission')turnState('recovery',{reason:'voice_restart'});
      turnState('ready',{reason:'voice_enabled'});
      enabled=true;setButton();await listen();
    }catch(error){enabled=false;setButton();turnState('error',{error:String(error),reason:'voice_start_failed'});toast(error.message||'Rhiannon Voice is unavailable.',true)}
  }

  const foot=document.querySelector('.sideFoot');
  if(foot&&!button()){
    const b=document.createElement('button');b.id='auraHandsFree';b.className='btn';b.textContent='◉ Rhiannon Voice';b.title='Hands-free listen → Rhiannon response → spoken reply loop';b.onclick=startVoice;foot.prepend(b);
  }
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&enabled)stopVoice()});
  window.addEventListener('beforeunload',()=>{enabled=false;stopTiming('page_unload');if(activeSpeechJob)interruptTurn('page_unload',null,{job_id:activeSpeechJob});cleanupCapture()});
})();
"""


@router.get("/aura-intelligence/voice-conversation.js")
def voice_conversation_script():
    return Response(content=VOICE_SCRIPT, media_type="application/javascript", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


class AuraVoiceConversationMiddleware(BaseHTTPMiddleware):
    """Inject the canonical Rhiannon turn, timing, fallback and Realtime voice layers."""

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
        turn_marker = "<script src='/aura-intelligence/rhiannon-turn-state.js'></script>"
        timing_marker = "<script src='/aura-intelligence/rhiannon-voice-timing.js'></script>"
        fallback_marker = "<script src='/aura-intelligence/voice-conversation.js'></script>"
        realtime_marker = "<script src='/aura-intelligence/realtime-voice.js'></script>"
        markers = turn_marker + timing_marker + fallback_marker + realtime_marker
        for marker in (turn_marker, timing_marker, fallback_marker, realtime_marker):
            text = text.replace(marker, "")
        text = text.replace("</body>", markers + "</body>")
        encoded = text.encode("utf-8")
        migrated = Response(content=encoded, status_code=response.status_code, background=response.background)
        raw_headers = [(key, value) for key, value in response.raw_headers if key.lower() != b"content-length"]
        raw_headers.append((b"content-length", str(len(encoded)).encode("ascii")))
        migrated.raw_headers = raw_headers
        return migrated


__all__ = ["router", "AuraVoiceConversationMiddleware", "VOICE_SCRIPT"]