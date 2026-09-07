from __future__ import annotations

import json

from .game_forge_asset_bindings import binding_runtime_payload
from .game_forge_assets import runtime_asset_manifest
from .game_forge_models import GameDNA
from .game_forge_world import GameWorldDNA, world_stream_index


_PBR_MAP_SLOTS = (
    "base_color",
    "normal",
    "metallic",
    "roughness",
    "emissive",
    "opacity",
    "ao",
    "height",
)


def _runtime_payload(game: GameDNA, world: GameWorldDNA) -> dict:
    entities = []
    for row in world.entities:
        if not row.visible or not row.active:
            continue
        entities.append(
            {
                "id": row.id,
                "name": row.name,
                "kind": row.kind,
                "position": row.transform.position.model_dump(mode="json"),
                "rotation": row.transform.rotation_deg.model_dump(mode="json"),
                "scale": row.transform.scale.model_dump(mode="json"),
                "asset_ref": row.asset_ref,
                "material": row.material.model_dump(mode="json") if row.material else None,
                "light": row.light.model_dump(mode="json") if row.light else None,
                "physics": row.physics.model_dump(mode="json") if row.physics else None,
                "behaviors": [x.model_dump(mode="json") for x in row.behaviors],
                "lod_group": row.lod_group,
                "metadata": dict(row.metadata),
            }
        )
    return {
        "title": game.title,
        "genre": game.genre,
        "engine": "aura3d",
        "runtime_version": 2,
        "world_id": world.world_id,
        "world_revision": world.revision,
        "terrain": world.terrain.model_dump(mode="json"),
        "environment": world.environment.model_dump(mode="json"),
        "performance": world.performance.model_dump(mode="json"),
        "stream_index": world_stream_index(world),
        "entities": entities,
        "assets": runtime_asset_manifest(game.id),
        "bindings": binding_runtime_payload(game.id, world=world),
        "runtime_contract": {
            "native_aura_renderer": True,
            "generated_game_code_executed": False,
            "network_access": False,
            "same_origin_verified_media_only": True,
            "explicit_world_dna_bindings": True,
            "pbr_material_maps": list(_PBR_MAP_SLOTS),
            "height_map_mode": "micro_parallax",
            "spatial_audio": True,
            "spatial_audio_model": "webaudio_hrtf",
            "separate_music_and_sfx_buses": True,
            "external_engine": None,
        },
    }


