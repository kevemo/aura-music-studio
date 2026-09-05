from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from .aura_avatar_client_health import router as avatar_client_health_router
from .aura_avatar_validator import (
    GAZE_ANIMATION_ALIASES,
    GESTURE_ANIMATION_ALIASES,
    VISEME_ANIMATION_ALIASES,
    validate_aura_glb,
)

router = APIRouter(tags=["Aura Avatar"])
router.include_router(avatar_client_health_router)

BROWSER_3D_RENDERER_IMPLEMENTED = True
LAYERED_PERFORMANCE_RUNTIME_IMPLEMENTED = True
_DEFAULT_MODEL_VIEWER_MODULE = (
    "https://ajax.googleapis.com/ajax/libs/model-viewer/4.3.1/model-viewer.min.js"
)

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


def _renderer_module_url() -> str | None:
    raw = os.getenv("AURA_AVATAR_RENDERER_MODULE_URL")
    value = _DEFAULT_MODEL_VIEWER_MODULE if raw is None else raw.strip()
    if not value:
        return None
    if value.startswith("/") and not value.startswith("//"):
        return value
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Aura avatar renderer module must be a same-origin path or public HTTPS URL")
    return value


def _alias_payload(contract: dict[str, tuple[str, ...]]) -> dict[str, list[str]]:
    return {name: list(aliases) for name, aliases in contract.items()}


