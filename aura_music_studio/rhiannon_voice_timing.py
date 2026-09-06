from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter
from fastapi.responses import Response

from .rhiannon_voice_contracts import SPEECH_TIMING_PROTOCOL, SpeechTimingTrack, TimingKind

router = APIRouter(include_in_schema=False)

_MAX_CLIENT_VISEME_SPANS = 4000
_DURATION_TOLERANCE_MS = 750


def canonical_viseme_schedule(track: SpeechTimingTrack | dict[str, Any]) -> list[dict[str, Any]]:
    """Return the bounded canonical viseme schedule Chat 1 may drive in Rhiannon.

    Chat 1 consumes timing supplied by the owning voice runtime. It never upgrades fallback
    timing into precise phoneme/viseme truth and rejects overlapping viseme intervals so the
    browser renderer has one deterministic mouth shape at each playback-clock position.
    """

    parsed = track if isinstance(track, SpeechTimingTrack) else SpeechTimingTrack.model_validate(track)
    if parsed.precise_timing and parsed.source == "fallback":
        raise ValueError("fallback timing cannot be promoted to precise timing")

    visemes = [span for span in parsed.spans if span.kind == TimingKind.VISEME]
    if len(visemes) > _MAX_CLIENT_VISEME_SPANS:
        raise ValueError("viseme timing exceeds browser consumption budget")

    previous_end = -1
    schedule: list[dict[str, Any]] = []
    for span in visemes:
        if span.start_ms < previous_end:
            raise ValueError("viseme timing spans must not overlap")
        previous_end = span.end_ms
        schedule.append(
            {
                "viseme": span.value,
                "start_ms": span.start_ms,
                "end_ms": span.end_ms,
                "confidence": span.confidence,
                "expression_hint": span.expression_hint,
            }
        )
    return schedule


def timing_consumption_contract() -> dict[str, Any]:
    return {
        "protocol": SPEECH_TIMING_PROTOCOL,
        "playback_clock": "HTMLMediaElement.currentTime",
        "precise_timing_requires_canonical_visemes": True,
        "precise_fallback_allowed": False,
        "stale_job_authority": "RhiannonTurnHost.acceptsSpeechJob",
        "max_client_viseme_spans": _MAX_CLIENT_VISEME_SPANS,
        "duration_tolerance_ms": _DURATION_TOLERANCE_MS,
        "fallback": {
            "mode": "amplitude_fallback",
            "precise_timing": False,
            "phoneme_accurate": False,
            "mouth_shapes": ["sil", "aa"],
        },
        "boundaries": {
            "voice_generation_owned_here": False,
            "timing_generation_owned_here": False,
            "voice_cloning_owned_here": False,
            "arbitrary_animation_code_allowed": False,
        },
    }


_TIMING_SCRIPT_CONTRACT = json.dumps(timing_consumption_contract(), separators=(",", ":"))