def render_aura3d_playtest(game: GameDNA, world: GameWorldDNA, *, csp: str) -> str:
    """Render Aura3D v2 from closed World/Asset DNA.

    The runtime executes only reviewed Pulsar code. Creator data is serialized as configuration,
    verified media stays same-origin, and CSP keeps JavaScript network access disabled. Aura3D v2
    consumes explicit PBR maps and supports HRTF spatial audio assigned to world entities.
    """
    payload = json.dumps(_runtime_payload(game, world), ensure_ascii=False).replace("</", "<\\/")
    template = """<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>
<meta http-equiv='Content-Security-Policy' content="__CSP__">
<title>Aura Game Engine 3D</title><style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#050711;color:#fff;font-family:Inter,system-ui,sans-serif}canvas{display:block;width:100%;height:100%;touch-action:none}#hud{position:fixed;left:12px;top:12px;z-index:4;background:#060914d9;border:1px solid #ffffff26;border-radius:12px;padding:9px 11px;max-width:min(680px,90vw);backdrop-filter:blur(8px)}#hud b{display:block}#hud small{display:block;color:#bec6d8;line-height:1.35}#status{position:fixed;right:12px;bottom:12px;z-index:4;background:#060914d9;border:1px solid #ffffff26;border-radius:10px;padding:7px 9px;color:#dce4f4;font-size:12px}#fallback{display:none;position:fixed;inset:0;padding:32px;place-items:center;text-align:center;background:radial-gradient(circle at 50% 30%,#241446,#050711 60%);z-index:9}#fallback div{max-width:680px}#media-controls{position:fixed;left:12px;bottom:12px;z-index:6;display:flex;gap:7px;flex-wrap:wrap}#media-controls button{background:#0c1324e8;color:#fff;border:1px solid #ffffff33;border-radius:9px;padding:8px 10px;font-weight:800;cursor:pointer}#cutscene{position:fixed;inset:6%;z-index:8;width:88%;height:88%;object-fit:contain;background:#000;border:1px solid #ffffff33;border-radius:14px;box-shadow:0 24px 80px #000c}[hidden]{display:none!important}
</style></head><body><canvas id='game'></canvas><div id='hud'><b id='title'></b><small id='meta'></small><small>WASD / arrows move · drag to orbit camera · mouse wheel zoom</small></div><div id='status'>Starting Aura3D v2…</div><div id='media-controls'><button id='audio-toggle' hidden>Enable game audio</button><button id='video-toggle' hidden>Play cutscene</button></div><video id='cutscene' controls playsinline hidden></video><div id='fallback'><div><h2>Aura3D needs WebGL2 on this browser.</h2><p>This device can still use compatible 2D/fallback playtests. WebGPU acceleration remains an enhancement path rather than a requirement.</p></div></div><script>
'use strict';
const cfg=__PAYLOAD__;
const canvas=document.getElementById('game'),statusEl=document.getElementById('status');
document.getElementById('title').textContent=cfg.title;
document.getElementById('meta').textContent=`${cfg.genre} · Aura Game Engine 3D v2 · World r${cfg.world_revision}`;
const media=Array.isArray(cfg.assets)?cfg.assets:[],worldBindings=cfg.bindings?.world||{},entityBindings=cfg.bindings?.entities||{};
const role=a=>String(a?.role||'').toLowerCase();
const heuristicImage=media.find(a=>a.kind==='image'&&/(texture|terrain|surface|world|environment|background)/.test(role(a)))||media.find(a=>a.kind==='image');
const soundtrackAsset=worldBindings.soundtrack||media.find(a=>(a.kind==='music'||a.kind==='audio')&&/(soundtrack|music|ambient|theme|score)/.test(role(a)))||null;
const videoAsset=worldBindings.cutscene||media.find(a=>a.kind==='video'&&/(cutscene|intro|cinematic|background)/.test(role(a)))||media.find(a=>a.kind==='video');
if(videoAsset){const v=document.getElementById('cutscene'),b=document.getElementById('video-toggle');v.src=videoAsset.media_url;v.preload='metadata';b.hidden=false;b.addEventListener('click',async()=>{v.hidden=false;try{await v.play()}catch(_e){v.controls=true}});v.addEventListener('ended',()=>{v.hidden=true});v.addEventListener('dblclick',()=>{v.pause();v.hidden=true})}

// Audio is user-activated and stays on verified same-origin media. Music and positional SFX use
// independent buses so future mixer controls can change one without rebuilding World DNA.
let audioContext=null,masterGain=null,musicGain=null,sfxGain=null,soundtrack=null,soundtrackNode=null,audioStarted=false;
const spatialSources=[];
function audioBehavior(e){return (e.behaviors||[]).find(x=>x.op==='audio_zone')?.params||{}}
function sourceAssetForEntity(e){return entityBindings[e.id]?.audio||null}
function setAudioPosition(node,p){if(node.positionX){node.positionX.value=Number(p.x||0);node.positionY.value=Number(p.y||0);node.positionZ.value=Number(p.z||0)}else if(node.setPosition)node.setPosition(Number(p.x||0),Number(p.y||0),Number(p.z||0))}
function setListener(eye,target){if(!audioContext)return;const l=audioContext.listener,fx=target[0]-eye[0],fy=target[1]-eye[1],fz=target[2]-eye[2];if(l.positionX){l.positionX.value=eye[0];l.positionY.value=eye[1];l.positionZ.value=eye[2];l.forwardX.value=fx;l.forwardY.value=fy;l.forwardZ.value=fz;l.upX.value=0;l.upY.value=1;l.upZ.value=0}else{l.setPosition(eye[0],eye[1],eye[2]);l.setOrientation(fx,fy,fz,0,1,0)}}
async function initAudio(){if(audioContext){if(audioContext.state==='suspended')await audioContext.resume();return}const AC=window.AudioContext||window.webkitAudioContext;if(!AC)return;audioContext=new AC();masterGain=audioContext.createGain();musicGain=audioContext.createGain();sfxGain=audioContext.createGain();musicGain.gain.value=.72;sfxGain.gain.value=1;musicGain.connect(masterGain);sfxGain.connect(masterGain);masterGain.connect(audioContext.destination);
if(soundtrackAsset){soundtrack=new Audio(soundtrackAsset.media_url);soundtrack.preload='metadata';soundtrack.loop=true;soundtrackNode=audioContext.createMediaElementSource(soundtrack);soundtrackNode.connect(musicGain);try{await soundtrack.play()}catch(_e){}}
for(const e of cfg.entities){const asset=sourceAssetForEntity(e);if(!asset)continue;const params=audioBehavior(e),element=new Audio(asset.media_url);element.preload='metadata';element.loop=params.loop!==false;const source=audioContext.createMediaElementSource(element),gain=audioContext.createGain(),panner=audioContext.createPanner();gain.gain.value=Math.max(0,Math.min(4,Number(params.volume??1)));panner.panningModel='HRTF';panner.distanceModel='inverse';panner.refDistance=Math.max(.1,Number(params.ref_distance??2));panner.maxDistance=Math.max(panner.refDistance,Number(params.max_distance??80));panner.rolloffFactor=Math.max(0,Number(params.rolloff??1));setAudioPosition(panner,e.position||{});source.connect(gain);gain.connect(panner);panner.connect(sfxGain);spatialSources.push({entity:e,asset,element,source,gain,panner});try{await element.play()}catch(_e){}}
audioStarted=false}
const audioButton=document.getElementById('audio-toggle');if(soundtrackAsset||cfg.entities.some(e=>sourceAssetForEntity(e))){audioButton.hidden=false;audioButton.addEventListener('click',async()=>{await initAudio();if(!audioContext)return;if(audioContext.state==='running'&&audioStarted){await audioContext.suspend();audioButton.textContent='Resume game audio'}else{await audioContext.resume();audioStarted=true;audioButton.textContent='Pause game audio'}})}

const gl=canvas.getContext('webgl2',{alpha:false,antialias:true,powerPreference:'high-performance'});
if(!gl){document.getElementById('fallback').style.display='grid';canvas.style.display='none';statusEl.textContent='WebGL2 unavailable';throw new Error('WebGL2 unavailable')}
const vs=`#version 300 es
precision highp float;
layout(location=0) in vec3 aPos;
layout(location=1) in vec3 aNormal;
uniform mat4 uModel;
uniform mat4 uViewProj;
out vec3 vWorldPos;
out vec3 vNormal;
out vec2 vUV;
void main(){vec4 world=uModel*vec4(aPos,1.0);vWorldPos=world.xyz;vNormal=normalize(mat3(uModel)*aNormal);vUV=aPos.xz*.5+.5;gl_Position=uViewProj*world;}`;
const fs=`#version 300 es
precision highp float;
in vec3 vWorldPos;
in vec3 vNormal;
in vec2 vUV;
uniform vec3 uBaseColor;
uniform vec3 uLightDir;
uniform vec3 uLightColor;
uniform vec3 uAmbient;
uniform vec3 uCameraPos;
uniform float uMetallic;
uniform float uRoughness;
uniform float uEmissiveStrength;
uniform float uOpacity;
uniform float uExposure;
uniform int uShaderMode;
uniform sampler2D uBaseColorMap;uniform float uUseBaseColorMap;
uniform sampler2D uNormalMap;uniform float uUseNormalMap;
uniform sampler2D uMetallicMap;uniform float uUseMetallicMap;
uniform sampler2D uRoughnessMap;uniform float uUseRoughnessMap;
uniform sampler2D uEmissiveMap;uniform float uUseEmissiveMap;
uniform sampler2D uOpacityMap;uniform float uUseOpacityMap;
uniform sampler2D uAOMap;uniform float uUseAOMap;
uniform sampler2D uHeightMap;uniform float uUseHeightMap;
out vec4 outColor;
const float PI=3.14159265359;
mat3 cotangentFrame(vec3 N,vec3 p,vec2 uv){vec3 dp1=dFdx(p),dp2=dFdy(p);vec2 duv1=dFdx(uv),duv2=dFdy(uv);vec3 dp2perp=cross(dp2,N),dp1perp=cross(N,dp1);vec3 T=dp2perp*duv1.x+dp1perp*duv2.x;vec3 B=dp2perp*duv1.y+dp1perp*duv2.y;float invmax=inversesqrt(max(dot(T,T),dot(B,B))+1e-8);return mat3(T*invmax,B*invmax,N);}
float distributionGGX(vec3 N,vec3 H,float r){float a=r*r,a2=a*a,NdotH=max(dot(N,H),0.0),d=NdotH*NdotH*(a2-1.0)+1.0;return a2/max(PI*d*d,1e-5);}
float geometrySchlick(float NdotV,float r){float k=(r+1.0);k=k*k/8.0;return NdotV/(NdotV*(1.0-k)+k);}
float geometrySmith(vec3 N,vec3 V,vec3 L,float r){return geometrySchlick(max(dot(N,V),0.0),r)*geometrySchlick(max(dot(N,L),0.0),r);}
vec3 fresnelSchlick(float cosTheta,vec3 F0){return F0+(1.0-F0)*pow(clamp(1.0-cosTheta,0.0,1.0),5.0);}
void main(){vec3 V=normalize(uCameraPos-vWorldPos);vec2 uv=vUV;if(uUseHeightMap>.5){float h=texture(uHeightMap,uv).r-.5;uv+=V.xz*h*.03;}vec4 baseSample=texture(uBaseColorMap,uv);vec3 albedo=mix(uBaseColor,baseSample.rgb,clamp(uUseBaseColorMap,0.0,1.0)*.92);float alpha=uOpacity;if(uUseBaseColorMap>.5)alpha*=baseSample.a;if(uUseOpacityMap>.5)alpha*=texture(uOpacityMap,uv).r;vec3 N=normalize(vNormal);if(uUseNormalMap>.5){vec3 mapped=texture(uNormalMap,uv).xyz*2.0-1.0;N=normalize(cotangentFrame(N,vWorldPos,uv)*mapped);}float metallic=clamp(uMetallic*(uUseMetallicMap>.5?texture(uMetallicMap,uv).r:1.0),0.0,1.0);float rough=clamp(uRoughness*(uUseRoughnessMap>.5?texture(uRoughnessMap,uv).r:1.0),.04,1.0);float ao=uUseAOMap>.5?texture(uAOMap,uv).r:1.0;vec3 emission=uEmissiveStrength*(uUseEmissiveMap>.5?texture(uEmissiveMap,uv).rgb:albedo);if(uShaderMode==1){outColor=vec4((albedo+emission)*uExposure,alpha);return;}vec3 L=normalize(-uLightDir),H=normalize(V+L);float NdotL=max(dot(N,L),0.0),NdotV=max(dot(N,V),0.0);if(uShaderMode==2)NdotL=floor(NdotL*4.0)/3.0;vec3 F0=mix(vec3(.04),albedo,metallic),F=fresnelSchlick(max(dot(H,V),0.0),F0);float D=distributionGGX(N,H,rough),G=geometrySmith(N,V,L,rough);vec3 spec=(D*G*F)/max(4.0*NdotV*NdotL,.001);vec3 kS=F,kD=(vec3(1.0)-kS)*(1.0-metallic);vec3 direct=(kD*albedo/PI+spec)*uLightColor*NdotL;vec3 ambient=uAmbient*albedo*ao*(.35+.3*max(N.y,0.0));vec3 color=(ambient+direct+emission)*uExposure;color=color/(color+vec3(1.0));color=pow(color,vec3(1.0/2.2));outColor=vec4(color,clamp(alpha,0.0,1.0));}`;
function shader(type,src){const s=gl.createShader(type);gl.shaderSource(s,src);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(s)||'shader compile failed');return s}
const program=gl.createProgram();gl.attachShader(program,shader(gl.VERTEX_SHADER,vs));gl.attachShader(program,shader(gl.FRAGMENT_SHADER,fs));gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(program)||'program link failed');gl.useProgram(program);
const uniform=n=>gl.getUniformLocation(program,n),uModel=uniform('uModel'),uViewProj=uniform('uViewProj'),uBaseColor=uniform('uBaseColor'),uLightDir=uniform('uLightDir'),uLightColor=uniform('uLightColor'),uAmbient=uniform('uAmbient'),uCameraPos=uniform('uCameraPos'),uMetallic=uniform('uMetallic'),uRoughness=uniform('uRoughness'),uEmissiveStrength=uniform('uEmissiveStrength'),uOpacity=uniform('uOpacity'),uExposure=uniform('uExposure'),uShaderMode=uniform('uShaderMode');
const MAPS=[['base_color','uBaseColorMap','uUseBaseColorMap'],['normal','uNormalMap','uUseNormalMap'],['metallic','uMetallicMap','uUseMetallicMap'],['roughness','uRoughnessMap','uUseRoughnessMap'],['emissive','uEmissiveMap','uUseEmissiveMap'],['opacity','uOpacityMap','uUseOpacityMap'],['ao','uAOMap','uUseAOMap'],['height','uHeightMap','uUseHeightMap']].map((x,i)=>({slot:x[0],unit:i,sampler:uniform(x[1]),use:uniform(x[2])}));for(const m of MAPS)gl.uniform1i(m.sampler,m.unit);
const cube=new Float32Array([-1,-1,1,0,0,1,1,-1,1,0,0,1,1,1,1,0,0,1,-1,-1,1,0,0,1,1,1,1,0,0,1,-1,1,1,0,0,1,1,-1,-1,0,0,-1,-1,-1,-1,0,0,-1,-1,1,-1,0,0,-1,1,-1,-1,0,0,-1,-1,1,-1,0,0,-1,1,1,-1,0,0,-1,-1,1,1,0,1,0,1,1,1,0,1,0,1,1,-1,0,1,0,-1,1,1,0,1,0,1,1,-1,0,1,0,-1,1,-1,0,1,0,-1,-1,-1,0,-1,0,1,-1,-1,0,-1,0,1,-1,1,0,-1,0,-1,-1,-1,0,-1,0,1,-1,1,0,-1,0,-1,-1,1,0,-1,0,1,-1,1,1,0,0,1,-1,-1,1,0,0,1,1,-1,1,0,0,1,-1,1,1,0,0,1,-1,1,1,0,0,1,1,1,1,0,0,-1,-1,-1,-1,0,0,-1,-1,1,-1,0,0,-1,1,1,-1,0,0,-1,-1,-1,-1,0,0,-1,1,1,-1,0,0,-1,1,-1,-1,0,0]);
const vao=gl.createVertexArray();gl.bindVertexArray(vao);const vbo=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,vbo);gl.bufferData(gl.ARRAY_BUFFER,cube,gl.STATIC_DRAW);gl.enableVertexAttribArray(0);gl.vertexAttribPointer(0,3,gl.FLOAT,false,24,0);gl.enableVertexAttribArray(1);gl.vertexAttribPointer(1,3,gl.FLOAT,false,24,12);
function newTextureState(asset){const texture=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,texture);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.REPEAT);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.REPEAT);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR_MIPMAP_LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,1,1,0,gl.RGBA,gl.UNSIGNED_BYTE,new Uint8Array([255,255,255,255]));const state={asset,texture,ready:false};const img=new Image();img.decoding='async';img.onload=()=>{gl.bindTexture(gl.TEXTURE_2D,texture);gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,true);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,img);gl.generateMipmap(gl.TEXTURE_2D);state.ready=true};img.src=asset.media_url;return state}
const textureStates=new Map();function ensureTexture(asset){if(!asset||asset.kind!=='image')return null;if(!textureStates.has(asset.id))textureStates.set(asset.id,newTextureState(asset));return textureStates.get(asset.id)}for(const asset of media)if(asset.kind==='image')ensureTexture(asset);
function bindMap(asset,map){const state=ensureTexture(asset);gl.activeTexture(gl.TEXTURE0+map.unit);if(state?.ready){gl.bindTexture(gl.TEXTURE_2D,state.texture);gl.uniform1f(map.use,1);return true}gl.bindTexture(gl.TEXTURE_2D,null);gl.uniform1f(map.use,0);return false}
gl.enable(gl.DEPTH_TEST);gl.enable(gl.CULL_FACE);gl.cullFace(gl.BACK);gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);
function ident(){return new Float32Array([1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1])}function mul(a,b){const o=new Float32Array(16);for(let c=0;c<4;c++)for(let r=0;r<4;r++)o[c*4+r]=a[r]*b[c*4]+a[4+r]*b[c*4+1]+a[8+r]*b[c*4+2]+a[12+r]*b[c*4+3];return o}function perspective(fov,aspect,near,far){const f=1/Math.tan(fov/2),nf=1/(near-far),o=new Float32Array(16);o[0]=f/aspect;o[5]=f;o[10]=(far+near)*nf;o[11]=-1;o[14]=2*far*near*nf;return o}function lookAt(eye,target,up){let zx=eye[0]-target[0],zy=eye[1]-target[1],zz=eye[2]-target[2];let zl=Math.hypot(zx,zy,zz)||1;zx/=zl;zy/=zl;zz/=zl;let xx=up[1]*zz-up[2]*zy,xy=up[2]*zx-up[0]*zz,xz=up[0]*zy-up[1]*zx;let xl=Math.hypot(xx,xy,xz)||1;xx/=xl;xy/=xl;xz/=xl;const yx=zy*xz-zz*xy,yy=zz*xx-zx*xz,yz=zx*xy-zy*xx,o=ident();o[0]=xx;o[1]=yx;o[2]=zx;o[4]=xy;o[5]=yy;o[6]=zy;o[8]=xz;o[9]=yz;o[10]=zz;o[12]=-(xx*eye[0]+xy*eye[1]+xz*eye[2]);o[13]=-(yx*eye[0]+yy*eye[1]+yz*eye[2]);o[14]=-(zx*eye[0]+zy*eye[1]+zz*eye[2]);return o}function model(e){const p=e.position||{x:0,y:0,z:0},s=e.scale||{x:1,y:1,z:1},r=(e.rotation?.y||0)*Math.PI/180,c=Math.cos(r),sn=Math.sin(r),o=ident();o[0]=c*s.x;o[2]=-sn*s.x;o[5]=s.y;o[8]=sn*s.z;o[10]=c*s.z;o[12]=p.x;o[13]=p.y;o[14]=p.z;return o}function hex(v){const s=String(v||'#8ea6ff').replace('#',''),n=parseInt(s.length===3?s.split('').map(x=>x+x).join(''):s,16);return [((n>>16)&255)/255,((n>>8)&255)/255,(n&255)/255]}
const entities=cfg.entities.map(e=>JSON.parse(JSON.stringify(e)));let player=entities.find(e=>e.id==='player'||e.kind==='player');if(!player){player={id:'runtime-player',kind:'player',position:{x:0,y:1,z:0},rotation:{x:0,y:0,z:0},scale:{x:.6,y:1,z:.6},material:{base_color:'#69e4ff',roughness:.4,metallic:0,opacity:1}};entities.push(player)}
const sun=entities.find(e=>e.light?.kind==='directional'),lightDir=sun?[-.4,-1,-.35]:[-.4,-1,-.3],lightColor=sun?hex(sun.light.color):[1,.96,.85],ambient=hex(cfg.environment.ambient_color);
function fallbackBaseAsset(e){if(!heuristicImage)return null;const r=role(heuristicImage);if(/player|character|avatar|sprite/.test(r))return e.kind==='player'?heuristicImage:null;if(/terrain|ground|world|background|environment/.test(r))return e.kind==='terrain'?heuristicImage:null;return e.kind!=='player'?heuristicImage:null}
function materialAsset(e,slot){const b=entityBindings[e.id]||{};if(slot==='base_color')return b.textures?.base_color||b.visual||(e.kind==='terrain'?worldBindings.world_background:null)||fallbackBaseAsset(e);return b.textures?.[slot]||null}
function shaderMode(mat){const s=String(mat?.shader||'pbr');return s==='unlit'?1:s==='toon'?2:s==='emissive'?3:0}
const keys=new Set();addEventListener('keydown',e=>keys.add(e.key.toLowerCase()));addEventListener('keyup',e=>keys.delete(e.key.toLowerCase()));let yaw=.55,pitch=.35,distance=12,drag=false,lx=0,ly=0;canvas.addEventListener('pointerdown',e=>{drag=true;lx=e.clientX;ly=e.clientY;canvas.setPointerCapture(e.pointerId)});canvas.addEventListener('pointermove',e=>{if(!drag)return;yaw-=(e.clientX-lx)*.006;pitch=Math.max(-.15,Math.min(1.2,pitch-(e.clientY-ly)*.004));lx=e.clientX;ly=e.clientY});canvas.addEventListener('pointerup',()=>drag=false);canvas.addEventListener('wheel',e=>{e.preventDefault();distance=Math.max(4,Math.min(35,distance+e.deltaY*.012))},{passive:false});
function resize(){const d=Math.min(devicePixelRatio||1,2),w=Math.max(1,innerWidth),h=Math.max(1,innerHeight);if(canvas.width!==Math.floor(w*d)||canvas.height!==Math.floor(h*d)){canvas.width=Math.floor(w*d);canvas.height=Math.floor(h*d);gl.viewport(0,0,canvas.width,canvas.height)}return w/h}function cellVisible(e){if(e.kind==='terrain'||e.kind==='light'||e.kind==='camera')return true;const size=cfg.performance.streaming_cell_size||256,r=cfg.performance.streaming_radius_cells||2,pp=player.position,ep=e.position||{x:0,y:0,z:0};return Math.abs(Math.floor(ep.x/size)-Math.floor(pp.x/size))<=r&&Math.abs(Math.floor(ep.z/size)-Math.floor(pp.z/size))<=r}function entityScale(e){if(e.kind==='terrain')return{x:Math.max(1,cfg.terrain.width/2),y:.1,z:Math.max(1,cfg.terrain.depth/2)};if(e.kind==='player')return{x:.55,y:1,z:.55};if(e.kind==='light'||e.kind==='camera'||e.kind==='spawn'||e.kind==='audio'||e.kind==='ui_anchor')return null;return e.scale||{x:1,y:1,z:1}}
let last=performance.now();function frame(now){const dt=Math.min(.033,(now-last)/1000);last=now;let dx=0,dz=0;if(keys.has('a')||keys.has('arrowleft'))dx--;if(keys.has('d')||keys.has('arrowright'))dx++;if(keys.has('w')||keys.has('arrowup'))dz--;if(keys.has('s')||keys.has('arrowdown'))dz++;const len=Math.hypot(dx,dz)||1,speed=6;player.position.x+=dx/len*speed*dt;player.position.z-=dz/len*speed*dt;const pp=player.position,target=[pp.x,pp.y+.7,pp.z],eye=[pp.x+Math.sin(yaw)*Math.cos(pitch)*distance,pp.y+2+Math.sin(pitch)*distance,pp.z+Math.cos(yaw)*Math.cos(pitch)*distance],aspect=resize(),vp=mul(perspective(Math.PI/3,aspect,.08,5000),lookAt(eye,target,[0,1,0]));setListener(eye,target);for(const s of spatialSources)setAudioPosition(s.panner,s.entity.position||{});gl.clearColor(.025,.04,.075,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.useProgram(program);gl.uniformMatrix4fv(uViewProj,false,vp);gl.uniform3fv(uLightDir,lightDir);gl.uniform3fv(uLightColor,lightColor);gl.uniform3fv(uAmbient,ambient);gl.uniform3fv(uCameraPos,eye);gl.uniform1f(uExposure,Number(cfg.environment.exposure??1));let drawn=0,textured=0;for(const e of entities){if(!cellVisible(e))continue;const sc=entityScale(e);if(!sc)continue;const original=e.scale;e.scale=sc;const m=model(e);e.scale=original;const dist=Math.hypot((e.position?.x||0)-pp.x,(e.position?.z||0)-pp.z),lod=(cfg.performance.lod_distances||[30,80,180,400]);if(e.kind!=='terrain'&&dist>(lod[lod.length-1]||400)*2)continue;gl.uniformMatrix4fv(uModel,false,m);const mat=e.material||{base_color:e.kind==='hazard'?'#ff627f':e.kind==='collectible'?'#f6d36c':'#8ea6ff',metallic:0,roughness:.7,emissive_strength:0,opacity:1,shader:'pbr'};gl.uniform3fv(uBaseColor,hex(mat.base_color));gl.uniform1f(uMetallic,Number(mat.metallic??0));gl.uniform1f(uRoughness,Number(mat.roughness??.7));gl.uniform1f(uEmissiveStrength,Number(mat.emissive_strength??0));gl.uniform1f(uOpacity,Number(mat.opacity??1));gl.uniform1i(uShaderMode,shaderMode(mat));for(const map of MAPS)if(bindMap(materialAsset(e,map.slot),map))textured++;gl.drawArrays(gl.TRIANGLES,0,36);drawn++}statusEl.textContent=`Aura3D v2 · ${drawn} draw(s) · ${textured} PBR map binding(s) · ${cfg.stream_index.length} streamed cell(s) · ${spatialSources.length} spatial audio source(s) · ${navigator.gpu?'WebGPU available':'WebGL2 active'}`;requestAnimationFrame(frame)}requestAnimationFrame(frame);
</script></body></html>"""
    return template.replace("__CSP__", csp).replace("__PAYLOAD__", payload)


__all__ = ["render_aura3d_playtest", "_runtime_payload"]
