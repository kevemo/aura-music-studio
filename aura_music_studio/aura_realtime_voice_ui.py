from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter(include_in_schema=False)

REALTIME_VOICE_SCRIPT = r"""
(()=>{
  const api='/aura-intelligence/api';
  const realtimeUrl='https://api.openai.com/v1/realtime/calls';
  let pc=null,micStream=null,remoteAudio=null,events=null,active=false,connecting=false,legacyClick=null;

  function toast(message,bad=false){try{note(message,bad)}catch(_){console[bad?'error':'log'](message)}}
  function hostState(state,detail={}){try{window.AuraHost?.setState(state,detail)}catch(_){}}
  function button(){return document.getElementById('auraHandsFree')}
  function setButton(label){const b=button();if(b)b.textContent=label}

  function cleanupRealtime(){
    active=false;connecting=false;
    if(events){try{events.close()}catch(_){}events=null}
    if(pc){try{pc.close()}catch(_){}pc=null}
    if(micStream){for(const track of micStream.getTracks()){try{track.stop()}catch(_){}}micStream=null}
    if(remoteAudio){try{remoteAudio.pause()}catch(_){}remoteAudio.srcObject=null;remoteAudio.remove();remoteAudio=null}
    setButton('◉ Aura Voice');hostState('idle');
  }

  function fallback(reason){
    cleanupRealtime();
    if(reason)toast(reason);
    const b=button();
    if(typeof legacyClick==='function'&&b){legacyClick.call(b)}
    else toast('Aura Voice fallback is unavailable in this browser.',true);
  }

  function handleEvent(raw){
    let event;try{event=JSON.parse(raw)}catch(_){return}
    const type=String(event?.type||'');
    if(type==='input_audio_buffer.speech_started')hostState('listening',{transport:'webrtc'});
    else if(type==='input_audio_buffer.speech_stopped')hostState('thinking',{transport:'webrtc'});
    else if(type==='response.created')hostState('thinking',{transport:'webrtc'});
    else if(type==='response.done')hostState('listening',{transport:'webrtc'});
    else if(type==='error'){
      const message=String(event?.error?.message||'Aura Realtime Voice reported an error.');
      toast(message,true);hostState('warning',{transport:'webrtc',error:message});
    }
  }

  async function startRealtime(){
    if(active||connecting){cleanupRealtime();toast('Aura Realtime Voice stopped.');return}
    if(typeof current==='undefined'||!current)return fallback('Open an Aura conversation first.');
    if(typeof RTCPeerConnection==='undefined'||!navigator.mediaDevices?.getUserMedia)return fallback('Realtime WebRTC is unavailable; using standard Aura Voice.');
    connecting=true;setButton('… Connecting Aura Voice');hostState('thinking',{phase:'realtime_connect'});
    try{
      const statusResponse=await fetch(`${api}/realtime-voice/status`,{credentials:'same-origin'});
      const status=await statusResponse.json();
      if(!statusResponse.ok)throw new Error(status.detail||'Aura Realtime Voice status is unavailable.');
      if(!status.available_to_plan)throw new Error('Aura Realtime Voice is not enabled for this membership.');
      if(!status.configured)return fallback('Realtime voice is not configured on this host; using standard Aura Voice.');

      const secretResponse=await fetch(`${api}/threads/${encodeURIComponent(current)}/realtime-client-secret`,{
        method:'POST',credentials:'same-origin',headers:{'Accept':'application/json'}
      });
      const secretBody=await secretResponse.json();
      if(!secretResponse.ok)throw new Error(secretBody.detail||'Aura Realtime Voice session could not be created.');
      const ephemeralKey=String(secretBody.client_secret||'');
      if(!ephemeralKey)throw new Error('Aura Realtime Voice returned no short-lived client credential.');

      micStream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
      pc=new RTCPeerConnection();
      remoteAudio=document.createElement('audio');remoteAudio.autoplay=true;remoteAudio.playsInline=true;remoteAudio.hidden=true;document.body.appendChild(remoteAudio);
      pc.ontrack=(event)=>{remoteAudio.srcObject=event.streams[0];hostState('speaking',{transport:'webrtc'})};
      pc.onconnectionstatechange=()=>{
        const state=pc?.connectionState;
        if(state==='failed'||state==='closed'||state==='disconnected')cleanupRealtime();
      };
      for(const track of micStream.getAudioTracks())pc.addTrack(track,micStream);
      events=pc.createDataChannel('oai-events');
      events.addEventListener('message',(event)=>handleEvent(event.data));
      events.addEventListener('open',()=>{active=true;connecting=false;setButton('■ Stop Aura Voice');hostState('listening',{transport:'webrtc'});toast('Aura Realtime Voice connected.');});

      const offer=await pc.createOffer();
      await pc.setLocalDescription(offer);
      const sdpResponse=await fetch(realtimeUrl,{
        method:'POST',body:offer.sdp,
        headers:{'Authorization':`Bearer ${ephemeralKey}`,'Content-Type':'application/sdp'}
      });
      if(!sdpResponse.ok)throw new Error(`Realtime WebRTC negotiation failed (${sdpResponse.status}).`);
      const answer={type:'answer',sdp:await sdpResponse.text()};
      await pc.setRemoteDescription(answer);
    }catch(error){
      const message=String(error?.message||'Aura Realtime Voice is unavailable.');
      if(message.includes('not configured'))return fallback(message);
      cleanupRealtime();toast(message,true);hostState('warning',{transport:'webrtc',error:message});
    }
  }

  function install(){
    const b=button();if(!b||b.dataset.auraRealtimeBound==='1')return;
    legacyClick=b.onclick;b.onclick=startRealtime;b.dataset.auraRealtimeBound='1';
    b.title='Realtime speech-to-speech Aura Voice when available; standard Aura Voice fallback otherwise';
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&(active||connecting))cleanupRealtime()});
  window.addEventListener('beforeunload',cleanupRealtime);
})();
"""


@router.get('/aura-intelligence/realtime-voice.js')
def realtime_voice_script():
    return Response(
        content=REALTIME_VOICE_SCRIPT,
        media_type='application/javascript',
        headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'},
    )


__all__ = ['router', 'REALTIME_VOICE_SCRIPT']