def avatar_status() -> dict:
    model = None
    config_error = None
    try:
        model = _model_path()
        model_installed = model.is_file() and model.suffix.lower() == ".glb"
    except Exception as exc:
        model_installed = False
        config_error = f"{type(exc).__name__}: {exc}"

    validation = validate_aura_glb(model) if model_installed and model is not None else {
        "exists": False,
        "valid_glb": False,
        "error": "Aura GLB asset is not installed",
        "warnings": [],
        "animations": [],
        "animation_state_matches": {},
        "morph_target_names": [],
        "viseme_animation_matches": {},
        "gaze_animation_matches": {},
        "gesture_animation_matches": {},
        "production_rig_ready": False,
        "layered_performance_clips_ready": False,
    }
    model_valid = bool(validation.get("valid_glb"))
    production_rig_ready = bool(validation.get("production_rig_ready"))

    try:
        renderer_module = _renderer_module_url()
        renderer_configured = bool(renderer_module and BROWSER_3D_RENDERER_IMPLEMENTED)
    except Exception as exc:
        renderer_module = None
        renderer_configured = False
        config_error = config_error or f"{type(exc).__name__}: {exc}"

    operator_validated = _truthy("AURA_AVATAR_3D_RENDERER_READY", False)
    enabled = _truthy("AURA_AVATAR_ENABLED", True)
    production_ready = bool(
        enabled
        and model_valid
        and production_rig_ready
        and renderer_configured
        and operator_validated
    )
    if production_ready:
        truthful_state = "production_3d_avatar_ready"
    elif model_installed and not model_valid:
        truthful_state = "model_asset_invalid"
    elif model_valid and renderer_configured and operator_validated and not production_rig_ready:
        truthful_state = "model_renderer_validated_performance_rig_incomplete"
    elif model_valid and renderer_configured:
        truthful_state = "model_and_renderer_configured_validation_pending"
    elif model_valid:
        truthful_state = "model_asset_valid_renderer_pending"
    elif renderer_configured:
        truthful_state = "renderer_ready_model_asset_missing"
    else:
        truthful_state = "runtime_ready_model_asset_missing"

    public_validation = {
        "valid_glb": bool(validation.get("valid_glb")),
        "glb_version": validation.get("glb_version"),
        "file_bytes": validation.get("file_bytes"),
        "skins": validation.get("skins", 0),
        "nodes": validation.get("nodes", 0),
        "meshes": validation.get("meshes", 0),
        "animations": list(validation.get("animations") or [])[:240],
        "animation_state_matches": validation.get("animation_state_matches") or {},
        "morph_target_names": list(validation.get("morph_target_names") or [])[:300],
        "morph_target_count": validation.get("morph_target_count", 0),
        "has_skin": bool(validation.get("has_skin")),
        "has_animations": bool(validation.get("has_animations")),
        "has_morph_targets": bool(validation.get("has_morph_targets")),
        "facial_rig_signal": bool(validation.get("facial_rig_signal")),
        "base_state_animation_ready": bool(validation.get("base_state_animation_ready")),
        "viseme_animation_matches": validation.get("viseme_animation_matches") or {},
        "viseme_animation_coverage": validation.get("viseme_animation_coverage", 0),
        "full_viseme_animation_set": bool(validation.get("full_viseme_animation_set")),
        "gaze_animation_matches": validation.get("gaze_animation_matches") or {},
        "gaze_animation_ready": bool(validation.get("gaze_animation_ready")),
        "gesture_animation_matches": validation.get("gesture_animation_matches") or {},
        "gesture_animation_ready": bool(validation.get("gesture_animation_ready")),
        "layered_performance_clips_ready": bool(validation.get("layered_performance_clips_ready")),
        "root_bone_signal": bool(validation.get("root_bone_signal")),
        "lod_signal": bool(validation.get("lod_signal")),
        "production_rig_ready": production_rig_ready,
        "warnings": list(validation.get("warnings") or [])[:60],
        "error": validation.get("error"),
    }

    return {
        "enabled": enabled,
        "software_runtime_connected": True,
        "state_bus_connected": True,
        "model_installed": model_installed,
        "model_valid": model_valid,
        "model_configured": model_valid,
        "model_validation": public_validation,
        "renderer_implemented": BROWSER_3D_RENDERER_IMPLEMENTED,
        "renderer_configured": renderer_configured,
        "renderer_connected": renderer_configured,
        "layered_performance_runtime_implemented": LAYERED_PERFORMANCE_RUNTIME_IMPLEMENTED,
        "operator_validated": operator_validated,
        "production_3d_ready": production_ready,
        "model_url": "/aura-intelligence/avatar/model.glb" if model_valid else None,
        "renderer_module_url": renderer_module,
        "config_error": config_error,
        "states": list(AVATAR_STATES),
        "rig_contract": {
            "format": "GLB/glTF 2.0",
            "humanoid_skeleton": True,
            "facial_blendshapes_expected": True,
            "viseme_or_audio_driven_mouth_expected": True,
            "full_layered_viseme_set_expected": list(VISEME_ANIMATION_ALIASES),
            "layered_gaze_expected": list(GAZE_ANIMATION_ALIASES),
            "layered_gestures_expected": list(GESTURE_ANIMATION_ALIASES),
            "lod_expected": True,
            "animations_expected": [
                "idle",
                "welcome",
                "listen",
                "think",
                "speak",
                "gesture",
                "celebrate",
                "warn",
                "recording_coach",
                "studio_engineer",
            ],
        },
        "performance_contract": {
            "protocol": "AuraHost.performance/v1",
            "viseme_aliases": _alias_payload(VISEME_ANIMATION_ALIASES),
            "gaze_aliases": _alias_payload(GAZE_ANIMATION_ALIASES),
            "gesture_aliases": _alias_payload(GESTURE_ANIMATION_ALIASES),
            "input_events": [
                "aura:viseme-input",
                "aura:gaze-input",
                "aura:gesture-input",
                "aura:speech-frame",
            ],
            "output_event": "aura:performance",
        },
        "renderer_contract": {
            "engine": "model-viewer",
            "module_pinned": True,
            "camera_controls": True,
            "animation_state_mapping": True,
            "animation_crossfade": True,
            "client_load_health": True,
            "durable_privacy_bounded_client_health_evidence": True,
            "client_health_can_promote_production_readiness": False,
            "layered_animation_api": True,
            "rig_authored_viseme_layering": True,
            "rig_authored_gaze_layering": True,
            "rig_authored_gesture_layering": True,
            "automatic_layered_blink": True,
            "direct_raw_morph_driver": False,
            "private_renderer_internals_used": False,
            "next_renderer_phase": "final production rig asset, performance tuning and device validation",
        },
        "truthful_state": truthful_state,
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
    validation = validate_aura_glb(model)
    if not validation.get("valid_glb"):
        raise HTTPException(409, "Aura production 3D model failed GLB structure validation")
    return FileResponse(
        model,
        media_type="model/gltf-binary",
        headers={"Cache-Control": "private, max-age=3600", "Content-Disposition": "inline"},
    )


AVATAR_SCRIPT = r"""
(()=>{
  const API='/aura-intelligence/api/avatar/status';
  let currentState='idle',modelViewer=null,availableAnimations=[],layeredPerformanceSupported=false,blinkTimer=null;
  let performanceContract={viseme_aliases:{},gaze_aliases:{},gesture_aliases:{}};
  const activeLayers={viseme:null,gaze:null,gesture:null};
  const animationAliases={
    idle:['idle','breathing','stand'],welcoming:['welcome','welcoming','greet','greeting'],listening:['listen','listening','attentive'],
    thinking:['think','thinking','ponder'],tool_running:['think','working','work','typing'],speaking:['speak','speaking','talk','talking'],
    celebrating:['celebrate','celebrating','happy','cheer'],warning:['warn','warning','concerned'],recording_coach:['recording_coach','coach','listen'],studio_engineer:['studio_engineer','engineer','working']
  };
  function safeState(value){const allowed=['idle','welcoming','listening','thinking','tool_running','speaking','celebrating','warning','recording_coach','studio_engineer'];return allowed.includes(value)?value:'idle'}
  function clamp(value,low=0,high=1){const n=Number(value);return Number.isFinite(n)?Math.max(low,Math.min(high,n)):low}
  function chooseFromAliases(names=[]){const lowered=availableAnimations.map(name=>String(name).toLowerCase());for(const wantedRaw of names||[]){const wanted=String(wantedRaw).toLowerCase();let i=lowered.indexOf(wanted);if(i>=0)return availableAnimations[i];i=lowered.findIndex(name=>name.includes(wanted));if(i>=0)return availableAnimations[i]}return null}
  function chooseAnimation(state){return chooseFromAliases(animationAliases[state]||[])||availableAnimations[0]||null}
  function applyAnimation(state){if(!modelViewer)return;const animation=chooseAnimation(state);if(!animation)return;try{if(modelViewer.animationName!==animation)modelViewer.animationName=animation;modelViewer.play?.({repetitions:Infinity})}catch(_){}}
  function emitPerformance(type,detail={}){document.dispatchEvent(new CustomEvent('aura:performance',{detail:{type,...detail}}))}
  function layerAliases(group,key){return performanceContract?.[group]?.[key]||[]}
  function clearLayer(channel,fade=.08){const animation=activeLayers[channel];if(!animation)return false;try{if(modelViewer&&typeof modelViewer.detachAnimation==='function')modelViewer.detachAnimation(animation,{fade});activeLayers[channel]=null;emitPerformance('layer_cleared',{channel,animation});return true}catch(_){activeLayers[channel]=null;return false}}
  function applyLayer(channel,aliases,options={}){if(!modelViewer||!layeredPerformanceSupported)return false;const animation=chooseFromAliases(aliases);if(!animation)return false;const fade=clamp(options.fade??.08,0,2);clearLayer(channel,fade);try{modelViewer.appendAnimation(animation,{repetitions:options.repetitions??Infinity,weight:clamp(options.weight??1),timeScale:clamp(options.timeScale??1,.05,4),fade,pingpong:!!options.pingpong});activeLayers[channel]=animation;emitPerformance('layer_applied',{channel,animation,weight:clamp(options.weight??1)});return true}catch(error){emitPerformance('layer_error',{channel,animation,error:String(error)});return false}}
  function setViseme(value='sil',options={}){const key=String(value||'sil').trim().toLowerCase();const aliases=layerAliases('viseme_aliases',key);if(!aliases.length){if(key==='sil')return clearLayer('viseme',options.fade??.05);return false}return applyLayer('viseme',aliases,{...options,repetitions:Infinity})}
  function setGaze(value='center',options={}){const key=String(value||'center').trim().toLowerCase();const aliases=layerAliases('gaze_aliases',key);if(!aliases.length){if(key==='center')return clearLayer('gaze',options.fade??.12);return false}return applyLayer('gaze',aliases,{...options,repetitions:Infinity})}
  function gesture(value,options={}){const key=String(value||'').trim().toLowerCase();const aliases=layerAliases('gesture_aliases',key);if(!aliases.length)return false;return applyLayer('gesture',aliases,{...options,repetitions:1,fade:options.fade??.1})}
  function clearPerformance(){clearLayer('viseme',.05);clearLayer('gaze',.12);clearLayer('gesture',.08);emitPerformance('cleared',{})}
  function speechFrame(detail={}){if(detail.speaking!==false&&currentState!=='speaking')setState('speaking',{phase:'performance_frame'});let applied=false;if(detail.viseme!=null)applied=setViseme(detail.viseme,{weight:detail.visemeWeight??detail.weight??1,fade:detail.fade??.04})||applied;if(detail.gaze!=null)applied=setGaze(detail.gaze,{weight:detail.gazeWeight??1,fade:detail.gazeFade??.12})||applied;if(detail.gesture)applied=gesture(detail.gesture,{weight:detail.gestureWeight??1,fade:detail.gestureFade??.1})||applied;if(detail.speaking===false){setViseme('sil',{fade:.05});if(currentState==='speaking')setState('idle',{phase:'performance_frame_end'})}emitPerformance('speech_frame',{applied,viseme:detail.viseme??null,gaze:detail.gaze??null,gesture:detail.gesture??null});return applied}
  function performanceStatus(){return{supported:layeredPerformanceSupported,active:{...activeLayers},contract:performanceContract,animations:[...availableAnimations]}}
  function scheduleBlink(){if(blinkTimer)clearTimeout(blinkTimer);blinkTimer=setTimeout(()=>{if(modelViewer&&layeredPerformanceSupported&&currentState!=='warning')gesture('blink',{weight:1,fade:.035});scheduleBlink()},3200+Math.random()*3600)}
  function setState(value,detail={}){const previous=currentState;currentState=safeState(value);const dock=document.getElementById('auraAvatarDock');if(dock){dock.dataset.state=currentState;const label=dock.querySelector('[data-aura-state]');if(label)label.textContent=currentState.replace('_',' ')}if(previous==='speaking'&&currentState!=='speaking')setViseme('sil',{fade:.05});applyAnimation(currentState);document.dispatchEvent(new CustomEvent('aura:state',{detail:{state:currentState,...detail}}))}
  window.AuraHost={get state(){return currentState},setState,on(handler){document.addEventListener('aura:state',handler);return()=>document.removeEventListener('aura:state',handler)},onPerformance(handler){document.addEventListener('aura:performance',handler);return()=>document.removeEventListener('aura:performance',handler)},get modelViewer(){return modelViewer},performance:{setViseme,setGaze,gesture,speechFrame,clear:clearPerformance,status:performanceStatus}};

  const style=document.createElement('style');style.textContent=`
    #auraAvatarDock{position:fixed;right:18px;bottom:142px;width:220px;z-index:54;border:1px solid #ffffff20;border-radius:22px;background:linear-gradient(160deg,#10162aef,#070914f4);box-shadow:0 18px 60px #000a;overflow:hidden;transition:.2s}
    #auraAvatarDock.min{width:58px;height:58px;border-radius:50%;bottom:150px}#auraAvatarDock.min .auraAvatarBody,#auraAvatarDock.min .auraAvatarMeta{display:none}
    .auraAvatarHead{display:flex;align-items:center;gap:8px;padding:8px 10px}.auraAvatarTitle{font-weight:900;font-size:.78rem;flex:1}.auraAvatarTitle small{display:block;color:#a9b2c8;font-size:.62rem;font-weight:600;text-transform:capitalize}.auraAvatarToggle{border:0;background:transparent;color:#a9b2c8;cursor:pointer}
    .auraAvatarBody{height:260px;position:relative;display:grid;place-items:center;background:radial-gradient(circle at 50% 38%,#9b70ff42,transparent 28%),radial-gradient(circle at 50% 72%,#58dfff22,transparent 42%),#050711;overflow:hidden}
    .auraAvatarBody model-viewer{width:100%;height:100%;background:transparent;--poster-color:transparent}.auraModelLoading{position:absolute;inset:0;display:grid;place-items:center;color:#a9b2c8;font-size:.7rem;padding:14px;text-align:center}
    .auraOrb{width:104px;height:126px;border-radius:50% 50% 44% 44%;background:radial-gradient(circle at 38% 30%,#fff8 0 4%,transparent 5%),radial-gradient(circle at 62% 30%,#fff8 0 4%,transparent 5%),radial-gradient(circle at 50% 38%,#b79aff,#6a45bf 43%,#211745 72%);box-shadow:0 0 42px #9b70ff77;position:relative;transform:perspective(350px) rotateY(-5deg);transition:.25s}
    .auraOrb:after{content:'';position:absolute;left:18%;right:18%;bottom:-46px;height:72px;border-radius:50% 50% 20% 20%;background:linear-gradient(145deg,#171b33,#593ea0 55%,#151a32);z-index:-1}.auraAvatarMeta{padding:8px 10px;color:#a9b2c8;font-size:.65rem;border-top:1px solid #ffffff12}
    #auraAvatarDock[data-state='listening'] .auraOrb{box-shadow:0 0 52px #58dfffaa;animation:auraListen 1.1s infinite alternate}#auraAvatarDock[data-state='thinking'] .auraOrb,#auraAvatarDock[data-state='tool_running'] .auraOrb{animation:auraThink 1.4s linear infinite}#auraAvatarDock[data-state='speaking'] .auraOrb{animation:auraSpeak .32s infinite alternate}#auraAvatarDock[data-state='celebrating'] .auraOrb{animation:auraCelebrate .7s infinite alternate}#auraAvatarDock[data-state='warning'] .auraOrb{box-shadow:0 0 48px #ff8fa688}
    @keyframes auraListen{to{transform:perspective(350px) rotateY(5deg) scale(1.03)}}@keyframes auraThink{to{filter:hue-rotate(32deg);transform:perspective(350px) rotateY(355deg)}}@keyframes auraSpeak{to{transform:perspective(350px) translateY(-2px) scaleY(1.025)}}@keyframes auraCelebrate{to{transform:translateY(-6px) scale(1.04)}}
    @media(max-width:820px){#auraAvatarDock{right:8px;bottom:132px;width:170px}.auraAvatarBody{height:190px}.auraOrb{transform:scale(.8)}}`;
  document.head.append(style);

  function loadModule(url){return new Promise((resolve,reject)=>{if(customElements.get('model-viewer'))return resolve();const s=document.createElement('script');s.type='module';s.src=url;s.onload=()=>customElements.whenDefined('model-viewer').then(resolve).catch(reject);s.onerror=()=>reject(new Error('Aura 3D renderer module failed to load'));document.head.append(s)})}
  async function mount3D(body,status,meta){try{await loadModule(status.renderer_module_url);modelViewer=document.createElement('model-viewer');modelViewer.src=status.model_url;modelViewer.alt='Aura 3D AI host';modelViewer.setAttribute('camera-controls','');modelViewer.setAttribute('interaction-prompt','none');modelViewer.setAttribute('shadow-intensity','0.8');modelViewer.setAttribute('exposure','1');modelViewer.setAttribute('tone-mapping','neutral');modelViewer.setAttribute('autoplay','');modelViewer.setAttribute('disable-tap','');modelViewer.animationCrossfadeDuration=180;body.replaceChildren(modelViewer);const loading=document.createElement('div');loading.className='auraModelLoading';loading.textContent='Loading Aura 3D host…';body.append(loading);modelViewer.addEventListener('load',()=>{loading.remove();availableAnimations=Array.from(modelViewer.availableAnimations||[]);layeredPerformanceSupported=typeof modelViewer.appendAnimation==='function'&&typeof modelViewer.detachAnimation==='function';applyAnimation(currentState);scheduleBlink();const rigReady=!!status.model_validation?.layered_performance_clips_ready;if(status.production_3d_ready)meta.textContent='Production 3D Aura · layered performance validated';else if(!rigReady)meta.textContent='Aura 3D model loaded · layered performance rig incomplete';else meta.textContent='Aura 3D model loaded · deployment validation pending';document.dispatchEvent(new CustomEvent('aura:3d-ready',{detail:{animations:availableAnimations,productionReady:!!status.production_3d_ready,layeredPerformanceSupported,performanceRigReady:rigReady}}))});modelViewer.addEventListener('error',()=>{if(blinkTimer)clearTimeout(blinkTimer);blinkTimer=null;clearPerformance();modelViewer=null;body.innerHTML='<div class="auraOrb" aria-label="Aura Core host state visual"></div>';meta.textContent='Aura Core state visual · 3D model failed to load';setState('warning',{reason:'3d_model_load_failed'})})}catch(error){body.innerHTML='<div class="auraOrb" aria-label="Aura Core host state visual"></div>';meta.textContent='Aura Core state visual · 3D renderer unavailable';setState('warning',{reason:String(error)})}}

  async function mount(){let status={software_runtime_connected:true,model_valid:false,renderer_configured:false};try{status=await fetch(API,{credentials:'same-origin'}).then(r=>r.json())}catch(_){}if(status.enabled===false)return;performanceContract=status.performance_contract||performanceContract;let metaText='Aura Core state visual · production 3D rig pending';if(status.renderer_configured&&!status.model_installed)metaText='3D renderer ready · Aura rig asset pending';if(status.model_installed&&!status.model_valid)metaText='Aura GLB detected · structure validation failed';if(status.model_valid&&!status.renderer_configured)metaText='Valid Aura GLB · renderer unavailable';if(status.model_valid&&status.renderer_configured)metaText=status.model_validation?.layered_performance_clips_ready?'Aura 3D host · loading':'Aura 3D host · performance rig incomplete';const dock=document.createElement('div');dock.id='auraAvatarDock';dock.dataset.state='idle';dock.innerHTML=`<div class="auraAvatarHead"><div class="auraAvatarTitle">Aura<small data-aura-state>idle</small></div><button class="auraAvatarToggle" title="Minimise Aura">−</button></div><div class="auraAvatarBody"><div class="auraOrb" aria-label="Aura Core host state visual"></div></div><div class="auraAvatarMeta"></div>`;document.body.append(dock);const body=dock.querySelector('.auraAvatarBody'),meta=dock.querySelector('.auraAvatarMeta');meta.textContent=metaText;dock.querySelector('.auraAvatarToggle').onclick=()=>dock.classList.toggle('min');if(status.model_valid&&status.renderer_configured&&status.model_url&&status.renderer_module_url)mount3D(body,status,meta);setState('welcoming');setTimeout(()=>setState('idle'),1100)}
  mount();
  document.addEventListener('aura:viseme-input',event=>setViseme(event.detail?.viseme??event.detail?.value??'sil',event.detail||{}));
  document.addEventListener('aura:gaze-input',event=>setGaze(event.detail?.gaze??event.detail?.value??'center',event.detail||{}));
  document.addEventListener('aura:gesture-input',event=>gesture(event.detail?.gesture??event.detail?.value,event.detail||{}));
  document.addEventListener('aura:speech-frame',event=>speechFrame(event.detail||{}));
  if(typeof send==='function'){const baseSend=send;send=async function(...args){setState('thinking');try{return await baseSend(...args)}catch(error){setState('warning',{error:String(error)});throw error}finally{if(currentState!=='speaking')setState('idle')}}}
  if(typeof mic==='function'){const baseMic=mic;mic=async function(...args){setState(recording?'thinking':'listening');try{return await baseMic(...args)}finally{setTimeout(()=>{if(currentState==='listening')setState('idle')},500)}}}
  const observer=new MutationObserver(()=>{const tools=document.querySelector('.toolLine:not(:empty)'),thinking=document.querySelector('.thinking');if(tools)setState('tool_running');else if(thinking&&currentState!=='speaking')setState('thinking')});observer.observe(document.body,{subtree:true,childList:true,characterData:true});
  window.addEventListener('beforeunload',()=>{if(blinkTimer)clearTimeout(blinkTimer);blinkTimer=null;clearPerformance()});
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
        runtime_marker = "<script src='/aura-intelligence/avatar-runtime.js'></script>"
        health_marker = "<script src='/aura-intelligence/avatar-client-health.js'></script>"
        markers = runtime_marker + health_marker
        if runtime_marker not in text:
            text = text.replace("</body>", markers + "</body>")
        elif health_marker not in text:
            text = text.replace(runtime_marker, markers)
        encoded = text.encode("utf-8")
        migrated = Response(content=encoded, status_code=response.status_code, background=response.background)
        raw_headers = [(key, value) for key, value in response.raw_headers if key.lower() != b"content-length"]
        raw_headers.append((b"content-length", str(len(encoded)).encode("ascii")))
        migrated.raw_headers = raw_headers
        return migrated


__all__ = [
    "router",
    "AuraAvatarRuntimeMiddleware",
    "avatar_status",
    "AVATAR_STATES",
    "_model_path",
    "_renderer_module_url",
    "BROWSER_3D_RENDERER_IMPLEMENTED",
    "LAYERED_PERFORMANCE_RUNTIME_IMPLEMENTED",
]
