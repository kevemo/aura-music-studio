from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

router = APIRouter(tags=["Aura Avatar"])

AVATAR_STATES = (
    "idle",
    "welcoming",
    "listening",
    "thinking",
    "tool_running",
    "speaking",
    "celebrating",
    "warning",
    "recording_coach",
    "studio_engineer",
)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _asset_root() -> Path:
    return Path(os.getenv("AURA_AVATAR_ASSET_DIR", "aura_music_studio/static/aura")).resolve()


def _model_path() -> Path:
    root = _asset_root()
    configured = os.getenv("AURA_AVATAR_MODEL_PATH", "").strip()
    target = Path(configured).expanduser() if configured else root / "aura.glb"
    if not target.is_absolute():
        target = root / target
    target = target.resolve()
    if target != root and root not in target.parents:
        raise ValueError("Aura avatar model must resolve under AURA_AVATAR_ASSET_DIR")
    return target


def avatar_status() -> dict:
    try:
        model = _model_path()
        model_configured = model.is_file() and model.suffix.lower() == ".glb"
        config_error = None
    except Exception as exc:
        model_configured = False
        config_error = f"{type(exc).__name__}: {exc}"
    return {
        "enabled": _truthy("AURA_AVATAR_ENABLED", True),
        "software_runtime_connected": True,
        "model_configured": model_configured,
        "model_url": "/aura-intelligence/avatar/model.glb" if model_configured else None,
        "config_error": config_error,
        "states": list(AVATAR_STATES),
        "rig_contract": {
            "format": "GLB/glTF 2.0",
            "humanoid_skeleton": True,
            "facial_blendshapes_expected": True,
            "viseme_or_audio_driven_mouth_expected": True,
            "lod_expected": True,
            "animations_expected": ["idle", "welcome", "listen", "think", "speak", "gesture", "celebrate", "warn"],
        },
        "truthful_state": "production_3d_model_ready" if model_configured else "runtime_ready_model_asset_missing",
    }


@router.get("/aura-intelligence/api/avatar/status")
def get_avatar_status(request: Request):
    _member(request)
    return avatar_status()


@router.get("/aura-intelligence/avatar/model.glb", include_in_schema=False)
def avatar_model(request: Request):
    _member(request)
    if not _truthy("AURA_AVATAR_ENABLED", True):
        raise HTTPException(404, "Aura avatar is disabled")
    try:
        model = _model_path()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not model.is_file() or model.suffix.lower() != ".glb":
        raise HTTPException(404, "Aura production 3D model is not installed on this host")
    return FileResponse(
        model,
        media_type="model/gltf-binary",
        headers={"Cache-Control": "private, max-age=3600", "Content-Disposition": "inline"},
    )


