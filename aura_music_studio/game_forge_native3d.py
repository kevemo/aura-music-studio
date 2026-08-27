from __future__ import annotations

import json

from .game_forge_models import GameDNA
from .game_forge_world import GameWorldDNA, world_stream_index


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
                "material": row.material.model_dump(mode="json") if row.material else None,
                "light": row.light.model_dump(mode="json") if row.light else None,
                "physics": row.physics.model_dump(mode="json") if row.physics else None,
                "behaviors": [x.model_dump(mode="json") for x in row.behaviors],
                "lod_group": row.lod_group,
            }
        )
    return {
        "title": game.title,
        "genre": game.genre,
        "engine": "aura3d",
        "world_id": world.world_id,
        "world_revision": world.revision,
        "terrain": world.terrain.model_dump(mode="json"),
        "environment": world.environment.model_dump(mode="json"),
        "performance": world.performance.model_dump(mode="json"),
        "stream_index": world_stream_index(world),
        "entities": entities,
        "runtime_contract": {
            "native_aura_renderer": True,
            "generated_game_code_executed": False,
            "network_access": False,
            "external_engine": None,
        },
    }


def render_aura3d_playtest(game: GameDNA, world: GameWorldDNA, *, csp: str) -> str:
    """Render a native Aura WebGL2 world from validated World DNA.

    This renderer executes only reviewed Pulsar runtime code. World/game data is serialized into
    a closed scene format; no creator/LLM JavaScript is inserted or executed. v1 intentionally
    starts with primitives, transforms, streamed cells, camera movement, depth testing, culling
    and simple material/light response. Higher-end WebGPU/PBR/terrain/animation stages layer on
    this same World DNA rather than replacing the project model.
    """
    payload = json.dumps(_runtime_payload(game, world), ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>
<meta http-equiv='Content-Security-Policy' content=\"{csp}\">
<title>Aura Game Engine 3D</title><style>
*{{box-sizing:border-box}}html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#050711;color:#fff;font-family:Inter,system-ui,sans-serif}}canvas{{display:block;width:100%;height:100%;touch-action:none}}#hud{{position:fixed;left:12px;top:12px;z-index:3;background:#060914d9;border:1px solid #ffffff26;border-radius:12px;padding:9px 11px;max-width:min(640px,88vw);backdrop-filter:blur(8px)}}#hud b{{display:block}}#hud small{{display:block;color:#bec6d8;line-height:1.35}}#status{{position:fixed;right:12px;bottom:12px;z-index:3;background:#060914d9;border:1px solid #ffffff26;border-radius:10px;padding:7px 9px;color:#dce4f4;font-size:12px}}#fallback{{display:none;position:fixed;inset:0;padding:32px;place-items:center;text-align:center;background:radial-gradient(circle at 50% 30%,#241446,#050711 60%)}}#fallback div{{max-width:680px}}
</style></head><body><canvas id='game'></canvas><div id='hud'><b id='title'></b><small id='meta'></small><small>WASD / arrows move · drag to orbit camera · mouse wheel zoom</small></div><div id='status'>Starting Aura3D…</div><div id='fallback'><div><h2>Aura3D needs WebGL2 on this browser.</h2><p>This device can still use compatible 2D/fallback playtests. WebGPU acceleration is an enhancement path, not a requirement for the v1 renderer.</p></div></div><script>
'use strict';
const cfg={payload};
const canvas=document.getElementById('game'),statusEl=document.getElementById('status');
document.getElementById('title').textContent=cfg.title;
document.getElementById('meta').textContent=`${{cfg.genre}} · Aura Game Engine 3D · World r${{cfg.world_revision}}`;
const gl=canvas.getContext('webgl2',{{alpha:false,antialias:true,powerPreference:'high-performance'}});
if(!gl){{document.getElementById('fallback').style.display='grid';canvas.style.display='none';statusEl.textContent='WebGL2 unavailable';throw new Error('WebGL2 unavailable')}}
const vs=`#version 300 es
precision highp float;
layout(location=0) in vec3 aPos;
layout(location=1) in vec3 aNormal;
uniform mat4 uModel;
uniform mat4 uViewProj;
out vec3 vNormal;
out vec3 vWorld;
void main(){{vec4 world=uModel*vec4(aPos,1.0);vWorld=world.xyz;vNormal=normalize(mat3(uModel)*aNormal);gl_Position=uViewProj*world;}}`;
const fs=`#version 300 es
precision highp float;
in vec3 vNormal;
in vec3 vWorld;
uniform vec3 uBaseColor;
uniform vec3 uLightDir;
uniform vec3 uLightColor;
uniform vec3 uAmbient;
uniform float uRoughness;
out vec4 outColor;
void main(){{vec3 n=normalize(vNormal);float ndl=max(dot(n,normalize(-uLightDir)),0.0);float hemi=.5+.5*n.y;vec3 diffuse=uBaseColor*(uAmbient*(.55+.45*hemi)+uLightColor*ndl);float rim=pow(1.0-max(dot(n,normalize(vec3(.2,.7,1.0))),0.0),3.0)*(1.0-uRoughness)*.18;outColor=vec4(diffuse+rim,1.0);}}`;
function shader(type,src){{const s=gl.createShader(type);gl.shaderSource(s,src);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(s)||'shader compile failed');return s}}
const program=gl.createProgram();gl.attachShader(program,shader(gl.VERTEX_SHADER,vs));gl.attachShader(program,shader(gl.FRAGMENT_SHADER,fs));gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(program)||'program link failed');gl.useProgram(program);
const uModel=gl.getUniformLocation(program,'uModel'),uViewProj=gl.getUniformLocation(program,'uViewProj'),uBaseColor=gl.getUniformLocation(program,'uBaseColor'),uLightDir=gl.getUniformLocation(program,'uLightDir'),uLightColor=gl.getUniformLocation(program,'uLightColor'),uAmbient=gl.getUniformLocation(program,'uAmbient'),uRoughness=gl.getUniformLocation(program,'uRoughness');
const cube=new Float32Array([
-1,-1,1,0,0,1, 1,-1,1,0,0,1, 1,1,1,0,0,1, -1,-1,1,0,0,1, 1,1,1,0,0,1, -1,1,1,0,0,1,
1,-1,-1,0,0,-1, -1,-1,-1,0,0,-1, -1,1,-1,0,0,-1, 1,-1,-1,0,0,-1, -1,1,-1,0,0,-1, 1,1,-1,0,0,-1,
-1,1,1,0,1,0, 1,1,1,0,1,0, 1,1,-1,0,1,0, -1,1,1,0,1,0, 1,1,-1,0,1,0, -1,1,-1,0,1,0,
-1,-1,-1,0,-1,0, 1,-1,-1,0,-1,0, 1,-1,1,0,-1,0, -1,-1,-1,0,-1,0, 1,-1,1,0,-1,0, -1,-1,1,0,-1,0,
1,-1,1,1,0,0, 1,-1,-1,1,0,0, 1,1,-1,1,0,0, 1,-1,1,1,0,0, 1,1,-1,1,0,0, 1,1,1,1,0,0,
-1,-1,-1,-1,0,0, -1,-1,1,-1,0,0, -1,1,1,-1,0,0, -1,-1,-1,-1,0,0, -1,1,1,-1,0,0, -1,1,-1,-1,0,0]);
const vao=gl.createVertexArray();gl.bindVertexArray(vao);const vbo=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,vbo);gl.bufferData(gl.ARRAY_BUFFER,cube,gl.STATIC_DRAW);gl.enableVertexAttribArray(0);gl.vertexAttribPointer(0,3,gl.FLOAT,false,24,0);gl.enableVertexAttribArray(1);gl.vertexAttribPointer(1,3,gl.FLOAT,false,24,12);
gl.enable(gl.DEPTH_TEST);gl.enable(gl.CULL_FACE);gl.cullFace(gl.BACK);
function ident(){{return new Float32Array([1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1])}}
function mul(a,b){{const o=new Float32Array(16);for(let c=0;c<4;c++)for(let r=0;r<4;r++)o[c*4+r]=a[r]*b[c*4]+a[4+r]*b[c*4+1]+a[8+r]*b[c*4+2]+a[12+r]*b[c*4+3];return o}}
function perspective(fov,aspect,near,far){{const f=1/Math.tan(fov/2),nf=1/(near-far),o=new Float32Array(16);o[0]=f/aspect;o[5]=f;o[10]=(far+near)*nf;o[11]=-1;o[14]=2*far*near*nf;return o}}
function lookAt(eye,target,up){{let zx=eye[0]-target[0],zy=eye[1]-target[1],zz=eye[2]-target[2];let zl=Math.hypot(zx,zy,zz)||1;zx/=zl;zy/=zl;zz/=zl;let xx=up[1]*zz-up[2]*zy,xy=up[2]*zx-up[0]*zz,xz=up[0]*zy-up[1]*zx;let xl=Math.hypot(xx,xy,xz)||1;xx/=xl;xy/=xl;xz/=xl;const yx=zy*xz-zz*xy,yy=zz*xx-zx*xz,yz=zx*xy-zy*xx;const o=ident();o[0]=xx;o[1]=yx;o[2]=zx;o[4]=xy;o[5]=yy;o[6]=zy;o[8]=xz;o[9]=yz;o[10]=zz;o[12]=-(xx*eye[0]+xy*eye[1]+xz*eye[2]);o[13]=-(yx*eye[0]+yy*eye[1]+yz*eye[2]);o[14]=-(zx*eye[0]+zy*eye[1]+zz*eye[2]);return o}}
function model(e){{const p=e.position||{{x:0,y:0,z:0}},s=e.scale||{{x:1,y:1,z:1}},r=(e.rotation?.y||0)*Math.PI/180,c=Math.cos(r),sn=Math.sin(r);const o=ident();o[0]=c*s.x;o[2]=-sn*s.x;o[5]=s.y;o[8]=sn*s.z;o[10]=c*s.z;o[12]=p.x;o[13]=p.y;o[14]=p.z;return o}}
function hex(v){{const s=String(v||'#8ea6ff').replace('#','');const n=parseInt(s.length===3?s.split('').map(x=>x+x).join(''):s,16);return [((n>>16)&255)/255,((n>>8)&255)/255,(n&255)/255]}}
const entities=cfg.entities.map(e=>JSON.parse(JSON.stringify(e)));let player=entities.find(e=>e.id==='player'||e.kind==='player');if(!player){{player={{id:'runtime-player',kind:'player',position:{{x:0,y:1,z:0}},rotation:{{x:0,y:0,z:0}},scale:{{x:.6,y:1,z:.6}},material:{{base_color:'#69e4ff',roughness:.4}}}};entities.push(player)}}
const sun=entities.find(e=>e.light?.kind==='directional');const lightDir=sun?[-.4,-1,-.35]:[-.4,-1,-.3],lightColor=sun?hex(sun.light.color):[1,.96,.85],ambient=hex(cfg.environment.ambient_color);
const keys=new Set();addEventListener('keydown',e=>keys.add(e.key.toLowerCase()));addEventListener('keyup',e=>keys.delete(e.key.toLowerCase()));let yaw=.55,pitch=.35,distance=12,drag=false,lx=0,ly=0;canvas.addEventListener('pointerdown',e=>{{drag=true;lx=e.clientX;ly=e.clientY;canvas.setPointerCapture(e.pointerId)}});canvas.addEventListener('pointermove',e=>{{if(!drag)return;yaw-=(e.clientX-lx)*.006;pitch=Math.max(-.15,Math.min(1.2,pitch-(e.clientY-ly)*.004));lx=e.clientX;ly=e.clientY}});canvas.addEventListener('pointerup',()=>drag=false);canvas.addEventListener('wheel',e=>{{e.preventDefault();distance=Math.max(4,Math.min(35,distance+e.deltaY*.012))}},{{passive:false}});
function resize(){{const d=Math.min(devicePixelRatio||1,2),w=Math.max(1,innerWidth),h=Math.max(1,innerHeight);if(canvas.width!==Math.floor(w*d)||canvas.height!==Math.floor(h*d)){{canvas.width=Math.floor(w*d);canvas.height=Math.floor(h*d);gl.viewport(0,0,canvas.width,canvas.height)}}return w/h}}
function cellVisible(e){{if(e.kind==='terrain'||e.kind==='light'||e.kind==='camera')return true;const size=cfg.performance.streaming_cell_size||256,r=cfg.performance.streaming_radius_cells||2,pp=player.position,ep=e.position||{{x:0,y:0,z:0}};return Math.abs(Math.floor(ep.x/size)-Math.floor(pp.x/size))<=r&&Math.abs(Math.floor(ep.z/size)-Math.floor(pp.z/size))<=r}}
function entityScale(e){{if(e.kind==='terrain')return {{x:Math.max(1,cfg.terrain.width/2),y:.1,z:Math.max(1,cfg.terrain.depth/2)}};if(e.kind==='player')return {{x:.55,y:1.0,z:.55}};if(e.kind==='light'||e.kind==='camera'||e.kind==='spawn'||e.kind==='audio'||e.kind==='ui_anchor')return null;return e.scale||{{x:1,y:1,z:1}}}}
let last=performance.now();function frame(now){{const dt=Math.min(.033,(now-last)/1000);last=now;let dx=0,dz=0;if(keys.has('a')||keys.has('arrowleft'))dx--;if(keys.has('d')||keys.has('arrowright'))dx++;if(keys.has('w')||keys.has('arrowup'))dz--;if(keys.has('s')||keys.has('arrowdown'))dz++;const len=Math.hypot(dx,dz)||1,speed=6;player.position.x+=dx/len*speed*dt;player.position.z-=dz/len*speed*dt;const pp=player.position,target=[pp.x,pp.y+.7,pp.z],eye=[pp.x+Math.sin(yaw)*Math.cos(pitch)*distance,pp.y+2+Math.sin(pitch)*distance,pp.z+Math.cos(yaw)*Math.cos(pitch)*distance],aspect=resize();const vp=mul(perspective(Math.PI/3,aspect,.08,5000),lookAt(eye,target,[0,1,0]));gl.clearColor(.025,.04,.075,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.useProgram(program);gl.uniformMatrix4fv(uViewProj,false,vp);gl.uniform3fv(uLightDir,lightDir);gl.uniform3fv(uLightColor,lightColor);gl.uniform3fv(uAmbient,ambient);let drawn=0;for(const e of entities){{if(!cellVisible(e))continue;const sc=entityScale(e);if(!sc)continue;const original=e.scale;e.scale=sc;const m=model(e);e.scale=original;const dist=Math.hypot((e.position?.x||0)-pp.x,(e.position?.z||0)-pp.z);const lod=(cfg.performance.lod_distances||[30,80,180,400]);if(e.kind!=='terrain'&&dist>(lod[lod.length-1]||400)*2)continue;gl.uniformMatrix4fv(uModel,false,m);const mat=e.material||{{base_color:e.kind==='hazard'?'#ff627f':e.kind==='collectible'?'#f6d36c':'#8ea6ff',roughness:.7}};gl.uniform3fv(uBaseColor,hex(mat.base_color));gl.uniform1f(uRoughness,Number(mat.roughness??.7));gl.drawArrays(gl.TRIANGLES,0,36);drawn++}}statusEl.textContent=`Aura3D WebGL2 · ${{drawn}} draw(s) · ${{cfg.world_revision}} world rev · ${{navigator.gpu?'WebGPU available':'WebGPU fallback mode'}}`;requestAnimationFrame(frame)}}requestAnimationFrame(frame);
</script></body></html>"""
