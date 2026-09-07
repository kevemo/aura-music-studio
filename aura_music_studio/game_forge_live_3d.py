from __future__ import annotations

import json

from .game_forge_models import GameDNA

_MARKER = "id='aura-live-3d-tuning'"
_BODY_CLOSE = "</body></html>"
_MAX_TUNABLE_ENTITIES = 32


def inject_live_3d_tuning(html: str, *, game: GameDNA) -> str:
    """Add private-playtest Aura3D tuning for already-authored gameplay entities only.

    This layer never creates entities or mutates persistent World/Game DNA. It can temporarily
    suppress authored collectible/damage entities and restore only entities that this live session
    itself suppressed. A legitimately collected/taken entity is never resurrected by "more".
    """
    if game.dimension != "3d" or game.engine_target != "aura3d":
        return html
    if _MARKER in html:
        return html
    if _BODY_CLOSE not in html:
        raise ValueError("Aura3D live tuning document boundary changed; injection requires review")

    config = json.dumps(
        {
            "version": 1,
            "game_id": str(game.id),
            "max_tunable_entities": _MAX_TUNABLE_ENTITIES,
            "player_speed_min": 1.5,
            "player_speed_max": 14.0,
            "player_speed_default": 6.0,
            "project_persistence": False,
            "arbitrary_entity_creation": False,
            "authored_entities_only": True,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).replace("</", "<\\/")

    fragment = r"""
<script id='aura-live-3d-tuning' type='application/json'>__CONFIG__</script>
<script id='aura-live-3d-tuning-script'>
'use strict';
(()=>{
const cfg3=JSON.parse(document.getElementById('aura-live-3d-tuning').textContent||'{}');
const expected3=`/api/game-forge/games/${encodeURIComponent(String(cfg3.game_id||''))}/playtest-frame`;
if(location.pathname!==expected3)return;
const panel3=document.getElementById('aura-live-copilot');
const row3=document.getElementById('aura-live-copilot-row');
const input3=document.getElementById('aura-live-command');
const status3=document.getElementById('aura-live-copilot-status');
if(!panel3||!row3||!input3||!status3)return;
const max3=Math.max(1,Math.min(32,Number(cfg3.max_tunable_entities)||32));
const minSpeed3=Math.max(.1,Number(cfg3.player_speed_min)||1.5),maxSpeed3=Math.max(minSpeed3,Number(cfg3.player_speed_max)||14),defaultSpeed3=Math.max(minSpeed3,Math.min(maxSpeed3,Number(cfg3.player_speed_default)||6));
const clampSpeed3=value=>Math.max(minSpeed3,Math.min(maxSpeed3,Number(value)||defaultSpeed3));
const movement3=Object.seal({speed:defaultSpeed3});window.Aura3DPlayerTuning=movement3;
const hidden3=new Map(),history3=[];
const say3=value=>{status3.textContent=String(value||'').slice(0,220)};
const behavior3=(row,op)=>(row?.behaviors||[]).some(item=>item?.op===op);
function rows3(kind){
  if(typeof auraRows==='undefined'||!Array.isArray(auraRows)||typeof auraEntity!=='function'||typeof auraState==='undefined'||!auraState||typeof auraState.get!=='function')return [];
  const op=kind==='hazards'?'damage':'collectible';
  return auraRows.filter(row=>behavior3(row,op)).slice(0,max3).map(row=>({row,e:auraEntity(row.id),state:auraState.get(row.id)})).filter(item=>item.e&&item.state);
}
function active3(item){return !hidden3.has(item.row.id)&&item.state.taken!==true}
function scale3(item){const value=item.e.scale||item.row.scale||{};return{x:Number(value.x??1),y:Number(value.y??1),z:Number(value.z??1)}}
function suppress3(item,kind){
  if(!item||hidden3.has(item.row.id)||item.state.taken===true)return false;
  hidden3.set(item.row.id,{kind,scale:scale3(item),taken:item.state.taken===true});
  item.state.taken=true;
  item.e.scale={x:.000001,y:.000001,z:.000001};
  return true;
}
function restore3(id){
  const saved=hidden3.get(id);if(!saved)return false;
  const item=rows3(saved.kind).find(value=>value.row.id===id);if(!item){hidden3.delete(id);return false}
  item.state.taken=saved.taken===true;
  item.e.scale={...saved.scale};
  hidden3.delete(id);
  return true;
}
function hiddenSnapshot3(){return Array.from(hidden3.entries()).map(([id,value])=>({id,kind:value.kind,taken:value.taken===true,scale:{...value.scale}}))}
function snapshot3(){return{hidden:hiddenSnapshot3(),speed:clampSpeed3(movement3.speed)}}
function restoreSnapshot3(snapshot){
  for(const id of Array.from(hidden3.keys()))restore3(id);
  const rows=Array.isArray(snapshot?.hidden)?snapshot.hidden:[];
  for(const saved of rows){
    if(!saved||!['hazards','collectibles'].includes(saved.kind))continue;
    const item=rows3(saved.kind).find(value=>value.row.id===saved.id);if(!item)continue;
    hidden3.set(saved.id,{kind:saved.kind,taken:saved.taken===true,scale:{x:Number(saved.scale?.x??1),y:Number(saved.scale?.y??1),z:Number(saved.scale?.z??1)}});
    item.state.taken=true;
    item.e.scale={x:.000001,y:.000001,z:.000001};
  }
  movement3.speed=clampSpeed3(snapshot?.speed);
}
function enforce3(){
  for(const [id,saved] of hidden3){const item=rows3(saved.kind).find(value=>value.row.id===id);if(!item)continue;item.state.taken=true;item.e.scale={x:.000001,y:.000001,z:.000001}}
  movement3.speed=clampSpeed3(movement3.speed);
  requestAnimationFrame(enforce3);
}
requestAnimationFrame(enforce3);
function remember3(){history3.push(snapshot3());if(history3.length>20)history3.shift()}
function undo3(){const prior=history3.pop();if(!prior){say3('There is no Aura3D live tuning change to undo.');return false}restoreSnapshot3(prior);say3(`Aura restored the previous Aura3D live tuning state · speed ${movement3.speed.toFixed(1)}.`);return true}
function adjust3(kind,delta){
  const items=rows3(kind);if(!items.length){say3(`No authored ${kind} are available for Aura3D live tuning.`);return false}
  let changed=0;
  if(delta<0){const candidates=items.filter(active3).slice(-Math.min(Math.abs(delta),max3));if(!candidates.length){say3(`All tunable authored ${kind} are already suppressed.`);return false}remember3();for(const item of candidates)if(suppress3(item,kind))changed++}
  else {const ids=Array.from(hidden3.entries()).filter(([,value])=>value.kind===kind).map(([id])=>id).slice(0,Math.min(delta,max3));if(!ids.length){say3(`All currently available authored ${kind} are already active.`);return false}remember3();for(const id of ids)if(restore3(id))changed++}
  if(!changed){if(history3.length)history3.pop();return false}
  const active=items.filter(active3).length;
  say3(`Aura3D now has ${active} active ${kind} across ${items.length} tunable authored runtime entities. Use “Undo 3D” to roll this back.`);
  return true;
}
function speed3(multiplier,label){
  const before=clampSpeed3(movement3.speed),after=clampSpeed3(before*multiplier);if(Math.abs(after-before)<.0001){say3(`Aura3D player speed is already at the ${label==='faster'?'maximum':'minimum'} live-tuning limit.`);return false}
  remember3();movement3.speed=after;say3(`Aura made Aura3D player movement ${label} · ${after.toFixed(1)} units/sec. Use “Undo 3D” to roll this back.`);return true;
}
function command3(raw){
  const q=String(raw||'').trim().slice(0,240).toLowerCase().replace(/\s+/g,' ');if(!q)return false;
  if(/\b(undo 3d|undo aura3d|revert 3d|rollback 3d|roll back 3d)\b/.test(q))return undo3();
  if(/\b(faster|speed up|quicker)\b/.test(q)&&/\b(player|character|movement|move)\b/.test(q))return speed3(1.2,'faster');
  if(/\b(slower|slow down)\b/.test(q)&&/\b(player|character|movement|move)\b/.test(q))return speed3(.82,'slower');
  if(/\b(harder|more difficult)\b/.test(q))return adjust3('hazards',2);
  if(/\b(easier|less difficult)\b/.test(q))return adjust3('hazards',-2);
  if(/\b(more|add|increase|restore|reveal)\b/.test(q)&&/\b(hazard|hazards|enemy|enemies|danger)\b/.test(q))return adjust3('hazards',2);
  if(/\b(fewer|less|remove|reduce|hide)\b/.test(q)&&/\b(hazard|hazards|enemy|enemies|danger)\b/.test(q))return adjust3('hazards',-2);
  if(/\b(more|add|increase|restore|reveal)\b/.test(q)&&/\b(collectible|collectibles|pickup|pickups)\b/.test(q))return adjust3('collectibles',3);
  if(/\b(fewer|less|remove|reduce|hide)\b/.test(q)&&/\b(collectible|collectibles|pickup|pickups)\b/.test(q))return adjust3('collectibles',-3);
  return false;
}
const undoButton3=document.createElement('button');undoButton3.id='aura-live-3d-undo';undoButton3.type='button';undoButton3.textContent='Undo 3D';undoButton3.title='Undo the last Aura3D live tuning change';row3.appendChild(undoButton3);undoButton3.addEventListener('click',event=>{event.preventDefault();event.stopImmediatePropagation();undo3()});
document.addEventListener('click',event=>{if(event.target?.id!=='aura-live-apply')return;if(command3(input3.value)){event.preventDefault();event.stopImmediatePropagation()}},true);
document.addEventListener('keydown',event=>{if(event.target!==input3||event.key!=='Enter')return;if(command3(input3.value)){event.preventDefault();event.stopImmediatePropagation()}},true);
window.addEventListener('message',event=>{
  const data=event.data;if(event.source!==window.parent||!data||typeof data!=='object'||typeof data.command!=='string')return;
  const liveCfgEl=document.getElementById('aura-live-copilot-config');if(!liveCfgEl)return;
  let liveCfg={};try{liveCfg=JSON.parse(liveCfgEl.textContent||'{}')}catch(_e){return}
  const channelKey=String(liveCfg.voice_channel_query||''),channel=new URLSearchParams(location.search).get(channelKey)||'';
  if(!channel||data.type!==liveCfg.voice_message_type||data.game_id!==String(cfg3.game_id)||data.channel!==channel||data.command.length>240)return;
  if(command3(data.command)){input3.value=String(data.command).slice(0,240);event.stopImmediatePropagation()}
},true);
window.AuraLive3D=Object.freeze({version:1,command:command3,undo:undo3,snapshot:()=>Object.freeze({speed:clampSpeed3(movement3.speed),suppressed:hidden3.size}),contract:Object.freeze({private_playtest_only:true,authored_entities_only:true,arbitrary_entity_creation:false,project_persistence:false,player_speed_tuning:true,player_speed_min:minSpeed3,player_speed_max:maxSpeed3,max_tunable_entities:max3})});
})();
</script>
"""
    return html.replace(_BODY_CLOSE, fragment.replace("__CONFIG__", config) + _BODY_CLOSE, 1)


__all__ = ["inject_live_3d_tuning"]
