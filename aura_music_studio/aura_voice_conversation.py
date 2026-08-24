from __future__ import annotations

from fastapi import APIRouter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

router = APIRouter(include_in_schema=False)

VOICE_SCRIPT = r"""
(()=>{
  const api='/aura-intelligence/api';
  let enabled=false,stream=null,media=null,audioCtx=null,analyser=null,raf=null,activeAudio=null,starting=false;
  let baselineSamples=[],heardVoice=false,lastVoiceAt=0,startedAt=0;

  function toast(message,bad=false){try{note(message,bad)}catch(_){console[bad?'error':'log'](message)}}
  function hostState(state,detail={}){try{window.AuraHost?.setState(state,detail)}catch(_){}}
  function button(){return document.getElementById('auraHandsFree')}
  function setButton(){const b=button();if(!b)return;b.textContent=enabled?'■ Stop Aura Voice':'◉ Aura Voice';b.style.borderColor=enabled?'#73e2aa66':''}
  function cleanupCapture(){
    if(raf)cancelAnimationFrame(raf);raf=null;
    if(media&&media.state!=='inactive'){try{media.stop()}catch(_){}}
    if(stream){stream.getTracks().forEach(t=>t.stop());stream=null}
    if(audioCtx){try{audioCtx.close()}catch(_){}}audioCtx=null;analyser=null;media=null;
  }
  function stopVoice(){enabled=false;starting=false;if(activeAudio){try{activeAudio.pause()}catch(_){}activeAudio=null}cleanupCapture();setButton();hostState('idle');toast('Aura Voice Conversation stopped.')}

  function rmsLevel(){
    if(!analyser)return 0;const data=new Uint8Array(analyser.fftSize);analyser.getByteTimeDomainData(data);let total=0;
    for(const value of data){const normalized=(value-128)/128;total+=normalized*normalized}
    return Math.sqrt(total/data.length);
  }

  async function transcribeAndSend(blob){
    if(!enabled||!current)return;
    hostState('thinking',{phase:'transcription'});
    const fd=new FormData();fd.append('file',blob,'aura-handsfree.webm');
    try{
      const response=await fetch(`${api}/threads/${encodeURIComponent(current)}/voice-transcribe`,{method:'POST',credentials:'same-origin',body:fd});
      const body=await response.json();if(!response.ok)throw new Error(body.detail||'Voice transcription failed');
      const transcript=String(body.transcript||'').trim();
      if(!transcript){toast('I did not catch speech. Listening again…');return listen()}
      if(!enabled)return;
      hostState('thinking',{phase:'reasoning'});await send(transcript);
      if(!enabled)return;
      await speakLatestThenListen();
    }catch(error){hostState('warning',{error:String(error)});toast(error.message,true);stopVoice()}
  }

  async function speakLatestThenListen(){
    if(!enabled||!current)return;
    const message=[...(messagesCache||[])].reverse().find(row=>row.role==='assistant'&&row.id&&!String(row.id).startsWith('local_'));
    if(!message){return listen()}
    try{
      hostState('speaking',{message_id:message.id});
      activeAudio=new Audio(`${api}/threads/${encodeURIComponent(current)}/messages/${encodeURIComponent(message.id)}/speech`);
      await new Promise((resolve,reject)=>{activeAudio.onended=resolve;activeAudio.onerror=()=>reject(new Error('Aura speech output is unavailable'));activeAudio.play().catch(reject)});
      activeAudio=null;if(enabled)await listen();else hostState('idle');
    }catch(error){activeAudio=null;hostState('warning',{error:String(error)});toast(error.message,true);stopVoice()}
  }

  async function listen(){
    if(!enabled||starting||controller)return;starting=true;cleanupCapture();setButton();hostState('listening');
    try{
      stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
      const mime=MediaRecorder.isTypeSupported('audio/webm;codecs=opus')?'audio/webm;codecs=opus':'audio/webm';
      media=new MediaRecorder(stream,{mimeType:mime});const chunks=[];media.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)};
      media.onstop=()=>{const blob=new Blob(chunks,{type:media?.mimeType||mime});cleanupCapture();if(enabled&&heardVoice&&blob.size>500)transcribeAndSend(blob);else if(enabled)setTimeout(()=>listen(),250)};
      audioCtx=new (window.AudioContext||window.webkitAudioContext)();const source=audioCtx.createMediaStreamSource(stream);analyser=audioCtx.createAnalyser();analyser.fftSize=1024;source.connect(analyser);
      baselineSamples=[];heardVoice=false;lastVoiceAt=0;startedAt=performance.now();media.start(250);starting=false;toast('Aura is listening…');
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
    }catch(error){starting=false;cleanupCapture();hostState('warning',{error:String(error)});toast('Microphone access or Aura speech is unavailable.',true);stopVoice()}
  }

  async function startVoice(){
    if(enabled)return stopVoice();
    if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined')return toast('Hands-free voice capture is not supported in this browser.',true);
    if(typeof current==='undefined'||!current)return toast('Open an Aura conversation first.',true);
    try{
      const status=await fetch(`${api}/status`,{credentials:'same-origin'}).then(r=>r.json());
      if(!status.speech?.available_to_plan)throw new Error('Aura Voice is not enabled for this membership.');
      if(!status.speech?.stt_configured)throw new Error('Aura speech-to-text is not configured on this host.');
      if(!status.speech?.tts_configured)throw new Error('Aura text-to-speech is not configured on this host.');
      enabled=true;setButton();await listen();
    }catch(error){enabled=false;setButton();hostState('warning',{error:String(error)});toast(error.message||'Aura Voice is unavailable.',true)}
  }

  const foot=document.querySelector('.sideFoot');
  if(foot&&!button()){
    const b=document.createElement('button');b.id='auraHandsFree';b.className='btn';b.textContent='◉ Aura Voice';b.title='Hands-free listen → Aura response → spoken reply loop';b.onclick=startVoice;foot.prepend(b);
  }
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&enabled)stopVoice()});
  window.addEventListener('beforeunload',()=>{enabled=false;cleanupCapture()});
})();
"""


@router.get("/aura-intelligence/voice-conversation.js")
def voice_conversation_script():
    return Response(content=VOICE_SCRIPT, media_type="application/javascript", headers={"Cache-Control": "no-store"})


class AuraVoiceConversationMiddleware(BaseHTTPMiddleware):
    """Inject the hands-free voice layer only into Aura's signed-in HTML workspace."""

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
        marker = "<script src='/aura-intelligence/voice-conversation.js'></script>"
        if marker not in text:
            text = text.replace("</body>", marker + "</body>")
        encoded = text.encode("utf-8")
        migrated = Response(content=encoded, status_code=response.status_code, background=response.background)
        raw_headers = [(key, value) for key, value in response.raw_headers if key.lower() != b"content-length"]
        raw_headers.append((b"content-length", str(len(encoded)).encode("ascii")))
        migrated.raw_headers = raw_headers
        return migrated


__all__ = ["router", "AuraVoiceConversationMiddleware"]