RHIANNON_TIMING_SCRIPT = rf"""
(()=>{{
  const contract={_TIMING_SCRIPT_CONTRACT};
  const protocol=contract.protocol;
  const canonicalVisemes=new Set(['sil','pp','ff','th','dd','kk','ch','ss','nn','rr','aa','e','i','o','u']);
  let active=null;

  function turnHost(){{return window.RhiannonTurnHost}}
  function acceptsJob(jobId){{const host=turnHost();return !!host?.acceptsSpeechJob?.(String(jobId||''))}}
  function frame(jobId,detail={{}}){{const host=turnHost();if(!acceptsJob(jobId)||!host?.speechFrame)return false;return host.speechFrame({{...detail,job_id:String(jobId)}})}}
  function emit(type,detail={{}}){{try{{document.dispatchEvent(new CustomEvent('rhiannon:voice-timing',{{detail:{{protocol,type,...detail}}}}))}}catch(_){{}}}}
  function cleanJobId(jobId){{return String(jobId||'').trim().slice(0,160)}}
  function mediaClockMs(audio){{return Math.max(0,Math.round(Number(audio?.currentTime||0)*1000))}}
  function validMedia(audio){{return !!audio&&typeof audio.currentTime==='number'&&typeof audio.addEventListener==='function'}}
  function durationMismatch(audio,track){{const seconds=Number(audio?.duration);if(!Number.isFinite(seconds)||seconds<=0)return false;return Math.abs(seconds*1000-Number(track.audio_duration_ms||0))>contract.duration_tolerance_ms}}

  function stop(reason='stopped',reset=true){{
    const current=active;if(!current)return false;active=null;
    if(current.raf)cancelAnimationFrame(current.raf);
    if(current.endedHandler)try{{current.audio.removeEventListener('ended',current.endedHandler)}}catch(_){{}}
    if(current.context)try{{current.context.close()}}catch(_){{}}
    if(reset&&acceptsJob(current.jobId))frame(current.jobId,{{speaking:true,viseme:'sil',source:'rhiannon_timing_stop',precise_timing:false,lip_sync_mode:'silence',playback_ms:mediaClockMs(current.audio)}});
    emit('stopped',{{reason,job_id:current.jobId,mode:current.mode}});return true
  }}

  function normalizeTrack(input){{
    if(!input||String(input.protocol||'')!==protocol)return null;
    const duration=Number(input.audio_duration_ms);if(!Number.isFinite(duration)||duration<=0||duration>3600000)return null;
    if(input.precise_timing!==true||String(input.source||'')==='fallback')return null;
    const spans=Array.isArray(input.spans)?input.spans:[];const visemes=[];let previousEnd=-1;
    for(const span of spans){{
      if(String(span?.kind||'')!=='viseme')continue;
      const viseme=String(span?.value||'').toLowerCase();const start=Number(span?.start_ms),end=Number(span?.end_ms);
      if(!canonicalVisemes.has(viseme)||!Number.isFinite(start)||!Number.isFinite(end)||start<0||end<start||end>duration||start<previousEnd)return null;
      previousEnd=end;visemes.push({{viseme,start_ms:start,end_ms:end,confidence:span?.confidence??null,expression_hint:span?.expression_hint??null}});
      if(visemes.length>contract.max_client_viseme_spans)return null;
    }}
    if(!visemes.length)return null;
    return {{protocol,audio_duration_ms:duration,precise_timing:true,source:String(input.source||'runtime'),visemes}};
  }}

  function startPrecise(audio,input,jobId){{
    jobId=cleanJobId(jobId);const track=normalizeTrack(input);
    if(!jobId||!validMedia(audio)||!track||!acceptsJob(jobId)){{emit('precise_rejected',{{job_id:jobId||null}});return false}}
    stop('superseded',false);
    if(durationMismatch(audio,track)){{emit('duration_mismatch',{{job_id:jobId,audio_duration_ms:Number(audio.duration)*1000,timing_duration_ms:track.audio_duration_ms}});return false}}
    const state={{mode:'canonical_timing',audio,jobId,track,index:0,raf:null,endedHandler:null,context:null}};active=state;
    const tick=()=>{{
      if(active!==state)return;
      if(!acceptsJob(jobId)){{stop('stale_job',false);return}}
      if(durationMismatch(audio,track)){{stop('duration_mismatch');return}}
      const now=mediaClockMs(audio);while(state.index<track.visemes.length&&now>=track.visemes[state.index].end_ms)state.index+=1;
      const span=track.visemes[state.index];const inSpan=!!span&&now>=span.start_ms&&now<span.end_ms;
      const viseme=inSpan?span.viseme:'sil';
      frame(jobId,{{speaking:true,viseme,source:'canonical_timing',precise_timing:true,lip_sync_mode:'canonical_timing',timing_protocol:protocol,playback_ms:now,confidence:inSpan?span.confidence:null,expression_hint:inSpan?span.expression_hint:null}});
      if(!audio.ended)state.raf=requestAnimationFrame(tick)
    }};
    state.endedHandler=()=>stop('audio_ended');audio.addEventListener('ended',state.endedHandler,{{once:true}});
    state.raf=requestAnimationFrame(tick);emit('started',{{job_id:jobId,mode:state.mode,precise_timing:true}});return true
  }}

  async function startAmplitudeFallback(audio,jobId){{
    jobId=cleanJobId(jobId);
    if(!jobId||!validMedia(audio)||!acceptsJob(jobId)){{emit('fallback_rejected',{{job_id:jobId||null}});return false}}
    stop('superseded',false);
    const AudioContextCtor=window.AudioContext||window.webkitAudioContext;
    if(!AudioContextCtor){{emit('fallback_unavailable',{{job_id:jobId,reason:'web_audio_unavailable'}});return false}}
    let context,source,analyser;
    try{{
      context=new AudioContextCtor();source=context.createMediaElementSource(audio);analyser=context.createAnalyser();analyser.fftSize=512;analyser.smoothingTimeConstant=.65;source.connect(analyser);analyser.connect(context.destination);if(context.state==='suspended')await context.resume();
    }}catch(error){{try{{context?.close()}}catch(_){{}}emit('fallback_unavailable',{{job_id:jobId,reason:'audio_analysis_unavailable'}});return false}}
    const state={{mode:'amplitude_fallback',audio,jobId,track:null,index:0,raf:null,endedHandler:null,context}};active=state;const samples=new Uint8Array(analyser.fftSize);
    const tick=()=>{{
      if(active!==state)return;
      if(!acceptsJob(jobId)){{stop('stale_job',false);return}}
      if(audio.ended){{stop('audio_ended');return}}
      analyser.getByteTimeDomainData(samples);let total=0;for(const value of samples){{const n=(value-128)/128;total+=n*n}}const rms=Math.sqrt(total/samples.length);const viseme=rms>=.035?'aa':'sil';
      frame(jobId,{{speaking:true,viseme,source:'amplitude_fallback',precise_timing:false,lip_sync_mode:'amplitude_fallback',phoneme_accurate:false,playback_ms:mediaClockMs(audio),amplitude:Math.min(1,rms*8)}});
      state.raf=requestAnimationFrame(tick)
    }};
    state.endedHandler=()=>stop('audio_ended');audio.addEventListener('ended',state.endedHandler,{{once:true}});state.raf=requestAnimationFrame(tick);emit('started',{{job_id:jobId,mode:state.mode,precise_timing:false,phoneme_accurate:false}});return true
  }}

  function status(){{return {{protocol,active:active?{{job_id:active.jobId,mode:active.mode}}:null,contract}}}}
  window.RhiannonTimingHost=Object.freeze({{protocol,startPrecise,startAmplitudeFallback,stop,status}});emit('ready',{{contract}})
}})();
""".strip()


@router.get("/aura-intelligence/rhiannon-voice-timing.js")
def rhiannon_voice_timing_script():
    return Response(
        content=RHIANNON_TIMING_SCRIPT,
        media_type="application/javascript",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


__all__ = [
    "router",
    "canonical_viseme_schedule",
    "timing_consumption_contract",
    "RHIANNON_TIMING_SCRIPT",
]