AVATAR_SCRIPT = r"""
(()=>{
  const API='/aura-intelligence/api/avatar/status';
  let currentState='idle';
  function safeState(value){const allowed=['idle','welcoming','listening','thinking','tool_running','speaking','celebrating','warning','recording_coach','studio_engineer'];return allowed.includes(value)?value:'idle'}
  function setState(value,detail={}){
    currentState=safeState(value);const dock=document.getElementById('auraAvatarDock');if(dock){dock.dataset.state=currentState;const label=dock.querySelector('[data-aura-state]');if(label)label.textContent=currentState.replace('_',' ')}
    document.dispatchEvent(new CustomEvent('aura:state',{detail:{state:currentState,...detail}}));
  }
  window.AuraHost={get state(){return currentState},setState,on(handler){document.addEventListener('aura:state',handler);return()=>document.removeEventListener('aura:state',handler)}};

  const style=document.createElement('style');style.textContent=`
    #auraAvatarDock{position:fixed;right:18px;bottom:142px;width:190px;z-index:54;border:1px solid #ffffff20;border-radius:22px;background:linear-gradient(160deg,#10162aeF,#070914f4);box-shadow:0 18px 60px #000a;overflow:hidden;transition:.2s}
    #auraAvatarDock.min{width:58px;height:58px;border-radius:50%;bottom:150px}#auraAvatarDock.min .auraAvatarBody,#auraAvatarDock.min .auraAvatarMeta{display:none}
    .auraAvatarHead{display:flex;align-items:center;gap:8px;padding:8px 10px}.auraAvatarTitle{font-weight:900;font-size:.78rem;flex:1}.auraAvatarTitle small{display:block;color:#a9b2c8;font-size:.62rem;font-weight:600;text-transform:capitalize}.auraAvatarToggle{border:0;background:transparent;color:#a9b2c8;cursor:pointer}
    .auraAvatarBody{height:190px;position:relative;display:grid;place-items:center;background:radial-gradient(circle at 50% 38%,#9b70ff42,transparent 28%),radial-gradient(circle at 50% 72%,#58dfff22,transparent 42%),#050711}
    .auraOrb{width:104px;height:126px;border-radius:50% 50% 44% 44%;background:radial-gradient(circle at 38% 30%,#fff8 0 4%,transparent 5%),radial-gradient(circle at 62% 30%,#fff8 0 4%,transparent 5%),radial-gradient(circle at 50% 38%,#b79aff,#6a45bf 43%,#211745 72%);box-shadow:0 0 42px #9b70ff77;position:relative;transform:perspective(350px) rotateY(-5deg);transition:.25s}
    .auraOrb:after{content:'';position:absolute;left:18%;right:18%;bottom:-46px;height:72px;border-radius:50% 50% 20% 20%;background:linear-gradient(145deg,#171b33,#593ea0 55%,#151a32);z-index:-1}.auraAvatarMeta{padding:8px 10px;color:#a9b2c8;font-size:.65rem;border-top:1px solid #ffffff12}
    #auraAvatarDock[data-state='listening'] .auraOrb{box-shadow:0 0 52px #58dfffaa;animation:auraListen 1.1s infinite alternate}#auraAvatarDock[data-state='thinking'] .auraOrb,#auraAvatarDock[data-state='tool_running'] .auraOrb{animation:auraThink 1.4s linear infinite}#auraAvatarDock[data-state='speaking'] .auraOrb{animation:auraSpeak .32s infinite alternate}#auraAvatarDock[data-state='celebrating'] .auraOrb{animation:auraCelebrate .7s infinite alternate}#auraAvatarDock[data-state='warning'] .auraOrb{box-shadow:0 0 48px #ff8fa688}
    @keyframes auraListen{to{transform:perspective(350px) rotateY(5deg) scale(1.03)}}@keyframes auraThink{to{filter:hue-rotate(32deg);transform:perspective(350px) rotateY(355deg)}}@keyframes auraSpeak{to{transform:perspective(350px) translateY(-2px) scaleY(1.025)}}@keyframes auraCelebrate{to{transform:translateY(-6px) scale(1.04)}}
    @media(max-width:820px){#auraAvatarDock{right:8px;bottom:132px;width:150px}.auraAvatarBody{height:145px}.auraOrb{transform:scale(.8)}}`;
  document.head.append(style);

  async function mount(){
    let status={software_runtime_connected:true,model_configured:false};try{status=await fetch(API,{credentials:'same-origin'}).then(r=>r.json())}catch(_){}
    if(status.enabled===false)return;
    const dock=document.createElement('div');dock.id='auraAvatarDock';dock.dataset.state='idle';dock.innerHTML=`<div class="auraAvatarHead"><div class="auraAvatarTitle">Aura<small data-aura-state>idle</small></div><button class="auraAvatarToggle" title="Minimise Aura">−</button></div><div class="auraAvatarBody"><div class="auraOrb" aria-label="Aura host visual"></div></div><div class="auraAvatarMeta">${status.model_configured?'3D rig connected':'Aura Core visual · production 3D rig pending'}</div>`;document.body.append(dock);
    dock.querySelector('.auraAvatarToggle').onclick=()=>dock.classList.toggle('min');setState('welcoming');setTimeout(()=>setState('idle'),1100);
  }
  mount();

  // Bind the visual host to the existing auditable chat runtime. A 3D renderer can subscribe
  // to the same aura:state events without changing the AI/tool execution layer.
  if(typeof send==='function'){
    const baseSend=send;send=async function(...args){setState('thinking');try{return await baseSend(...args)}catch(error){setState('warning',{error:String(error)});throw error}finally{if(currentState!=='speaking')setState('idle')}};
  }
  if(typeof mic==='function'){
    const baseMic=mic;mic=async function(...args){setState(recording?'thinking':'listening');try{return await baseMic(...args)}finally{setTimeout(()=>{if(currentState==='listening')setState('idle')},500)}};
  }
  const observer=new MutationObserver(()=>{
    const tools=document.querySelector('.toolLine:not(:empty)');const thinking=document.querySelector('.thinking');
    if(tools)setState('tool_running');else if(thinking&&currentState!=='speaking')setState('thinking');
  });
  observer.observe(document.body,{subtree:true,childList:true,characterData:true});
})();
"""


@router.get("/aura-intelligence/avatar-runtime.js", include_in_schema=False)
def avatar_runtime_script():
    return Response(content=AVATAR_SCRIPT, media_type="application/javascript", headers={"Cache-Control": "no-store"})


class AuraAvatarRuntimeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/aura-intelligence" or request.method.upper() != "GET":
            return response
        content_type = (response.headers.get("content-type") or "").lower()
        if not content_type.startswith("text/html"):
            return response
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return Response(content=body, status_code=response.status_code, headers=dict(response.headers), background=response.background)
        marker = "<script src='/aura-intelligence/avatar-runtime.js'></script>"
        if marker not in text:
            text = text.replace("</body>", marker + "</body>")
        encoded = text.encode("utf-8")
        migrated = Response(content=encoded, status_code=response.status_code, background=response.background)
        raw_headers = [(key, value) for key, value in response.raw_headers if key.lower() != b"content-length"]
        raw_headers.append((b"content-length", str(len(encoded)).encode("ascii")))
        migrated.raw_headers = raw_headers
        return migrated


__all__ = ["router", "AuraAvatarRuntimeMiddleware", "avatar_status", "AVATAR_STATES"]
