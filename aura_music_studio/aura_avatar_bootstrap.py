from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from .aura_avatar_runtime import RUNTIME_JS, THREE_VERSION, THREE_VRM_VERSION

router = APIRouter(tags=["Aura Embodied Bootstrap"])

# Repair the guarded thinking-state scalar and add compatibility hooks so the embodied
# runtime follows speech played by existing pages as well as speech started directly by AuraAvatar.
PATCHED_RUNTIME_JS = RUNTIME_JS.replace(
    "this.state==='thinking'?.55:0",
    "(this.state==='thinking'?0.55:0)",
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
    const {el,label}=unique[i];if(!document.body.contains(el))continue;el.scrollIntoView({behavior:'smooth',block:'center',inline:'nearest'});await sleep(420);
    if(!el.id)el.id='aura-guide-'+Math.random().toString(36).slice(2,9);this.guideTo('#'+CSS.escape(el.id));await sleep(720);
    const custom=el.getAttribute('data-aura-guide');const line=custom||('This is '+label+'.');await this.speak(line);await sleep(180);
  }
  this.guideTarget=null;this.setState('idle');await this.speak('That is the main layout. I can guide you to any feature whenever you need me.');
};
const aura=new AuraEmbodiedRuntime();aura.init();
""",
)


def avatar_bootstrap_html() -> str:
    return f"""
<script type='importmap' id='aura-avatar-importmap'>
{{"imports":{{
  "three":"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/build/three.module.js",
  "three/addons/":"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/examples/jsm/",
  "@pixiv/three-vrm":"https://cdn.jsdelivr.net/npm/@pixiv/three-vrm@{THREE_VRM_VERSION}/lib/three-vrm.module.js"
}}}}
</script>
<script type='module' src='/aura/avatar/runtime-v3.js' id='aura-avatar-runtime'></script>
"""


@router.get("/aura/avatar/runtime-v3.js", include_in_schema=False)
def runtime_js() -> Response:
    return Response(PATCHED_RUNTIME_JS, media_type="text/javascript; charset=utf-8", headers={"Cache-Control": "private, max-age=300"})
