from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter(tags=["Aura Avatar"])

SPEECH_TIMELINE_PROTOCOL = "AuraHost.speechTimeline/v1"
MAX_SPEECH_TIMELINE_FRAMES = 480
MAX_SPEECH_TIMELINE_MS = 120_000

SPEECH_SYNC_SCRIPT = f"""
(()=>{{
  const PROTOCOL='{SPEECH_TIMELINE_PROTOCOL}';
  const MAX_FRAMES={MAX_SPEECH_TIMELINE_FRAMES};
  const MAX_MS={MAX_SPEECH_TIMELINE_MS};
  let generation=0,rafId=null,boundMedia=null,frames=[],cursor=0,listeners=[];

  function host(){{return window.AuraHost?.performance||null}}
  function emit(type,detail={{}}){{document.dispatchEvent(new CustomEvent('aura:performance',{{detail:{{type,protocol:PROTOCOL,...detail}}}}))}}
  function finite(value){{const n=Number(value);return Number.isFinite(n)?n:null}}
  function normalise(input){{
    if(!Array.isArray(input))throw new TypeError('Aura speech timeline frames must be an array');
    if(input.length>MAX_FRAMES)throw new RangeError(`Aura speech timeline exceeds ${{MAX_FRAMES}} frames`);
    let previous=-1;
    return input.map((raw,index)=>{{
      if(!raw||typeof raw!=='object'||Array.isArray(raw))throw new TypeError(`Aura speech timeline frame ${{index}} must be an object`);
      const atMs=finite(raw.at_ms);
      if(atMs===null||atMs<0||atMs>MAX_MS)throw new RangeError(`Aura speech timeline frame ${{index}} has invalid at_ms`);
      if(atMs<previous)throw new RangeError('Aura speech timeline frames must be ordered by at_ms');
      previous=atMs;
      const frame={{at_ms:atMs}};
      if(raw.viseme!=null)frame.viseme=String(raw.viseme).slice(0,32);
      if(raw.gaze!=null)frame.gaze=String(raw.gaze).slice(0,32);
      if(raw.gesture!=null)frame.gesture=String(raw.gesture).slice(0,48);
      for(const key of ['weight','visemeWeight','gazeWeight','gestureWeight','fade','gazeFade','gestureFade']){{
        const value=finite(raw[key]);if(value!==null)frame[key]=value;
      }}
      return frame;
    }});
  }}
  function unbind(){{for(const [target,name,handler] of listeners)target.removeEventListener(name,handler);listeners=[];boundMedia=null}}
  function stopLoop(){{if(rafId!=null)cancelAnimationFrame(rafId);rafId=null}}
  function resetPresentation(){{try{{host()?.speechFrame({{speaking:false,viseme:'sil'}})}}catch(_){{}}}}
  function cancel(reason='cancelled',{{reset=true}}={{}}){{
    generation+=1;stopLoop();unbind();const remaining=Math.max(0,frames.length-cursor);frames=[];cursor=0;if(reset)resetPresentation();emit('speech_timeline_cancelled',{{reason,remaining}});return true;
  }}
  function applyDue(media,run){{
    if(run!==generation||media!==boundMedia)return;
    const elapsed=Math.max(0,Number(media.currentTime||0)*1000);
    while(cursor<frames.length&&frames[cursor].at_ms<=elapsed+12){{
      const frame=frames[cursor++];
      host()?.speechFrame({{...frame,speaking:true}});
      emit('speech_timeline_frame',{{index:cursor-1,at_ms:frame.at_ms,elapsed_ms:Math.round(elapsed)}});
    }}
    if(cursor>=frames.length){{emit('speech_timeline_frames_consumed',{{frame_count:frames.length}});stopLoop();return}}
    if(!media.paused&&!media.ended)rafId=requestAnimationFrame(()=>applyDue(media,run));
  }}
  function bind(target,name,handler){{target.addEventListener(name,handler);listeners.push([target,name,handler])}}
  function playSpeechTimeline(input,options={{}}){{
    const media=options.media;
    if(!(media instanceof HTMLMediaElement))throw new TypeError('Aura speech timeline requires an HTMLMediaElement in options.media');
    const nextFrames=normalise(input);
    cancel('replaced',{{reset:false}});
    const run=++generation;boundMedia=media;frames=nextFrames;cursor=0;
    const play=()=>{{if(run!==generation)return;host()?.speechFrame({{speaking:true}});stopLoop();applyDue(media,run)}};
    const pause=()=>{{if(run!==generation)return;stopLoop();resetPresentation();emit('speech_timeline_paused',{{elapsed_ms:Math.round(Math.max(0,Number(media.currentTime||0)*1000))}})}};
    const finish=()=>{{if(run!==generation)return;stopLoop();resetPresentation();const count=frames.length;unbind();frames=[];cursor=0;emit('speech_timeline_completed',{{frame_count:count}})}};
    const fail=()=>{{if(run!==generation)return;cancel('media_error')}};
    bind(media,'play',play);bind(media,'playing',play);bind(media,'seeking',()=>{{if(run!==generation)return;const elapsed=Math.max(0,Number(media.currentTime||0)*1000);cursor=0;while(cursor<frames.length&&frames[cursor].at_ms<elapsed-12)cursor+=1;stopLoop();if(!media.paused)applyDue(media,run)}});bind(media,'pause',pause);bind(media,'ended',finish);bind(media,'error',fail);bind(media,'abort',fail);bind(media,'emptied',fail);
    emit('speech_timeline_started',{{frame_count:frames.length,duration_ms:frames.length?frames[frames.length-1].at_ms:0,authoritative_timing_required:true}});
    if(!media.paused&&!media.ended)play();
    return{{protocol:PROTOCOL,frame_count:frames.length,cancel:()=>cancel('caller')}};
  }}
  function status(){{return{{protocol:PROTOCOL,active:!!boundMedia,frame_count:frames.length,cursor,pending:Math.max(0,frames.length-cursor),max_frames:MAX_FRAMES,max_timeline_ms:MAX_MS,authoritative_timing_required:true,starts_media_playback:false,fetches_audio:false}}}}
  function install(){{
    const performanceHost=host();
    if(!performanceHost){{setTimeout(install,25);return}}
    performanceHost.playSpeechTimeline=playSpeechTimeline;
    performanceHost.cancelSpeechTimeline=(reason='caller')=>cancel(String(reason).slice(0,80));
    performanceHost.speechTimelineStatus=status;
    document.addEventListener('aura:speech-timeline-input',event=>{{const detail=event?.detail||{{}};try{{playSpeechTimeline(detail.frames,{{media:detail.media}})}}catch(error){{emit('speech_timeline_rejected',{{error:String(error).slice(0,180)}})}}}});
    emit('speech_timeline_ready',status());
  }}
  window.addEventListener('beforeunload',()=>{{generation+=1;stopLoop();unbind()}});
  install();
}})();
"""


@router.get("/aura-intelligence/avatar-speech-sync.js", include_in_schema=False)
def avatar_speech_sync_script():
    return Response(
        content=SPEECH_SYNC_SCRIPT,
        media_type="application/javascript",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


__all__ = [
    "router",
    "SPEECH_SYNC_SCRIPT",
    "SPEECH_TIMELINE_PROTOCOL",
    "MAX_SPEECH_TIMELINE_FRAMES",
    "MAX_SPEECH_TIMELINE_MS",
]
