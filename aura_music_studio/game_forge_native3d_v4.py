from __future__ import annotations

import json

from .game_forge_cinematics import cinematic_runtime_payload
from .game_forge_native3d_v3 import _runtime_payload as _v3_runtime_payload
from .game_forge_native3d_v3 import render_aura3d_playtest as _render_v3


def _runtime_payload(game, world) -> dict:
    payload = _v3_runtime_payload(game, world)
    payload["runtime_version"] = 4
    payload["cinematic"] = cinematic_runtime_payload(game.id)
    payload["runtime_contract"].update(
        {
            "declarative_cinematics": True,
            "cinematic_transform_tracks": True,
            "cinematic_camera_tracks": True,
            "cinematic_subtitles": True,
            "cinematic_fades": True,
            "cinematic_audio_cues": True,
            "built_in_particle_vfx": True,
            "creator_javascript": False,
            "creator_shader_code": False,
            "skeletal_animation": False,
        }
    )
    return payload


def _cinematic_script(game_id: str) -> str:
    cinematic = cinematic_runtime_payload(game_id)
    serialized = json.dumps(cinematic, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f"""
<script id='aura-cinematic-dna' type='application/json'>{serialized}</script>
<div id='aura-cinematic-ui' style='position:fixed;right:12px;top:12px;z-index:7;display:flex;gap:7px' hidden>
  <button id='aura-cinematic-play' style='background:#0c1324e8;color:#fff;border:1px solid #ffffff33;border-radius:9px;padding:8px 10px;font-weight:800;cursor:pointer'>Play cinematic</button>
  <button id='aura-cinematic-stop' style='background:#0c1324e8;color:#fff;border:1px solid #ffffff33;border-radius:9px;padding:8px 10px;font-weight:800;cursor:pointer'>Stop</button>
</div>
<div id='aura-cinematic-subtitle' style='position:fixed;left:50%;bottom:9%;transform:translateX(-50%);z-index:12;max-width:min(900px,88vw);padding:10px 16px;border-radius:10px;background:#050711d9;color:#fff;font:700 18px/1.4 Inter,system-ui,sans-serif;text-align:center;text-shadow:0 2px 10px #000' hidden></div>
<div id='aura-cinematic-fade' style='position:fixed;inset:0;z-index:11;pointer-events:none;background:#000;opacity:0'></div>
<canvas id='aura-vfx-overlay' style='position:fixed;inset:0;z-index:10;pointer-events:none;width:100%;height:100%'></canvas>
<script>
'use strict';
const auraCinematic=JSON.parse(document.getElementById('aura-cinematic-dna').textContent||'null');
const cinematicState={{active:false,startMs:0,camera:null,audioTriggered:new Set(),vfxTriggered:new Set(),particles:[]}};
const cinematicBase=new Map(entities.map(e=>[e.id,{{position:{{...(e.position||{{x:0,y:0,z:0}})}},rotation:{{...(e.rotation||{{x:0,y:0,z:0}})}},scale:{{...(e.scale||{{x:1,y:1,z:1}})}}}}]));
const cinUI=document.getElementById('aura-cinematic-ui'),cinSubtitle=document.getElementById('aura-cinematic-subtitle'),cinFade=document.getElementById('aura-cinematic-fade'),vfxCanvas=document.getElementById('aura-vfx-overlay'),vfxCtx=vfxCanvas.getContext('2d');
function cinEase(name,t){{t=Math.max(0,Math.min(1,t));if(name==='ease_in')return t*t;if(name==='ease_out')return 1-(1-t)*(1-t);if(name==='ease_in_out')return t<.5?2*t*t:1-Math.pow(-2*t+2,2)/2;return t}}
function cinVec(a,b,t){{a=a||b||{{x:0,y:0,z:0}};b=b||a;return{{x:Number(a.x||0)+(Number(b.x||0)-Number(a.x||0))*t,y:Number(a.y||0)+(Number(b.y||0)-Number(a.y||0))*t,z:Number(a.z||0)+(Number(b.z||0)-Number(a.z||0))*t}}}}
function cinSample(track,t){{const rows=track.keyframes||[];if(!rows.length)return null;if(t<=rows[0].time_s)return{{...rows[0]}};if(t>=rows[rows.length-1].time_s)return{{...rows[rows.length-1]}};let a=rows[0],b=rows[rows.length-1];for(let i=1;i<rows.length;i++)if(t<=rows[i].time_s){{a=rows[i-1];b=rows[i];break}}const span=Math.max(.000001,b.time_s-a.time_s),p=cinEase(b.easing||a.easing||'linear',(t-a.time_s)/span);return{{position:(a.position||b.position)?cinVec(a.position,b.position,p):null,rotation_deg:(a.rotation_deg||b.rotation_deg)?cinVec(a.rotation_deg,b.rotation_deg,p):null,scale:(a.scale||b.scale)?cinVec(a.scale,b.scale,p):null,look_at_entity_id:b.look_at_entity_id||a.look_at_entity_id||null}}}}
function cinRestore(){{for(const e of entities){{const b=cinematicBase.get(e.id);if(!b)continue;e.position={{...b.position}};e.rotation={{...b.rotation}};e.scale={{...b.scale}}}}cinematicState.camera=null;cinSubtitle.hidden=true;cinFade.style.opacity='0';cinematicState.particles.length=0;vfxCtx.clearRect(0,0,vfxCanvas.width,vfxCanvas.height)}}
function cinPlay(){{if(!auraCinematic)return;cinRestore();cinematicState.active=true;cinematicState.startMs=performance.now();cinematicState.audioTriggered.clear();cinematicState.vfxTriggered.clear()}}
function cinStop(){{cinematicState.active=false;cinRestore()}}
function cinResizeVfx(){{const d=Math.min(devicePixelRatio||1,2),w=Math.max(1,innerWidth),h=Math.max(1,innerHeight);if(vfxCanvas.width!==Math.floor(w*d)||vfxCanvas.height!==Math.floor(h*d)){{vfxCanvas.width=Math.floor(w*d);vfxCanvas.height=Math.floor(h*d);vfxCtx.setTransform(d,0,0,d,0,0)}}}}
function cinSpawnVfx(cue){{const n=Math.min(256,Math.max(1,Number(cue.particle_count||36))),cx=innerWidth*.5,cy=innerHeight*.5,base=String(cue.vfx_preset||'spark');for(let i=0;i<n;i++){{const a=Math.random()*Math.PI*2,s=(20+Math.random()*110)*Math.max(.2,Number(cue.intensity||1)),life=Math.max(.15,Number(cue.duration_s||1));cinematicState.particles.push({{x:cx,y:cy,vx:Math.cos(a)*s,vy:Math.sin(a)*s-(base==='smoke'?30:0),age:0,life,color:cue.color||'#ffffff',size:base==='glow'?8:base==='energy_ring'?5:2+Math.random()*4,preset:base}})}}}}
function cinDrawVfx(dt){{cinResizeVfx();vfxCtx.clearRect(0,0,innerWidth,innerHeight);for(const p of cinematicState.particles){{p.age+=dt;p.x+=p.vx*dt;p.y+=p.vy*dt;if(p.preset==='smoke'||p.preset==='dust')p.vy-=4*dt;const q=Math.max(0,1-p.age/p.life);vfxCtx.globalAlpha=q;vfxCtx.fillStyle=p.color;vfxCtx.shadowColor=p.color;vfxCtx.shadowBlur=p.preset==='glow'||p.preset==='cosmic_burst'?18:6;vfxCtx.beginPath();vfxCtx.arc(p.x,p.y,Math.max(.5,p.size*(p.preset==='energy_ring'?(1-q)*4+1:q)),0,Math.PI*2);vfxCtx.fill()}}vfxCtx.globalAlpha=1;vfxCtx.shadowBlur=0;cinematicState.particles=cinematicState.particles.filter(p=>p.age<p.life)}}
let cinLast=performance.now();
function applyAuraCinematic(now){{const vfxDt=Math.min(.05,Math.max(0,(now-cinLast)/1000));cinLast=now;if(!auraCinematic||!cinematicState.active){{cinDrawVfx(vfxDt);return}}const t=(now-cinematicState.startMs)/1000;if(t>auraCinematic.duration_s){{cinStop();cinDrawVfx(vfxDt);return}}for(const track of auraCinematic.transform_tracks||[]){{if(track.enabled===false)continue;const sample=cinSample(track,t),e=entities.find(x=>x.id===track.target_entity_id);if(!sample||!e)continue;if(sample.position)e.position={{...sample.position}};if(sample.rotation_deg)e.rotation={{...sample.rotation_deg}};if(sample.scale)e.scale={{...sample.scale}};if(track.kind==='camera'){{const targetEntity=sample.look_at_entity_id?entities.find(x=>x.id===sample.look_at_entity_id):null;cinematicState.camera={{position:[e.position.x||0,e.position.y||0,e.position.z||0],target:targetEntity?[targetEntity.position.x||0,targetEntity.position.y||0,targetEntity.position.z||0]:[player.position.x||0,(player.position.y||0)+.7,player.position.z||0]}}}}}}
let subtitle=null,fade=null;for(const cue of auraCinematic.cues||[]){{const active=t>=cue.time_s&&t<=cue.time_s+cue.duration_s;if(cue.kind==='subtitle'&&active)subtitle=cue;if(cue.kind==='fade'&&active){{const p=Math.max(0,Math.min(1,(t-cue.time_s)/Math.max(.001,cue.duration_s)));fade={{...cue,p}}}}if(cue.kind==='audio'&&t>=cue.time_s&&!cinematicState.audioTriggered.has(cue.id)){{cinematicState.audioTriggered.add(cue.id);const asset=media.find(a=>a.id===cue.asset_id);if(asset){{const audio=new Audio(asset.media_url);audio.volume=Math.max(0,Math.min(1,Number(cue.volume||1)));audio.loop=cue.loop===true;audio.play().catch(()=>{{}})}}}}if(cue.kind==='vfx'&&t>=cue.time_s&&!cinematicState.vfxTriggered.has(cue.id)){{cinematicState.vfxTriggered.add(cue.id);cinSpawnVfx(cue)}}}}if(subtitle){{cinSubtitle.textContent=subtitle.text||'';cinSubtitle.hidden=false}}else cinSubtitle.hidden=true;if(fade){{const value=Number(fade.from_value||0)+(Number(fade.to_value||0)-Number(fade.from_value||0))*fade.p;cinFade.style.background=fade.color||'#000000';cinFade.style.opacity=String(Math.max(0,Math.min(1,value)))}}else cinFade.style.opacity='0';cinDrawVfx(vfxDt)}}
if(auraCinematic){{cinUI.hidden=false;document.getElementById('aura-cinematic-play').addEventListener('click',cinPlay);document.getElementById('aura-cinematic-stop').addEventListener('click',cinStop);if(auraCinematic.autoplay)cinPlay()}}
</script>
"""


def render_aura3d_playtest(game, world, *, csp: str) -> str:
    """Render Aura3D v4 by layering closed cinematic DNA over the reviewed v3 renderer.

    The v3 renderer remains the PBR/model/audio/streaming implementation. v4 injects only a
    deterministic declarative timeline controller and built-in screen-space particle presets.
    Creator JavaScript, creator GLSL, network APIs and external engines remain unavailable.
    """
    # Validate cinematic references before producing a playtest document.
    _runtime_payload(game, world)
    html = _render_v3(game, world, csp=csp)
    html = html.replace("Aura3D v3", "Aura3D v4")
    html = html.replace("Aura Game Engine 3D v3", "Aura Game Engine 3D v4")
    html = html.replace('"runtime_version": 3', '"runtime_version": 4')

    frame_anchor = "let last=performance.now();function frame(now){const dt="
    if frame_anchor not in html:
        raise ValueError("Aura3D v3 frame contract changed; v4 cinematic hook requires review")
    html = html.replace(
        frame_anchor,
        "let last=performance.now();function frame(now){applyAuraCinematic(now);const dt=",
        1,
    )

    camera_anchor = "const pp=player.position,target=[pp.x,pp.y+.7,pp.z],eye=[pp.x+Math.sin(yaw)*Math.cos(pitch)*distance,pp.y+2+Math.sin(pitch)*distance,pp.z+Math.cos(yaw)*Math.cos(pitch)*distance],aspect=resize(),vp=mul(perspective(Math.PI/3,aspect,.08,5000),lookAt(eye,target,[0,1,0]));"
    if camera_anchor not in html:
        raise ValueError("Aura3D v3 camera contract changed; v4 cinematic camera hook requires review")
    html = html.replace(
        camera_anchor,
        "const pp=player.position,defaultTarget=[pp.x,pp.y+.7,pp.z],defaultEye=[pp.x+Math.sin(yaw)*Math.cos(pitch)*distance,pp.y+2+Math.sin(pitch)*distance,pp.z+Math.cos(yaw)*Math.cos(pitch)*distance],target=cinematicState.camera?.target||defaultTarget,eye=cinematicState.camera?.position||defaultEye,aspect=resize(),vp=mul(perspective(Math.PI/3,aspect,.08,5000),lookAt(eye,target,[0,1,0]));",
        1,
    )

    return html.replace("</body></html>", _cinematic_script(game.id) + "</body></html>", 1)


__all__ = ["render_aura3d_playtest", "_runtime_payload"]
