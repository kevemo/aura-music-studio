from __future__ import annotations

import json
import os
import struct
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

router = APIRouter(tags=["Aura Embodied Interface"])

STATIC_DIR = Path(__file__).resolve().parent / "static" / "aura"
MODEL_PATH = Path(os.getenv("AURA_AVATAR_MODEL_PATH") or STATIC_DIR / "aura.glb")

CANONICAL_IDENTITY = {
    "name": "Aura",
    "identity_version": "auracore-osiris-canonical-v1",
    "appearance_locked": True,
    "generic_avatar_fallback_allowed": False,
    "visual_reference": {
        "face": "approved Aura face: refined feminine face, calm expression, silver-lilac hair, luminous magenta eyes",
        "body": "elegant full humanoid feminine android proportions, human-looking exterior",
        "suit": "matte-black fitted technical suit with fine magenta/violet circuitry",
        "core": "luminous pink-magenta heart core in the centre of the chest",
        "hands": "five-finger fully articulated human-proportioned hands",
        "feet": "fully modelled humanoid feet integrated into the suit/boot design",
        "excluded": ["mouse cursor from source reference", "generic robot face", "exposed skeletal robot exterior"],
    },
}

REQUIRED_HUMANOID_BONES = {
    "hips", "spine", "chest", "neck", "head",
    "leftUpperArm", "leftLowerArm", "leftHand",
    "rightUpperArm", "rightLowerArm", "rightHand",
    "leftUpperLeg", "leftLowerLeg", "leftFoot",
    "rightUpperLeg", "rightLowerLeg", "rightFoot",
}

RECOMMENDED_EXPRESSIONS = {
    "blink", "happy", "relaxed", "surprised",
    "aa", "ih", "ou", "ee", "oh",
    "lookUp", "lookDown", "lookLeft", "lookRight",
}

RECOMMENDED_ANIMATIONS = [
    "Idle", "Walk", "Talk", "Listen", "Think", "PointLeft", "PointRight", "Present", "Celebrate"
]


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    return member


def _glb_json(path: Path) -> dict[str, Any]:
    """Read the JSON chunk from a binary glTF without needing Blender at runtime."""
    with path.open("rb") as handle:
        header = handle.read(12)
        if len(header) != 12:
            raise ValueError("GLB header is incomplete")
        magic, version, total_length = struct.unpack("<4sII", header)
        if magic != b"glTF" or version != 2:
            raise ValueError("Aura model must be a glTF 2.0 GLB")
        if total_length != path.stat().st_size:
            raise ValueError("GLB length header does not match file size")
        while handle.tell() < total_length:
            chunk_header = handle.read(8)
            if len(chunk_header) != 8:
                break
            chunk_length, chunk_type = struct.unpack("<II", chunk_header)
            data = handle.read(chunk_length)
            if chunk_type == 0x4E4F534A:  # JSON
                return json.loads(data.decode("utf-8").rstrip("\x00 \t\r\n"))
    raise ValueError("GLB does not contain a JSON chunk")


