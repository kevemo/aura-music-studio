from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter(include_in_schema=False)

REALTIME_VOICE_SCRIPT = r"""
(()=>{
  const api='/aura-intelligence/api';
  const realtimeUrl='https://api.openai.com/v1/realtime/calls';
  let pc=null,micStream=null,remoteAudio=null,events=null,active=false,connecting=false,legacyClick=null,transcriptSession=null,realtimeThread=null;
  let activeResponseId=null,remoteSpeaking=false;

  function toast(message,bad=false){try{note(message,bad)}catch(_){console[bad?'error':'log'](message)}}
  function legacyHostState(state,detail={}){try{window.AuraHost?.setState(state,detail)}catch(_){}}
  function turnHost(){return window.RhiannonTurnHost}
  function turnState(state,detail={}){
    const host=turnHost();if(host?.transition)return host.transition(state,detail);
    const fallback={idle:'idle',ready:'welcoming',listening:'listening',processing:'thinking',thinking:'thinking',responding:'thinking',speaking:'speaking',interrupted:'warning',awaiting_permission:'warning',degraded:'warning',error:'warning',recovery:'thinking'};
    legacyHostState(fallback[state]||'idle',{...detail,rhiannon_turn_state:state});return true
  }
  function beginSpeech(jobId,detail={}){const host=turnHost();if(host?.beginSpeech)return host.beginSpeech(jobId,detail);turnState('speaking',{...detail,job_id:jobId});return true}
  function finishSpeech(jobId,next='listening',detail={}){const host=turnHost();if(host?.finishSpeech)return host.finishSpeech(jobId,next,detail);turnState(next,{...detail,job_id:jobId});return true}
  function interruptTurn(reason='user_barge_in',next='listening',detail={}){const host=turnHost();if(host?.interrupt)return host.interrupt(reason,next,detail);turnState('interrupted',{...detail,reason});if(next)turnState(next,{...detail,reason:`${reason}:resume`});return null}
  function button(){return document.getElementById('auraHandsFree')}
  function setButton(label){const b=button();if(b)b.textContent=label}
  function responseId(event){return String(event?.response?.id||event?.response_id||'').trim().slice(0,160)}
  function sendRealtimeEvent(payload){if(!events||events.readyState!=='open')return false;try{events.send(JSON.stringify(payload));return true}catch(_){return false}}

  function cleanupRealtime(finalState='idle'){
    const response=activeResponseId;
    active=false;connecting=false;transcriptSession=null;realtimeThread=null;activeResponseId=null;remoteSpeaking=false;
    if(response)interruptTurn('realtime_session_stopped',finalState,{response_id:response});else turnState(finalState,{reason:'realtime_session_stopped'});
    if(events){try{events.close()}catch(_){}events=null}
    if(pc){try{pc.close()}catch(_){}pc=null}
    if(micStream){for(const track of micStream.getTracks()){try{track.stop()}catch(_){}}micStream=null}
    if(remoteAudio){try{remoteAudio.pause()}catch(_){}remoteAudio.srcObject=null;remoteAudio.remove();remoteAudio=null}
    setButton('◉ Rhiannon Voice');
  }

  function fallback(reason){
    cleanupRealtime('degraded');
    if(reason)toast(reason);
    const b=button();
    if(typeof legacyClick==='function'&&b){legacyClick.call(b)}
    else toast('Rhiannon Voice fallback is unavailable in this browser.',true);
  }

  async function syncTranscript(event){
    if(!transcriptSession||!realtimeThread)return;
    const type=String(event?.type||'');
    const supported=new Set([
      'conversation.item.input_audio_transcription.completed',
      'response.output_audio_transcript.done',
      'response.audio_transcript.done'
    ]);
    if(!supported.has(type))return;
    const transcript=String(event?.transcript||'').trim();
    const eventId=String(event?.event_id||event?.item_id||event?.response_id||'').trim();
    if(!transcript||!eventId)return;
    try{
      const response=await fetch(`${api}/threads/${encodeURIComponent(realtimeThread)}/realtime-transcript`,{
        method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','Accept':'application/json'},
        body:JSON.stringify({transcript_session_id:transcriptSession,event_id:eventId,event_type:type,transcript})
      });
      if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(body.detail||'Realtime transcript could not be saved.')}
      try{if(typeof loadMessages==='function')await loadMessages(realtimeThread)}catch(_){}
    }catch(error){toast(String(error?.message||'Realtime transcript could not be saved.'),true)}
  }

  function cancelActiveResponse(reason='user_barge_in'){
    if(!activeResponseId&&!remoteSpeaking)return false;
    sendRealtimeEvent({type:'response.cancel'});
    const interrupted=activeResponseId;
    interruptTurn(reason,'listening',{transport:'webrtc',response_id:interrupted});
    activeResponseId=null;remoteSpeaking=false;
    setButton('■ Stop Rhiannon Voice');
    return true
  }

  function handleEvent(raw){
    let event;try{event=JSON.parse(raw)}catch(_){return}
    const type=String(event?.type||'');
    if(type==='input_audio_buffer.speech_started'){
      if(activeResponseId||remoteSpeaking)cancelActiveResponse('user_barge_in');
      turnState('listening',{transport:'webrtc',phase:'speech_started'});
    } else if(type==='input_audio_buffer.speech_stopped'){
      turnState('processing',{transport:'webrtc',phase:'speech_stopped'});
    } else if(type==='response.created'){
      activeResponseId=responseId(event)||`realtime_${String(event?.event_id||Date.now()).slice(0,120)}`;
      turnState('responding',{transport:'webrtc',response_id:activeResponseId});
    } else if(type==='response.output_audio.delta'||type==='response.audio.delta'){
      const jobId=activeResponseId||responseId(event);
      if(jobId&&!remoteSpeaking){beginSpeech(jobId,{transport:'webrtc',response_id:jobId});remoteSpeaking=true;setButton('■ Interrupt Rhiannon')}
    } else if(type==='response.done'){
      const jobId=activeResponseId||responseId(event);
      if(jobId&&turnHost()?.acceptsSpeechJob?.(jobId))finishSpeech(jobId,'listening',{transport:'webrtc',response_id:jobId});
      else turnState('listening',{transport:'webrtc',reason:'response_done'});
      activeResponseId=null;remoteSpeaking=false;setButton('■ Stop Rhiannon Voice');
    } else if(type==='error'){
      const message=String(event?.error?.message||'Rhiannon Realtime Voice reported an error.');
      toast(message,true);activeResponseId=null;remoteSpeaking=false;turnState('error',{transport:'webrtc',error:message,reason:'realtime_error'});
    }
    void syncTranscript(event);
  }

  async function startRealtime(){
    if(active||connecting){
      if(active&&cancelActiveResponse('manual_interruption'))return;
      cleanupRealtime();toast('Rhiannon Realtime Voice stopped.');return
    }
    if(typeof current==='undefined'||!current)return fallback('Open a Rhiannon conversation first.');
    if(typeof RTCPeerConnection==='undefined'||!navigator.mediaDevices?.getUserMedia)return fallback('Realtime WebRTC is unavailable; using standard Rhiannon Voice.');
    connecting=true;setButton('… Connecting Rhiannon Voice');
    const snapshot=turnHost()?.snapshot?.();
    if(snapshot?.state==='error'||snapshot?.state==='degraded'||snapshot?.state==='awaiting_permission')turnState('recovery',{reason:'realtime_restart',transport:'webrtc'});
    turnState('ready',{reason:'voice_start_requested',transport:'webrtc'});turnState('processing',{phase:'realtime_connect',transport:'webrtc'});
    try{
      const statusResponse=await fetch(`${api}/realtime-voice/status`,{credentials:'same-origin'});
      const status=await statusResponse.json();
      if(!statusResponse.ok)throw new Error(status.detail||'Rhiannon Realtime Voice status is unavailable.');
      if(!status.available_to_plan)throw new Error('Rhiannon Realtime Voice is not enabled for this membership.');
      if(!status.configured)return fallback('Realtime voice is not configured on this host; using standard Rhiannon Voice.');

      realtimeThread=String(current);
      const secretResponse=await fetch(`${api}/threads/${encodeURIComponent(realtimeThread)}/realtime-client-secret`,{
        method:'POST',credentials:'same-origin',headers:{'Accept':'application/json'}
      });
      const secretBody=await secretResponse.json();
      if(!secretResponse.ok)throw new Error(secretBody.detail||'Rhiannon Realtime Voice session could not be created.');
      const ephemeralKey=String(secretBody.client_secret||'');
      transcriptSession=String(secretBody.transcript_session_id||'');
      if(!ephemeralKey)throw new Error('Rhiannon Realtime Voice returned no short-lived client credential.');
      if(!transcriptSession)throw new Error('Rhiannon Realtime Voice returned no transcript session.');

      turnState('awaiting_permission',{transport:'webrtc',reason:'microphone_permission'});
      micStream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
      turnState('recovery',{transport:'webrtc',reason:'microphone_permission_granted'});
      turnState('ready',{transport:'webrtc',reason:'transport_setup'});
      pc=new RTCPeerConnection();
      remoteAudio=document.createElement('audio');remoteAudio.autoplay=true;remoteAudio.playsInline=true;remoteAudio.hidden=true;document.body.appendChild(remoteAudio);
      pc.ontrack=(event)=>{remoteAudio.srcObject=event.streams[0];if(activeResponseId&&!remoteSpeaking){beginSpeech(activeResponseId,{transport:'webrtc',response_id:activeResponseId});remoteSpeaking=true;setButton('■ Interrupt Rhiannon')}};
      pc.onconnectionstatechange=()=>{
        const state=pc?.connectionState;
        if(state==='failed'){turnState('error',{transport:'webrtc',reason:'peer_connection_failed'});cleanupRealtime('error')}
        else if(state==='closed'||state==='disconnected')cleanupRealtime('degraded')
      };
      for(const track of micStream.getAudioTracks())pc.addTrack(track,micStream);
      events=pc.createDataChannel('oai-events');
      events.addEventListener('message',(event)=>handleEvent(event.data));
      events.addEventListener('open',()=>{active=true;connecting=false;setButton('■ Stop Rhiannon Voice');turnState('listening',{transport:'webrtc',reason:'realtime_connected'});toast('Rhiannon Realtime Voice connected.')});

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
      const message=String(error?.message||'Rhiannon Realtime Voice is unavailable.');
      if(message.includes('not configured'))return fallback(message);
      cleanupRealtime('error');toast(message,true);turnState('error',{transport:'webrtc',error:message,reason:'realtime_start_failed'});
    }
  }

  function install(){
    const b=button();if(!b||b.dataset.auraRealtimeBound==='1')return;
    legacyClick=b.onclick;b.onclick=startRealtime;b.dataset.auraRealtimeBound='1';
    b.title='Realtime speech-to-speech Rhiannon Voice when available; standard Rhiannon Voice fallback otherwise';
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
