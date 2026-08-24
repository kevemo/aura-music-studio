from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter(tags=["Aura Embodied Runtime"])

# Pinned browser modules. They are deliberately version-pinned so a future upstream release
# cannot silently change Aura's animation/runtime behaviour. These can be vendored locally later.
THREE_VERSION = "0.180.0"
THREE_VRM_VERSION = "3.5.5"


def avatar_bootstrap_html() -> str:
    return f"""
<script type='importmap' id='aura-avatar-importmap'>
{{"imports":{{
  "three":"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/build/three.module.js",
  "three/addons/":"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/examples/jsm/",
  "@pixiv/three-vrm":"https://cdn.jsdelivr.net/npm/@pixiv/three-vrm@{THREE_VRM_VERSION}/lib/three-vrm.module.js"
}}}}
</script>
<script type='module' src='/aura/avatar/runtime-v2.js' id='aura-avatar-runtime'></script>
"""


RUNTIME_JS = r'''
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';

const clamp=(v,a=0,b=1)=>Math.max(a,Math.min(b,v));
const lerp=(a,b,t)=>a+(b-a)*t;
const now=()=>performance.now();

class AuraEmbodiedRuntime {
  constructor(){
    this.root=null; this.canvas=null; this.renderer=null; this.scene=null; this.camera=null;
    this.clock=new THREE.Clock(); this.vrm=null; this.mixer=null; this.actions=new Map();
    this.state='idle'; this.audio=null; this.audioCtx=null; this.analyser=null; this.audioSource=null;
    this.pointer={x:innerWidth/2,y:innerHeight/2}; this.guideTarget=null;
    this.target={x:Math.max(12,innerWidth-250),y:Math.max(12,innerHeight-430)}; this.pos={...this.target};
    this.lastBlink=now(); this.nextBlink=2200+Math.random()*3000; this.speechEnergy=0;
    this.heartMaterials=[]; this.circuitMaterials=[]; this.modelAvailable=false; this.placeholder=null;
    this._lookTarget=new THREE.Object3D(); this._stateTimer=0;
  }

  async init(){
    if(document.getElementById('aura-embodied-root')) return this;
    this.buildSurface(); this.buildScene(); this.bindEvents();
    try{
      const r=await fetch('/api/aura/avatar/manifest',{credentials:'same-origin'});
      if(!r.ok) throw new Error('manifest unavailable');
      const manifest=await r.json();
      if(manifest.model_url){await this.loadModel(manifest.model_url);this.modelAvailable=true;}
      else this.showCanonicalPendingState();
    }catch(_){this.showCanonicalPendingState();}
    this.scene.add(this._lookTarget); this.loop(); window.AuraAvatar=this;
    window.dispatchEvent(new CustomEvent('aura:avatar-ready',{detail:{modelAvailable:this.modelAvailable}}));
    return this;
  }

  buildSurface(){
    this.root=document.createElement('div'); this.root.id='aura-embodied-root';
    Object.assign(this.root.style,{position:'fixed',left:this.pos.x+'px',top:this.pos.y+'px',width:'230px',height:'410px',zIndex:'9996',pointerEvents:'none',transition:'filter .25s ease',filter:'drop-shadow(0 18px 35px rgba(0,0,0,.42))'});
    this.canvas=document.createElement('canvas'); Object.assign(this.canvas.style,{width:'100%',height:'100%',display:'block'}); this.root.appendChild(this.canvas); document.body.appendChild(this.root);
  }

  buildScene(){
    this.renderer=new THREE.WebGLRenderer({canvas:this.canvas,alpha:true,antialias:true,powerPreference:'high-performance'});
    this.renderer.outputColorSpace=THREE.SRGBColorSpace; this.renderer.setPixelRatio(Math.min(devicePixelRatio,2));
    this.scene=new THREE.Scene(); this.camera=new THREE.PerspectiveCamera(23,230/410,.05,100); this.camera.position.set(0,1.25,4.35);
    this.scene.add(new THREE.HemisphereLight(0xffefff,0x13071d,1.9));
    const key=new THREE.DirectionalLight(0xffd9f6,2.15); key.position.set(2.2,4.1,3.0); this.scene.add(key);
    const rim=new THREE.PointLight(0xd827ff,7.5,5,2); rim.position.set(-1.4,1.7,-.7); this.scene.add(rim); this.resize();
  }

  resize(){if(!this.renderer)return;const w=this.root.clientWidth||230,h=this.root.clientHeight||410;this.renderer.setSize(w,h,false);this.camera.aspect=w/h;this.camera.updateProjectionMatrix();}

  bindEvents(){
    addEventListener('resize',()=>{this.resize();this.target.x=clamp(this.target.x,8,innerWidth-238);this.target.y=clamp(this.target.y,8,innerHeight-418)});
    document.addEventListener('pointermove',e=>{this.pointer.x=e.clientX;this.pointer.y=e.clientY},{passive:true});
    document.addEventListener('focusin',e=>{const el=e.target;if(el?.matches?.('button,a,input,select,textarea,[role=button]'))this.lookAtElement(el)});
    addEventListener('aura:speaking',e=>this.setState(e.detail?.active?'speaking':'idle'));
    addEventListener('aura:listening',e=>this.setState(e.detail?.active?'listening':'idle'));
    addEventListener('aura:thinking',e=>this.setState(e.detail?.active?'thinking':'idle'));
    addEventListener('aura:guide',e=>this.guideTo(e.detail?.selector,e.detail?.message));
    addEventListener('aura:celebrate',()=>{this.setState('celebrate');clearTimeout(this._stateTimer);this._stateTimer=setTimeout(()=>this.setState('idle'),1800)});
  }

  showCanonicalPendingState(){
    // Never substitute a different human face/body. This luminous core is only a temporary loading state.
    if(this.placeholder)return; const g=new THREE.Group();
    const mat=new THREE.MeshStandardMaterial({color:0xff53ce,emissive:0xff168f,emissiveIntensity:2.7,roughness:.2});
    const core=new THREE.Mesh(new THREE.SphereGeometry(.16,32,32),mat); const ring=new THREE.Mesh(new THREE.TorusGeometry(.29,.015,14,72),new THREE.MeshStandardMaterial({color:0xe9c8ff,emissive:0xb932ff,emissiveIntensity:2.4}));
    ring.rotation.x=Math.PI/2;g.add(core,ring);g.position.y=1.2;this.scene.add(g);this.placeholder=g;
  }

  async loadModel(url){
    const loader=new GLTFLoader(); loader.register(parser=>new VRMLoaderPlugin(parser)); const gltf=await loader.loadAsync(url); const vrm=gltf.userData.vrm;
    if(!vrm)throw new Error('Aura asset must be VRM 1.0 GLB');
    VRMUtils.removeUnnecessaryVertices(gltf.scene);VRMUtils.combineSkeletons(gltf.scene);VRMUtils.rotateVRM0(vrm);
    this.vrm=vrm;this.scene.add(vrm.scene);vrm.scene.rotation.y=Math.PI;vrm.scene.position.set(0,-.95,0);this.mixer=new THREE.AnimationMixer(vrm.scene);
    for(const clip of gltf.animations||[]){if(clip.name)this.actions.set(clip.name.toLowerCase(),this.mixer.clipAction(clip));}
    vrm.scene.traverse(obj=>{if(!obj.isMesh)return;for(const m of(Array.isArray(obj.material)?obj.material:[obj.material])){if(!m)continue;const n=(m.name||'').toLowerCase();if(n.includes('heart')||n.includes('core'))this.heartMaterials.push(m);if(n.includes('circuit')||n.includes('energy')||n.includes('emissive'))this.circuitMaterials.push(m);}});
    this.playState('idle');
  }

  findAction(names){for(const name of names){const action=this.actions.get(name.toLowerCase());if(action)return action;}return null;}
  playState(state){if(!this.mixer)return;const map={idle:['idle'],walking:['walk','walking'],speaking:['talk','speaking'],listening:['listen','idle'],thinking:['think','idle'],pointing:['pointright','pointleft','present'],presenting:['present'],celebrate:['celebrate','present']};const next=this.findAction(map[state]||['idle']);if(!next)return;for(const a of this.actions.values())if(a!==next)a.fadeOut(.2);next.reset().fadeIn(.22).play();}
  setState(state){this.state=state||'idle';this.playState(this.state);}

  lookAtElement(el){const r=el.getBoundingClientRect();this.guideTarget={x:r.left+r.width/2,y:r.top+r.height/2};}
  guideTo(selector,message=''){
    const el=selector?document.querySelector(selector):null;if(!el)return false;const r=el.getBoundingClientRect();const putRight=r.left<innerWidth/2;
    this.target.x=putRight?clamp(r.right+16,8,innerWidth-238):clamp(r.left-238,8,innerWidth-238);this.target.y=clamp(r.top+r.height/2-205,8,innerHeight-418);this.lookAtElement(el);
    this.setState('walking');clearTimeout(this._stateTimer);this._stateTimer=setTimeout(()=>this.setState('pointing'),620);if(message)dispatchEvent(new CustomEvent('aura:guide-message',{detail:{message,selector}}));return true;
  }

  async speak(text,locale=null){
    if(!text?.trim())return;if(this.audio){this.audio.pause();this.audio=null;}this.setState('speaking');dispatchEvent(new CustomEvent('aura:speaking',{detail:{active:true}}));
    const res=await fetch('/api/aura/voice/speak',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({text,locale})});if(!res.ok){this.setState('idle');throw new Error('Aura speech unavailable');}
    const blob=await res.blob(),url=URL.createObjectURL(blob),audio=new Audio(url);this.audio=audio;
    try{this.audioCtx=this.audioCtx||new(window.AudioContext||window.webkitAudioContext)();if(this.audioCtx.state==='suspended')await this.audioCtx.resume();this.audioSource=this.audioCtx.createMediaElementSource(audio);this.analyser=this.audioCtx.createAnalyser();this.analyser.fftSize=512;this.audioSource.connect(this.analyser);this.analyser.connect(this.audioCtx.destination);}catch(_){this.analyser=null;}
    try{await audio.play();await new Promise(resolve=>{audio.onended=resolve;audio.onerror=resolve});}finally{URL.revokeObjectURL(url);this.analyser=null;this.audio=null;this.setState('idle');dispatchEvent(new CustomEvent('aura:speaking',{detail:{active:false}}));}
  }

  speechLevel(){if(!this.analyser)return 0;const data=new Uint8Array(this.analyser.frequencyBinCount);this.analyser.getByteFrequencyData(data);let sum=0,n=0;for(let i=2;i<Math.min(90,data.length);i++){sum+=data[i];n++;}return clamp(((sum/Math.max(1,n))-10)/80);}
  expr(name,v){try{this.vrm?.expressionManager?.setValue(name,clamp(v));}catch(_){}}
  updateBlink(t){const elapsed=t-this.lastBlink;if(elapsed<this.nextBlink)return;const bt=elapsed-this.nextBlink;let v=0;if(bt<80)v=bt/80;else if(bt<165)v=1-(bt-80)/85;this.expr('blink',v);if(bt>185){this.expr('blink',0);this.lastBlink=t;this.nextBlink=2100+Math.random()*3500;}}
  updateLipSync(level,t){this.speechEnergy=lerp(this.speechEnergy,level,.32);const e=this.speechEnergy,p=t*.001;this.expr('aa',e*(.55+.28*Math.sin(p*7.3)));this.expr('oh',e*(.18+.14*Math.sin(p*5.4+1.2)));this.expr('ee',e*.12);this.expr('ih',e*.10);this.expr('ou',e*.08);}

  updateHumanMotion(t,dt){
    if(!this.vrm)return;const h=this.vrm.humanoid,head=h?.getNormalizedBoneNode('head'),chest=h?.getNormalizedBoneNode('chest'),spine=h?.getNormalizedBoneNode('spine');
    const breath=Math.sin(t*.00155)*.007;if(chest)chest.rotation.x=breath;if(spine)spine.rotation.z=Math.sin(t*.00068)*.008;
    const target=this.guideTarget||this.pointer,nx=clamp((target.x/innerWidth-.5)*2,-1,1),ny=clamp((target.y/innerHeight-.5)*2,-1,1);
    if(head){head.rotation.y=lerp(head.rotation.y,-nx*.15,clamp(dt*2.6));head.rotation.x=lerp(head.rotation.x,-ny*.07,clamp(dt*2.6));}
    this._lookTarget.position.set(nx*1.25,1.25-ny*.55,2.45);try{if(this.vrm.lookAt)this.vrm.lookAt.target=this._lookTarget;}catch(_){}
  }

  updateEmission(t){const speech=this.state==='speaking'?this.speechEnergy:0,think=this.state==='thinking'?.55:0,base=.7+Math.sin(t*.0028)*.12,core=base+speech*2.2+think*.45;for(const m of this.heartMaterials)if('emissiveIntensity'in m)m.emissiveIntensity=core;for(const m of this.circuitMaterials)if('emissiveIntensity'in m)m.emissiveIntensity=.48+base*.32+speech*.95;}

  loop(){
    const frame=()=>{requestAnimationFrame(frame);const dt=Math.min(this.clock.getDelta(),.05),t=now();this.pos.x=lerp(this.pos.x,this.target.x,clamp(dt*5));this.pos.y=lerp(this.pos.y,this.target.y,clamp(dt*5));this.root.style.left=this.pos.x+'px';this.root.style.top=this.pos.y+'px';
      if(this.mixer)this.mixer.update(dt);if(this.vrm){this.updateBlink(t);this.updateLipSync(this.state==='speaking'?this.speechLevel():0,t);this.updateHumanMotion(t,dt);this.updateEmission(t);this.vrm.update(dt);}if(this.placeholder){this.placeholder.rotation.y+=dt*.45;this.placeholder.position.y=1.2+Math.sin(t*.002)*.035;}this.renderer.render(this.scene,this.camera);};frame();
  }
}

const aura=new AuraEmbodiedRuntime();aura.init();
export {AuraEmbodiedRuntime,aura};
'''


@router.get("/aura/avatar/runtime-v2.js", include_in_schema=False)
def runtime_js() -> Response:
    return Response(RUNTIME_JS, media_type="text/javascript; charset=utf-8", headers={"Cache-Control": "private, max-age=300"})
