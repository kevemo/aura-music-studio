from __future__ import annotations

import json

from .game_forge_adventure import adventure_runtime_payload
from .game_forge_gameplay_runtime import PLAYTEST_CSP, private_play_html, render_gameplay_playtest
from .game_forge_integrity import game_integrity_hash
from .game_forge_models import GameBuild, GameDNA
from .game_forge_store import game_dir, save_game


def _json_for_script(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _inject_before_body(html: str, fragment: str) -> str:
    marker = "</body></html>"
    if marker not in html:
        raise ValueError("Aura gameplay runtime document boundary changed; Adventure State injection requires review")
    return html.replace(marker, fragment + marker, 1)


_ADVENTURE_RUNTIME = r"""
'use strict';
const auraAdventure=JSON.parse(document.getElementById('aura-adventure-dna').textContent||'{}');
const advItems=new Map((auraAdventure.items||[]).map(x=>[x.id,x]));
const advObjectives=new Map((auraAdventure.objectives||[]).map(x=>[x.id,x]));
const advDialogues=new Map((auraAdventure.dialogues||[]).map(x=>[x.id,x]));
const advGates=new Map((auraAdventure.gates||[]).map(x=>[x.id,x]));
const advAllowedFlags=new Set(Object.keys(auraAdventure.initial_flags||{}));
for(const o of advObjectives.values()){if(o.flag)advAllowedFlags.add(o.flag);if(o.completion_flag)advAllowedFlags.add(o.completion_flag)}
for(const d of advDialogues.values()){if(d.completion_flag)advAllowedFlags.add(d.completion_flag);for(const c of d.choices||[])if(c.set_flag)advAllowedFlags.add(c.set_flag)}
for(const g of advGates.values()){if(g.requires_flag)advAllowedFlags.add(g.requires_flag);if(g.open_flag)advAllowedFlags.add(g.open_flag)}
const advDefaultState=()=>({flags:{...(auraAdventure.initial_flags||{})},inventory:{},objectives:{},dialogues:{},gates:{},checkpoint:null,play_seconds:0});
let adv=advDefaultState();
function advFinite(v,d=0){const n=Number(v);return Number.isFinite(n)?n:d}
function advClampInt(v,lo,hi){return Math.max(lo,Math.min(hi,Math.round(advFinite(v,lo))))}
function advSanitize(raw){const next=advDefaultState();if(!raw||typeof raw!=='object')return next;for(const flag of advAllowedFlags)if(typeof raw.flags?.[flag]==='boolean')next.flags[flag]=raw.flags[flag];for(const [id,item] of advItems){const count=advClampInt(raw.inventory?.[id],0,Number(item.max_stack||99));if(count)next.inventory[id]=count}for(const id of advObjectives.keys()){const row=raw.objectives?.[id];if(row&&typeof row==='object')next.objectives[id]={count:advClampInt(row.count,0,1000000),complete:row.complete===true}}for(const id of advDialogues.keys())if(raw.dialogues?.[id]===true)next.dialogues[id]=true;for(const id of advGates.keys())if(raw.gates?.[id]===true)next.gates[id]=true;const cp=raw.checkpoint;if(cp&&[cp.x,cp.y,cp.z].every(Number.isFinite))next.checkpoint={x:advFinite(cp.x),y:advFinite(cp.y),z:advFinite(cp.z)};next.play_seconds=Math.max(0,Math.min(864000,advFinite(raw.play_seconds,0)));return next}
try{const saved=localStorage.getItem(String(auraAdventure.save_key||''));if(saved)adv=advSanitize(JSON.parse(saved))}catch(_e){}
function advSave(){try{localStorage.setItem(String(auraAdventure.save_key||''),JSON.stringify(adv))}catch(_e){}}
const advHud=document.createElement('section');advHud.id='aura-adventure-hud';advHud.setAttribute('aria-label','Adventure objectives and inventory');advHud.style.cssText='position:fixed;right:12px;top:12px;z-index:8;width:min(320px,calc(100vw - 24px));max-height:42vh;overflow:auto;background:#060914e8;border:1px solid #ffffff26;border-radius:12px;padding:10px;color:#fff;font:600 12px/1.45 system-ui,sans-serif;box-shadow:0 12px 40px #0008';document.body.appendChild(advHud);
const advStatus=document.createElement('div');advStatus.setAttribute('role','status');advStatus.setAttribute('aria-live','polite');advStatus.style.cssText='margin-bottom:8px;color:#bde8ff';advHud.appendChild(advStatus);
const advBody=document.createElement('div');advHud.appendChild(advBody);
const advReset=document.createElement('button');advReset.type='button';advReset.textContent='Reset local game save';advReset.style.cssText='margin-top:8px;min-height:44px;border-radius:8px;border:1px solid #ffffff36;background:#ffffff12;color:#fff;padding:6px 9px;cursor:pointer';advReset.addEventListener('click',()=>{try{localStorage.removeItem(String(auraAdventure.save_key||''))}catch(_e){}location.reload()});advHud.appendChild(advReset);
const advModal=document.createElement('div');advModal.id='aura-dialogue';advModal.setAttribute('role','dialog');advModal.setAttribute('aria-modal','true');advModal.setAttribute('aria-label','Game dialogue');advModal.style.cssText='display:none;position:fixed;inset:0;z-index:20;background:#000b;align-items:center;justify-content:center;padding:18px';document.body.appendChild(advModal);
function advMessage(text){const value=String(text||'').slice(0,220);advStatus.textContent=value;try{if(typeof auraSay==='function')auraSay(value,3)}catch(_e){}}
function advInventoryAdd(id,qty){const item=advItems.get(id);if(!item)return;const max=Math.max(1,Number(item.max_stack||99));adv.inventory[id]=Math.max(0,Math.min(max,Number(adv.inventory[id]||0)+Math.max(1,Number(qty||1))));advSave();advRender()}
function advSetFlag(flag,value=true){if(!flag||!advAllowedFlags.has(flag))return;adv.flags[flag]=value===true;advSave();advEvaluate()}
function advCompleteObjective(row){const s=adv.objectives[row.id]||(adv.objectives[row.id]={count:0,complete:false});if(s.complete)return;s.complete=true;if(row.completion_flag)adv.flags[row.completion_flag]=true;if(row.reward_item_id)advInventoryAdd(row.reward_item_id,row.reward_quantity||1);advMessage(`Objective complete: ${row.title}`);advSave();advRender()}
function advObjectiveEvent(kind,entityId){for(const row of advObjectives.values()){const s=adv.objectives[row.id]||(adv.objectives[row.id]={count:0,complete:false});if(s.complete||row.kind!==kind)continue;if(row.target_entity_id&&row.target_entity_id!==entityId)continue;s.count=Math.min(Number(row.target_count||1),Number(s.count||0)+1);if(s.count>=Number(row.target_count||1))advCompleteObjective(row)}advSave();advRender()}
function advEvaluate(){for(const row of advObjectives.values()){const s=adv.objectives[row.id]||(adv.objectives[row.id]={count:0,complete:false});if(s.complete)continue;if(row.kind==='flag'&&row.flag&&adv.flags[row.flag]===true)advCompleteObjective(row);if(row.kind==='timer'&&adv.play_seconds>=Number(row.seconds||0))advCompleteObjective(row)}advRender()}
function advRender(){while(advBody.firstChild)advBody.removeChild(advBody.firstChild);const h=document.createElement('div');h.textContent='Objectives';h.style.fontWeight='800';advBody.appendChild(h);let shown=0;for(const row of advObjectives.values()){shown++;const s=adv.objectives[row.id]||{count:0,complete:false};const line=document.createElement('div');line.style.cssText='padding:4px 0;border-bottom:1px solid #ffffff16';const progress=row.kind==='collect'&&!s.complete?` ${Math.min(Number(s.count||0),Number(row.target_count||1))}/${Number(row.target_count||1)}`:'';line.textContent=`${s.complete?'✓':'○'} ${row.title}${progress}`;advBody.appendChild(line)}if(!shown){const empty=document.createElement('div');empty.textContent='No objectives authored yet.';empty.style.opacity='.72';advBody.appendChild(empty)}const ih=document.createElement('div');ih.textContent='Inventory';ih.style.cssText='font-weight:800;margin-top:8px';advBody.appendChild(ih);let items=0;for(const [id,count] of Object.entries(adv.inventory)){if(Number(count)<=0)continue;const item=advItems.get(id);if(!item)continue;items++;const line=document.createElement('div');line.textContent=`${item.name} × ${count}`;advBody.appendChild(line)}if(!items){const empty=document.createElement('div');empty.textContent='Empty';empty.style.opacity='.72';advBody.appendChild(empty)}}
function advFinishDialogue(dialogue,choice){if(choice?.set_flag)advSetFlag(choice.set_flag,true);if(choice?.grant_item_id)advInventoryAdd(choice.grant_item_id,choice.grant_quantity||1);if(dialogue.completion_flag)advSetFlag(dialogue.completion_flag,true);if(dialogue.once!==false||choice?.complete_dialogue!==false)adv.dialogues[dialogue.id]=true;advModal.style.display='none';advObjectiveEvent('talk',dialogue.trigger_entity_id);advSave();advRender()}
function advOpenDialogue(dialogue){if(!dialogue||adv.dialogues[dialogue.id]===true&&dialogue.once!==false)return;while(advModal.firstChild)advModal.removeChild(advModal.firstChild);const box=document.createElement('div');box.style.cssText='width:min(620px,100%);max-height:80vh;overflow:auto;background:#090d1c;border:1px solid #ffffff33;border-radius:16px;padding:18px;color:#fff;font:500 15px/1.55 system-ui,sans-serif';const speaker=document.createElement('h2');speaker.textContent=dialogue.speaker||'Character';speaker.style.margin='0 0 10px';box.appendChild(speaker);for(const text of dialogue.lines||[]){const p=document.createElement('p');p.textContent=String(text||'').slice(0,400);box.appendChild(p)}const choices=dialogue.choices||[];if(choices.length){for(const choice of choices){const b=document.createElement('button');b.type='button';b.textContent=choice.label;b.style.cssText='display:block;width:100%;min-height:44px;text-align:left;margin:7px 0;border:1px solid #ffffff2e;border-radius:9px;background:#ffffff10;color:#fff;padding:8px 10px;cursor:pointer';b.addEventListener('click',()=>advFinishDialogue(dialogue,choice));box.appendChild(b)}}else{const b=document.createElement('button');b.type='button';b.textContent='Continue';b.style.cssText='min-height:44px;border:1px solid #ffffff2e;border-radius:9px;background:#ffffff10;color:#fff;padding:8px 12px;cursor:pointer';b.addEventListener('click',()=>advFinishDialogue(dialogue,null));box.appendChild(b)}advModal.appendChild(box);advModal.style.display='flex';const first=box.querySelector('button');if(first)first.focus()}
function advOpenGate(gate){if(!gate||adv.gates[gate.id]===true)return;if(gate.requires_flag&&adv.flags[gate.requires_flag]!==true){advMessage(`${gate.label}: requirement not met`);return}if(gate.requires_item_id&&Number(adv.inventory[gate.requires_item_id]||0)<1){const item=advItems.get(gate.requires_item_id);advMessage(`${gate.label}: requires ${item?.name||'item'}`);return}if(gate.requires_item_id&&gate.consume_item===true)adv.inventory[gate.requires_item_id]=Math.max(0,Number(adv.inventory[gate.requires_item_id]||0)-1);if(gate.open_flag)adv.flags[gate.open_flag]=true;adv.gates[gate.id]=true;try{const state=auraState.get(gate.door_entity_id||gate.trigger_entity_id);if(state)state.taken=true;if(typeof auraEntity==='function'){const entity=auraEntity(gate.door_entity_id||gate.trigger_entity_id);if(entity){if(typeof auraHide==='function'&&state)auraHide(entity,state);else entity.scale={x:.000001,y:.000001,z:.000001}}}}catch(_e){}advMessage(`${gate.label} opened`);advSave();advEvaluate()}
const advPrevious=new Map();function advApplyRestoredGates(){for(const gate of advGates.values())if(adv.gates[gate.id]===true){try{const state=auraState.get(gate.door_entity_id||gate.trigger_entity_id);if(state)state.taken=true;if(typeof auraEntity==='function'){const entity=auraEntity(gate.door_entity_id||gate.trigger_entity_id);if(entity)entity.scale={x:.000001,y:.000001,z:.000001}}}catch(_e){}}}
function advRestoreCheckpoint(){if(!adv.checkpoint)return;try{auraCheckpoint={...adv.checkpoint};if(typeof auraResetPlayer==='function')auraResetPlayer()}catch(_e){}}
let advLast=performance.now(),advSaveClock=0;function advFrame(nowMs){const dt=Math.max(0,Math.min(.1,(nowMs-advLast)/1000));advLast=nowMs;adv.play_seconds=Math.min(864000,adv.play_seconds+dt);try{for(const row of auraRows||[]){const s=auraState.get(row.id);if(!s)continue;const p=advPrevious.get(row.id)||{taken:false,checkpoint:false,triggered:false};if(s.taken===true&&p.taken!==true)advObjectiveEvent('collect',row.id);if(s.checkpoint===true&&p.checkpoint!==true){advObjectiveEvent('reach',row.id);try{adv.checkpoint={x:advFinite(auraCheckpoint.x),y:advFinite(auraCheckpoint.y),z:advFinite(auraCheckpoint.z)}}catch(_e){}advSave()}if(s.triggered===true&&p.triggered!==true){advObjectiveEvent('reach',row.id);for(const d of advDialogues.values())if(d.trigger_entity_id===row.id)advOpenDialogue(d);for(const g of advGates.values())if(g.trigger_entity_id===row.id)advOpenGate(g)}advPrevious.set(row.id,{taken:s.taken===true,checkpoint:s.checkpoint===true,triggered:s.triggered===true})}}catch(_e){}advEvaluate();advSaveClock+=dt;if(advSaveClock>=5){advSaveClock=0;advSave()}requestAnimationFrame(advFrame)}
advRender();advApplyRestoredGates();advRestoreCheckpoint();requestAnimationFrame(advFrame);
"""


def render_adventure_playtest(game: GameDNA) -> str:
    html = render_gameplay_playtest(game)
    payload = adventure_runtime_payload(game)
    payload["save_key"] = f"aura-game-state:{game.id}:{game_integrity_hash(game)[:20]}"
    serialized = _json_for_script(payload)
    fragment = (
        "<script id='aura-adventure-dna' type='application/json'>"
        + serialized
        + "</script><script>\n"
        + _ADVENTURE_RUNTIME
        + "\n</script>"
    )
    return _inject_before_body(html, fragment)


def build_adventure_playtest(game: GameDNA) -> tuple[GameDNA, str]:
    content_hash = game_integrity_hash(game)
    html = render_adventure_playtest(game)
    runtime_name = (
        "aura_game_runtime_3d_webgl2_v6_adventure"
        if game.dimension == "3d" and game.engine_target == "aura3d"
        else "aura_game_runtime_2d_canvas_v3_adventure"
    )
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


__all__ = [
    "PLAYTEST_CSP",
    "render_adventure_playtest",
    "build_adventure_playtest",
    "private_play_html",
]
