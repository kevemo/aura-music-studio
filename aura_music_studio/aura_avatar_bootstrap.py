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
