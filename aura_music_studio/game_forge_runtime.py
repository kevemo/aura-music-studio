from __future__ import annotations

import json

from .game_forge_integrity import game_integrity_hash
from .game_forge_models import GameBuild, GameDNA
from .game_forge_native3d import render_aura3d_playtest
from .game_forge_store import game_dir, save_game
from .game_forge_world import ensure_world, world_stream_index, world_summary


PLAYTEST_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "img-src data: blob:; media-src data: blob:; connect-src 'none'; font-src 'none'; "
    "object-src 'none'; frame-src 'none'; child-src 'none'; worker-src 'none'; "
    "base-uri 'none'; form-action 'none'"
)


def _safe_runtime_config(game: GameDNA) -> dict:
    world = ensure_world(game)
    return {
        "title": game.title,
        "genre": game.genre,
        "dimension": game.dimension,
        "engine_target": game.engine_target,
        "synopsis": game.synopsis[:1200],
        "mechanics": game.mechanics[:20],
        "art_direction": game.art_direction[:1000],
        "world": world_summary(world),
        "stream_cells": world_stream_index(world),
    }


def render_foundation_playtest(game: GameDNA) -> str:
    """Render Aura's deterministic no-network native runtime.

    Aura3D projects use Pulsar's own WebGL2 renderer v1. Aura2D and not-yet-implemented export
    adapters stay on the deterministic Canvas runtime. No creator/LLM JavaScript or Python is
    inserted or executed in either path.
    """
    world = ensure_world(game)
    if game.dimension == "3d" and game.engine_target == "aura3d":
        return render_aura3d_playtest(game, world, csp=PLAYTEST_CSP)

    config = json.dumps(_safe_runtime_config(game), ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<meta http-equiv='Content-Security-Policy' content=\"{PLAYTEST_CSP}\">
<title>Game Playtest</title><style>
*{{box-sizing:border-box}}html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#060713;color:white;font-family:system-ui,sans-serif}}canvas{{display:block;width:100%;height:100%;touch-action:none}}#hud{{position:fixed;inset:12px auto auto 12px;z-index:2;background:#050713cc;border:1px solid #ffffff28;border-radius:12px;padding:9px 11px;max-width:min(620px,86vw)}}#hud b{{display:block}}#hud small{{color:#bec5d5}}#help{{position:fixed;right:12px;bottom:12px;background:#050713cc;border:1px solid #ffffff28;border-radius:10px;padding:7px 9px;font-size:12px;color:#d9deea}}
</style></head><body><canvas id='game'></canvas><div id='hud'><b id='title'></b><small id='meta'></small><div id='score'>Score 0 · Lives 3</div></div><div id='help'>WASD / arrows · collect stars · avoid hazards</div><script>
'use strict';const cfg={config};const canvas=document.getElementById('game'),ctx=canvas.getContext('2d',{{alpha:false}});document.getElementById('title').textContent=cfg.title;document.getElementById('meta').textContent=`${{cfg.genre}} · ${{cfg.dimension.toUpperCase()}} · ${{cfg.engine_target}} · ${{cfg.world.streaming_cells}} streamed cell(s)`;
let W=0,H=0,score=0,lives=3,last=performance.now(),keys=new Set(),touchTarget=null;const p={{x:240,y:220,r:18,s:260}};const stars=[],haz=[];function resize(){{const d=Math.min(devicePixelRatio||1,2);W=innerWidth;H=innerHeight;canvas.width=Math.floor(W*d);canvas.height=Math.floor(H*d);canvas.style.width=W+'px';canvas.style.height=H+'px';ctx.setTransform(d,0,0,d,0,0)}}addEventListener('resize',resize);resize();
function rnd(a,b){{return a+Math.random()*(b-a)}}function spawn(a,n,type){{while(a.length<n)a.push({{x:rnd(40,Math.max(41,W-40)),y:rnd(70,Math.max(71,H-40)),r:type==='star'?10:rnd(12,22),vx:rnd(-65,65),vy:rnd(-65,65)}})}}spawn(stars,8,'star');spawn(haz,5,'haz');addEventListener('keydown',e=>keys.add(e.key.toLowerCase()));addEventListener('keyup',e=>keys.delete(e.key.toLowerCase()));canvas.addEventListener('pointerdown',e=>touchTarget={{x:e.clientX,y:e.clientY}});canvas.addEventListener('pointermove',e=>{{if(e.buttons)touchTarget={{x:e.clientX,y:e.clientY}}}});canvas.addEventListener('pointerup',()=>touchTarget=null);
function hit(a,b){{return Math.hypot(a.x-b.x,a.y-b.y)<a.r+b.r}}function resetObj(o){{o.x=rnd(40,Math.max(41,W-40));o.y=rnd(70,Math.max(71,H-40))}}function update(dt){{let dx=0,dy=0;if(keys.has('a')||keys.has('arrowleft'))dx--;if(keys.has('d')||keys.has('arrowright'))dx++;if(keys.has('w')||keys.has('arrowup'))dy--;if(keys.has('s')||keys.has('arrowdown'))dy++;if(touchTarget){{dx=touchTarget.x-p.x;dy=touchTarget.y-p.y}}const m=Math.hypot(dx,dy)||1;p.x=Math.max(p.r,Math.min(W-p.r,p.x+dx/m*p.s*dt));p.y=Math.max(60+p.r,Math.min(H-p.r,p.y+dy/m*p.s*dt));for(const o of haz){{o.x+=o.vx*dt;o.y+=o.vy*dt;if(o.x<o.r||o.x>W-o.r)o.vx*=-1;if(o.y<60+o.r||o.y>H-o.r)o.vy*=-1;if(hit(p,o)){{lives--;resetObj(o);p.x=W/2;p.y=H/2;if(lives<=0){{score=0;lives=3}}}}}}for(const s of stars)if(hit(p,s)){{score++;resetObj(s)}}document.getElementById('score').textContent=`Score ${{score}} · Lives ${{lives}}`}}
function draw(){{const g=ctx.createLinearGradient(0,0,W,H);g.addColorStop(0,'#0b1835');g.addColorStop(.5,'#241044');g.addColorStop(1,'#07101d');ctx.fillStyle=g;ctx.fillRect(0,0,W,H);ctx.strokeStyle='#ffffff0e';for(let x=0;x<W;x+=48){{ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,H);ctx.stroke()}}for(let y=60;y<H;y+=48){{ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(W,y);ctx.stroke()}}ctx.fillStyle='#f6d36c';for(const s of stars){{ctx.beginPath();ctx.arc(s.x,s.y,s.r,0,Math.PI*2);ctx.fill()}}ctx.fillStyle='#ff627f';for(const o of haz){{ctx.beginPath();ctx.arc(o.x,o.y,o.r,0,Math.PI*2);ctx.fill()}}ctx.fillStyle='#69e4ff';ctx.beginPath();ctx.arc(p.x,p.y,p.r,0,Math.PI*2);ctx.fill();ctx.strokeStyle='white';ctx.lineWidth=2;ctx.stroke()}}
function frame(now){{const dt=Math.min(.033,(now-last)/1000);last=now;update(dt);draw();requestAnimationFrame(frame)}}requestAnimationFrame(frame);
</script></body></html>"""


def build_private_playtest(game: GameDNA) -> tuple[GameDNA, str]:
    ensure_world(game)
    content_hash = game_integrity_hash(game)
    html = render_foundation_playtest(game)
    runtime_name = "aura_game_runtime_3d_webgl2_v1" if game.dimension == "3d" and game.engine_target == "aura3d" else "aura_game_runtime_2d_canvas_v1"
    build = GameBuild(content_hash=content_hash, requested_engine=game.engine_target, runtime=runtime_name)
    folder = game_dir(game.id)
    build_dir = folder / "builds" / build.build_id
    build_dir.mkdir(parents=True, exist_ok=False)
    (build_dir / "play.html").write_text(html, encoding="utf-8")
    game.latest_build = build
    game.status = "review_ready"
    game.updated_at = build.created_at
    save_game(game)
    return game, html


def private_play_html(game: GameDNA) -> str:
    if not game.latest_build:
        raise FileNotFoundError("Game has no playtest build")
    path = game_dir(game.id) / "builds" / game.latest_build.build_id / "play.html"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")
