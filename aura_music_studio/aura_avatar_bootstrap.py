from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from .aura_avatar_runtime import RUNTIME_JS, THREE_VERSION, THREE_VRM_VERSION

router = APIRouter(tags=["Aura Embodied Bootstrap"])

# Repair/extend the base module before serving it. The source runtime is deliberately kept
# simple; this bootstrap layers product integration, accessibility and cross-page continuity.
PATCHED_RUNTIME_JS = RUNTIME_JS.replace(
    "this.state==='thinking'?.55:0",
    "(this.state==='thinking'?0.55:0)",
).replace(
    "width:'230px',height:'410px'",
    "width:(innerWidth<700?'165px':'230px'),height:(innerWidth<700?'295px':'410px')",
).replace(
    "behavior:'smooth'",
    "behavior:(matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth')",
).replace(
    "clamp(dt*5)",
    "clamp(dt*(matchMedia('(prefers-reduced-motion: reduce)').matches?30:5))",
).replace(
    "addEventListener('aura:celebrate',()=>{this.setState('celebrate');clearTimeout(this._stateTimer);this._stateTimer=setTimeout(()=>this.setState('idle'),1800)});",
    "addEventListener('aura:celebrate',()=>{this.setState('celebrate');clearTimeout(this._stateTimer);this._stateTimer=setTimeout(()=>this.setState('idle'),1800)});"
    "document.addEventListener('play',e=>{if(e.target instanceof HTMLMediaElement){this.setState('speaking');dispatchEvent(new CustomEvent('aura:speaking',{detail:{active:true,source:'page-audio'}}));}},true);"
    "document.addEventListener('ended',e=>{if(e.target instanceof HTMLMediaElement){this.setState('idle');dispatchEvent(new CustomEvent('aura:speaking',{detail:{active:false,source:'page-audio'}}));}},true);"
    "document.addEventListener('pause',e=>{if(e.target instanceof HTMLMediaElement&&!e.target.ended){this.setState('idle');dispatchEvent(new CustomEvent('aura:speaking',{detail:{active:false,source:'page-audio'}}));}},true);",
).replace(
    "this.updateLipSync(this.state==='speaking'?this.speechLevel():0,t);",
    "this.updateLipSync(this.state==='speaking'?(this.analyser?this.speechLevel():(0.25+0.20*Math.abs(Math.sin(t*.018)))):0,t);",
).replace(
    "const aura=new AuraEmbodiedRuntime();aura.init();",
    r"""
AuraEmbodiedRuntime.prototype.startTour=async function(){
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const visible=el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>18&&r.height>18&&r.bottom>0&&r.top<innerHeight&&s.visibility!=='hidden'&&s.display!=='none';};
  const all=[...document.querySelectorAll('main a[href],main button,.dashboard a[href],.dashboard button,.tiles a[href],.tiles button,.nav a[href],.nav button')];
  const unique=[];const seen=new Set();
  for(const el of all){
    if(!visible(el)||el.closest('#aura-embodied-root')||el.classList.contains('aura-companion-fab')||el.classList.contains('aura-tour-fab'))continue;
    const label=(el.getAttribute('data-aura-guide')||el.getAttribute('aria-label')||el.getAttribute('title')||el.textContent||'').replace(/\s+/g,' ').trim();
    if(label.length<2||label.length>100||seen.has(label.toLowerCase()))continue;seen.add(label.toLowerCase());unique.push({el,label});if(unique.length>=6)break;
  }
  if(!unique.length){await this.speak('I can stay with you here. Ask me what you would like to find or create.');return;}
  await this.speak('I will show you around. I will point out the main controls on this screen.');
  for(let i=0;i<unique.length;i++){
    const {el,label}=unique[i];if(!document.body.contains(el))continue;el.scrollIntoView({behavior:(matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'),block:'center',inline:'nearest'});await sleep(matchMedia('(prefers-reduced-motion: reduce)').matches?80:420);
    if(!el.id)el.id='aura-guide-'+Math.random().toString(36).slice(2,9);this.guideTo('#'+CSS.escape(el.id));await sleep(matchMedia('(prefers-reduced-motion: reduce)').matches?80:720);
    const custom=el.getAttribute('data-aura-guide');const line=custom||('This is '+label+'.');await this.speak(line);await sleep(180);
  }
  this.guideTarget=null;this.setState('idle');await this.speak('That is the main layout. I can guide you to any feature whenever you need me.');
};

AuraEmbodiedRuntime.prototype.scanPageControls=async function(){
  const visible=el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>12&&r.height>12&&s.visibility!=='hidden'&&s.display!=='none';};
  const nodes=[...document.querySelectorAll('a[href],button,input:not([type=hidden]),select,textarea,[role=button]')];
  const controls=[];let index=0;
  for(const el of nodes){
    if(!visible(el)||el.closest('#aura-embodied-root')||el.classList.contains('aura-companion-fab')||el.classList.contains('aura-tour-fab'))continue;
    const label=(el.getAttribute('data-aura-guide')||el.getAttribute('aria-label')||el.getAttribute('title')||el.textContent||el.getAttribute('placeholder')||'').replace(/\s+/g,' ').trim();
    if(label.length<2||label.length>180)continue;
    let id=el.getAttribute('data-aura-control-id');if(!id){id='ctl-'+(++index)+'-'+Math.random().toString(36).slice(2,8);el.setAttribute('data-aura-control-id',id);}
    controls.push({id,label,selector:'[data-aura-control-id="'+id+'"]',kind:(el.tagName||'control').toLowerCase()});if(controls.length>=80)break;
  }
  try{await fetch('/api/aura/avatar/page-context',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({path:location.pathname,title:document.title||'Live Sound Studio',controls})});}catch(_){}
  return controls;
};

AuraEmbodiedRuntime.prototype.handleBridgeCommand=async function(command){
  if(!command)return;const action=command.action,p=command.payload||{},message=p.message||'';
  if(action==='guide_to'){
    const el=p.selector?document.querySelector(p.selector):null;if(el){el.scrollIntoView({behavior:(matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'),block:'center',inline:'nearest'});await new Promise(r=>setTimeout(r,matchMedia('(prefers-reduced-motion: reduce)').matches?60:350));this.guideTo(p.selector,message);await new Promise(r=>setTimeout(r,matchMedia('(prefers-reduced-motion: reduce)').matches?60:650));}
    if(p.speak&&message)await this.speak(message);return;
  }
  if(action==='present'){this.setState('presenting');if(p.speak&&message)await this.speak(message);return;}
  if(action==='celebrate'){this.setState('celebrate');if(p.speak&&message)await this.speak(message);setTimeout(()=>this.setState('idle'),1500);return;}
  if(action==='listen'){this.setState('listening');if(p.speak&&message)await this.speak(message);return;}
  if(action==='think'){this.setState('thinking');if(p.speak&&message)await this.speak(message);return;}
  if(action==='minimize'){this.root.style.transformOrigin='bottom right';this.root.style.transform='scale(.34)';this.root.style.opacity='.82';this.persistUiState();return;}
  if(action==='restore'){this.root.style.transform='scale(1)';this.root.style.opacity='1';this.setState('idle');this.persistUiState();if(p.speak&&message)await this.speak(message);return;}
};

AuraEmbodiedRuntime.prototype.persistUiState=function(){
  try{sessionStorage.setItem('aura-embodied-ui',JSON.stringify({x:this.pos.x,y:this.pos.y,minimized:this.root?.style?.transform?.includes('scale(.34)')||false}));}catch(_){}
};
AuraEmbodiedRuntime.prototype.restoreUiState=function(){
  try{const raw=sessionStorage.getItem('aura-embodied-ui');if(!raw)return;const v=JSON.parse(raw);if(Number.isFinite(v.x)&&Number.isFinite(v.y)){this.pos.x=clamp(v.x,8,innerWidth-(innerWidth<700?173:238));this.pos.y=clamp(v.y,8,innerHeight-(innerWidth<700?303:418));this.target={...this.pos};}if(v.minimized){this.root.style.transformOrigin='bottom right';this.root.style.transform='scale(.34)';this.root.style.opacity='.82';}}catch(_){}
};

AuraEmbodiedRuntime.prototype.startBridge=function(){
  this.restoreUiState();this.resize();this.scanPageControls();
  addEventListener('pagehide',()=>this.persistUiState());
  this._pageScanTimer=setInterval(()=>this.scanPageControls(),5000);
  this._commandTimer=setInterval(async()=>{
    try{const r=await fetch('/api/aura/avatar/commands/next',{credentials:'same-origin'});if(!r.ok)return;const data=await r.json();if(data.command)await this.handleBridgeCommand(data.command);}catch(_){}
  },900);
};

const aura=new AuraEmbodiedRuntime();aura.init().then(()=>aura.startBridge());
""",
)


def avatar_bootstrap_html() -> str:
    return f"""
<script type='importmap' id='aura-avatar-importmap'>
{{"imports":{{
  "three":"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/build/three.module.js",
  "three/addons/":"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/examples/jsm/",
  "@pixiv/three-vrm":"https://cdn.jsdelivr.net/npm/@pixiv/three-vrm@{THREE_VRM_VERSION}/lib/three-vrm.module.min.js"
}}}}
</script>
<script type='module' src='/aura/avatar/runtime-v3.js' id='aura-avatar-runtime'></script>
"""


@router.get("/aura/avatar/runtime-v3.js", include_in_schema=False)
def runtime_js() -> Response:
    return Response(PATCHED_RUNTIME_JS, media_type="text/javascript; charset=utf-8", headers={"Cache-Control": "private, max-age=300"})