def validate_aura_model(path: Path = MODEL_PATH) -> dict[str, Any]:
    result: dict[str, Any] = {
        "exists": path.is_file(),
        "path": str(path),
        "valid_glb": False,
        "vrm_1": False,
        "missing_humanoid_bones": sorted(REQUIRED_HUMANOID_BONES),
        "expressions": [],
        "recommended_expression_coverage": 0.0,
        "animations": [],
        "identity_contract": CANONICAL_IDENTITY,
    }
    if not path.is_file():
        result["blocking_reason"] = "Canonical Aura GLB has not been installed on this deployment. A generic avatar will not be substituted."
        return result
    try:
        gltf = _glb_json(path)
    except Exception as exc:
        result["blocking_reason"] = f"Invalid Aura GLB: {exc}"
        return result

    result["valid_glb"] = True
    extensions = gltf.get("extensions") or {}
    vrm = extensions.get("VRMC_vrm") or {}
    result["vrm_1"] = bool(vrm and str(vrm.get("specVersion") or "").startswith("1."))

    humanoid = (vrm.get("humanoid") or {}).get("humanBones") or {}
    present_bones = set(humanoid.keys())
    result["missing_humanoid_bones"] = sorted(REQUIRED_HUMANOID_BONES - present_bones)

    expressions = (vrm.get("expressions") or {})
    preset = expressions.get("preset") or {}
    custom = expressions.get("custom") or {}
    names = sorted(set(preset.keys()) | set(custom.keys()))
    result["expressions"] = names
    covered = len(RECOMMENDED_EXPRESSIONS.intersection(names))
    result["recommended_expression_coverage"] = round(covered / len(RECOMMENDED_EXPRESSIONS), 3)

    animation_names = [str(item.get("name") or "") for item in (gltf.get("animations") or [])]
    result["animations"] = [name for name in animation_names if name]
    result["missing_recommended_animations"] = [name for name in RECOMMENDED_ANIMATIONS if name not in animation_names]
    result["ready_for_embodied_runtime"] = bool(
        result["valid_glb"] and result["vrm_1"] and not result["missing_humanoid_bones"]
    )
    if not result["vrm_1"]:
        result["blocking_reason"] = "Aura GLB must include the VRM 1.0 extension for standardized humanoid, gaze and expression control."
    elif result["missing_humanoid_bones"]:
        result["blocking_reason"] = "Aura GLB is missing required humanoid bones."
    return result


@router.get("/api/aura/avatar/manifest")
def avatar_manifest(request: Request):
    member = _member(request)
    validation = validate_aura_model()
    return {
        "canonical_identity": CANONICAL_IDENTITY,
        "member_plan": member.plan.id,
        "model_url": "/api/aura/avatar/model.glb" if validation.get("exists") else None,
        "model_format": "VRM 1.0 embedded in GLB",
        "required_humanoid_bones": sorted(REQUIRED_HUMANOID_BONES),
        "recommended_expressions": sorted(RECOMMENDED_EXPRESSIONS),
        "recommended_animations": RECOMMENDED_ANIMATIONS,
        "runtime": {
            "human_idle": True,
            "blink": True,
            "gaze_tracking": True,
            "speech_lipsync": True,
            "breathing": True,
            "micro_head_motion": True,
            "walk": True,
            "point_to_interface": True,
            "present": True,
            "guide_between_controls": True,
            "heart_core_emission": True,
            "circuitry_emission": True,
            "page_persistent_companion": True,
        },
        "validation": validation,
    }


@router.get("/api/aura/avatar/model.glb", include_in_schema=False)
def avatar_model(request: Request):
    _member(request)
    validation = validate_aura_model()
    if not validation.get("ready_for_embodied_runtime"):
        raise HTTPException(503, validation.get("blocking_reason") or "Canonical Aura model is not ready")
    return FileResponse(
        MODEL_PATH,
        media_type="model/gltf-binary",
        filename="Aura-AuraCore-Osiris.glb",
        headers={"Cache-Control": "private, max-age=3600"},
    )


AURA_RUNTIME_JS = r'''
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';

const clamp=(v,a=0,b=1)=>Math.max(a,Math.min(b,v));
const lerp=(a,b,t)=>a+(b-a)*t;

class AuraEmbodiedRuntime {
  constructor(){
    this.root=null; this.canvas=null; this.renderer=null; this.scene=null; this.camera=null;
    this.clock=new THREE.Clock(); this.vrm=null; this.mixer=null; this.actions=new Map();
    this.state='idle'; this.visible=true; this.audio=null; this.analyser=null; this.audioCtx=null;
    this.expression={blink:0,mouth:0}; this.lastBlink=performance.now(); this.nextBlink=2200+Math.random()*2800;
    this.target={x:window.innerWidth-250,y:window.innerHeight-430}; this.pos={...this.target};
    this.guideTarget=null; this.pointer={x:0,y:0}; this.loaded=false; this.modelAvailable=false;
    this.heartMaterials=[]; this.circuitMaterials=[]; this.speechEnergy=0; this.listenEnergy=0;
    this._raf=0; this._boundResize=()=>this.resize();
  }

  async init(){
    if(document.getElementById('aura-embodied-root')) return this;
    this.root=document.createElement('div');
    this.root.id='aura-embodied-root';
    Object.assign(this.root.style,{position:'fixed',right:'18px',bottom:'18px',width:'230px',height:'410px',zIndex:'9996',pointerEvents:'none',transition:'filter .25s ease',filter:'drop-shadow(0 18px 30px rgba(0,0,0,.38))'});
    this.canvas=document.createElement('canvas');
    Object.assign(this.canvas.style,{width:'100%',height:'100%',display:'block'});
    this.root.appendChild(this.canvas); document.body.appendChild(this.root);

    this.renderer=new THREE.WebGLRenderer({canvas:this.canvas,alpha:true,antialias:true,powerPreference:'high-performance'});
    this.renderer.outputColorSpace=THREE.SRGBColorSpace; this.renderer.setPixelRatio(Math.min(devicePixelRatio,2));
    this.scene=new THREE.Scene();
    this.camera=new THREE.PerspectiveCamera(24,1,0.05,100); this.camera.position.set(0,1.35,4.25);
    this.scene.add(new THREE.HemisphereLight(0xf7e7ff,0x150821,1.8));
    const key=new THREE.DirectionalLight(0xffd5f7,2.2); key.position.set(2,4,3); this.scene.add(key);
    const rim=new THREE.PointLight(0xd72aff,8,5,2); rim.position.set(-1.4,1.8,-0.5); this.scene.add(rim);
    window.addEventListener('resize',this._boundResize); this.resize();

    try{
      const manifest=await fetch('/api/aura/avatar/manifest',{credentials:'same-origin'}).then(r=>r.json());
      if(manifest.model_url){ await this.load(manifest.model_url); this.modelAvailable=true; }
      else this.showAwaitingCanonicalModel();
    }catch(err){ this.showAwaitingCanonicalModel(); }

    this.loaded=true; this.animate(); this.bindPageAwareness();
    window.AuraAvatar=this;
    window.dispatchEvent(new CustomEvent('aura:avatar-ready',{detail:{runtime:this,modelAvailable:this.modelAvailable}}));
    return this;
  }

  showAwaitingCanonicalModel(){
    // Do not replace Aura with a generic human. The orb is an explicit temporary system state.
    const g=new THREE.Group();
    const core=new THREE.Mesh(new THREE.SphereGeometry(.18,32,32),new THREE.MeshStandardMaterial({color:0xff4fcf,emissive:0xff167f,emissiveIntensity:2.8,roughness:.25}));
    const ring=new THREE.Mesh(new THREE.TorusGeometry(.31,.018,16,64),new THREE.MeshStandardMaterial({color:0xe6b9ff,emissive:0xba28ff,emissiveIntensity:2.3}));
    ring.rotation.x=Math.PI/2; g.add(core,ring); g.position.y=1.3; this.scene.add(g); this.placeholder=g;
  }

  async load(url){
    const loader=new GLTFLoader(); loader.register(parser=>new VRMLoaderPlugin(parser));
    const gltf=await loader.loadAsync(url);
    const vrm=gltf.userData.vrm; if(!vrm) throw new Error('Canonical Aura model is not VRM 1.0');
    VRMUtils.removeUnnecessaryVertices(gltf.scene); VRMUtils.combineSkeletons(gltf.scene); VRMUtils.rotateVRM0(vrm);
    this.vrm=vrm; this.scene.add(vrm.scene); vrm.scene.rotation.y=Math.PI; vrm.scene.position.set(0,-.95,0);
    this.mixer=new THREE.AnimationMixer(vrm.scene);
    for(const clip of gltf.animations||[]){ const action=this.mixer.clipAction(clip); this.actions.set((clip.name||'').toLowerCase(),action); }
    this.discoverEmissionMaterials(); this.playState('idle');
  }

  discoverEmissionMaterials(){
    if(!this.vrm) return;
    this.vrm.scene.traverse(obj=>{ if(!obj.isMesh) return; const mats=Array.isArray(obj.material)?obj.material:[obj.material];
      for(const mat of mats){ if(!mat) continue; const name=(mat.name||'').toLowerCase();
        if(name.includes('heart')||name.includes('core')) this.heartMaterials.push(mat);
        if(name.includes('circuit')||name.includes('emissive')||name.includes('energy')) this.circuitMaterials.push(mat);
      }
    });
  }

  resize(){ if(!this.renderer||!this.root) return; const w=this.root.clientWidth||230,h=this.root.clientHeight||410; this.renderer.setSize(w,h,false); this.camera.aspect=w/h; this.camera.updateProjectionMatrix(); }

  bindPageAwareness(){
    document.addEventListener('pointermove',e=>{this.pointer.x=e.clientX;this.pointer.y=e.clientY},{passive:true});
    document.addEventListener('focusin',e=>{const el=e.target;if(el&&el.matches('button,a,input,select,textarea,[role=button]')) this.lookAtElement(el);});
    window.addEventListener('aura:speaking',e=>this.setState(e.detail?.active?'speaking':'idle'));
    window.addEventListener('aura:listening',e=>this.setState(e.detail?.active?'listening':'idle'));
    window.addEventListener('aura:thinking',e=>this.setState(e.detail?.active?'thinking':'idle'));
    window.addEventListener('aura:guide',e=>this.guideTo(e.detail?.selector,e.detail?.message));
  }

  findAction(names){ for(const n of names){const a=this.actions.get(n.toLowerCase()); if(a)return a;} return null; }
  playState(state){
    if(!this.mixer)return; const names={idle:['idle'],walking:['walk','walking'],speaking:['talk','speaking'],listening:['listen'],thinking:['think'],pointing:['pointright','pointleft','present'],celebrate:['celebrate']}[state]||['idle'];
    const next=this.findAction(names); if(!next)return; for(const a of this.actions.values()) if(a!==next)a.fadeOut(.22); next.reset().fadeIn(.22).play();
  }
  setState(state){ this.state=state||'idle'; this.playState(this.state); }

  lookAtElement(el){ const r=el.getBoundingClientRect(); this.guideTarget={x:r.left+r.width/2,y:r.top+r.height/2}; }
  guideTo(selector,message=''){
    const el=selector?document.querySelector(selector):null; if(!el)return false; const r=el.getBoundingClientRect();
    const leftSide=r.left<window.innerWidth/2; this.target.x=leftSide?Math.min(window.innerWidth-245,r.right+18):Math.max(15,r.left-235);
    this.target.y=clamp(r.top+r.height/2-205,10,window.innerHeight-420); this.guideTarget={x:r.left+r.width/2,y:r.top+r.height/2};
    this.setState('walking'); setTimeout(()=>this.setState('pointing'),650); if(message) window.dispatchEvent(new CustomEvent('aura:guide-message',{detail:{message}})); return true;
  }

  async speak(text,locale){
    if(!text||!text.trim()) return;
    if(this.audio){this.audio.pause();this.audio=null;}
    this.setState('speaking'); window.dispatchEvent(new CustomEvent('aura:speaking',{detail:{active:true}}));
    const response=await fetch('/api/aura/voice/speak',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({text,locale:locale||null})});
    if(!response.ok){this.setState('idle');throw new Error('Aura speech unavailable');}
    const blob=await response.blob(); const url=URL.createObjectURL(blob); const audio=new Audio(url); this.audio=audio;
    try{ this.audioCtx=this.audioCtx||new (window.AudioContext||window.webkitAudioContext)(); const source=this.audioCtx.createMediaElementSource(audio); this.analyser=this.audioCtx.createAnalyser(); this.analyser.fftSize=512; source.connect(this.analyser); this.analyser.connect(this.audioCtx.destination);}catch(_){this.analyser=null;}
    await audio.play();
    await new Promise(resolve=>{audio.onended=resolve;audio.onerror=resolve}); URL.revokeObjectURL(url); this.analyser=null; this.audio=null; this.setState('idle'); window.dispatchEvent(new CustomEvent('aura:speaking',{detail:{active:false}}));
  }

  speechLevel(){ if(!this.analyser)return 0; const arr=new Uint8Array(this.analyser.frequencyBinCount); this.analyser.getByteFrequencyData(arr); let s=0; for(let i=2;i<Math.min(arr.length,80);i++)s+=arr[i]; return clamp((s/78-12)/72); }
  setExpression(name,value){ if(!this.vrm?.expressionManager)return; try{this.vrm.expressionManager.setValue(name,clamp(value));}catch(_){} }
  blink(now){ if(now-this.lastBlink>this.nextBlink){const t=now-this.lastBlink-this.nextBlink;const v=t<90?t/90:t<180?1-(t-90)/90:0;this.setExpression('blink',v);if(t>=190){this.lastBlink=now;this.nextBlink=2100+Math.random()*3400;}} }
  lipsync(level,now){
    this.speechEnergy=lerp(this.speechEnergy,level,.35); const e=this.speechEnergy;
    const phase=now/1000; this.setExpression('aa',e*(.55+.35*Math.sin(phase*7.1))); this.setExpression('oh',e*(.25+.18*Math.sin(phase*5.3+1.1))); this.setExpression('ee',e*.12); this.setExpression('ih',e*.10); this.setExpression('ou',e*.08);
  }
  humanMotion(now,dt){
    if(!this.vrm)return; const humanoid=this.vrm.humanoid; const head=humanoid?.getNormalizedBoneNode('head'); const chest=humanoid?.getNormalizedBoneNode('chest'); const spine=humanoid?.getNormalizedBoneNode('spine');
    const breath=Math.sin(now*.0016)*.007; if(chest)chest.rotation.x=breath; if(spine)spine.rotation.z=Math.sin(now*.00072)*.008;
    if(head){const target=this.guideTarget||this.pointer; const nx=clamp((target.x/window.innerWidth-.5)*2,-1,1); const ny=clamp((target.y/window.innerHeight-.5)*2,-1,1); head.rotation.y=lerp(head.rotation.y,-nx*.16,clamp(dt*2.5)); head.rotation.x=lerp(head.rotation.x,-ny*.08,clamp(dt*2.5));}
    if(this.vrm.lookAt && this.guideTarget){const tx=(this.guideTarget.x/window.innerWidth-.5)*1.4,ty=(.5-this.guideTarget.y/window.innerHeight)*.7; const obj=this._lookObj||(this._lookObj=new THREE.Object3D()); obj.position.set(tx,1.25+ty,2.5); this.scene.add(obj); try{this.vrm.lookAt.target=obj;}catch(_){}}
  }
  emissions(now){const speaking=this.state==='speaking'?1:0;const thinking=this.state==='thinking'?.55:0;const base=.7+Math.sin(now*.003)*.14;const core=base+speaking*this.speechEnergy*2.4+thinking*.55;for(const m of this.heartMaterials){if('emissiveIntensity'in m)m.emissiveIntensity=core;}for(const m of this.circuitMaterials){if('emissiveIntensity'in m)m.emissiveIntensity=.45+base*.35+speaking*this.speechEnergy*1.1;}}
  animate(){
    const tick=()=>{this._raf=requestAnimationFrame(tick);const dt=Math.min(this.clock.getDelta(),.05),now=performance.now();
      this.pos.x=lerp(this.pos.x,this.target.x,clamp(dt*5));this.pos.y=lerp(this.pos.y,this.target.y,clamp(dt*5));this.root.style.left=this.pos.x+'px';this.root.style.top=this.pos.y+'px';this.root.style.right='auto';this.root.style.bottom='auto';
      if(this.mixer)this.mixer.update(dt);if(this.vrm){this.blink(now);this.lipsync(this.state==='speaking'?this.speechLevel():0,now);this.humanMotion(now,dt);this.emissions(now);this.vrm.update(dt);}if(this.placeholder){this.placeholder.rotation.y+=dt*.4;this.placeholder.position.y=1.3+Math.sin(now*.002)*.04;}this.renderer.render(this.scene,this.camera);};tick();
  }
}

const aura=new AuraEmbodiedRuntime();
aura.init();
export { AuraEmbodiedRuntime, aura };
'''


@router.get("/aura/avatar/runtime.js", include_in_schema=False)
def avatar_runtime_js() -> Response:
    return Response(AURA_RUNTIME_JS, media_type="text/javascript; charset=utf-8", headers={"Cache-Control": "private, max-age=300"})
