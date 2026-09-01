from __future__ import annotations

import json
import re
from typing import Literal

CheckpointRuntime = Literal["aura2d", "aura3d"]

_SAFE_GAME_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_CONTROLS_MARKER = "<div id='media-controls'>"
_SCRIPT_CLOSE = "</script>"
_CHECKPOINT_BUTTON_ID = "checkpoint-save"


def _validate_identity(*, game_id: str, content_hash: str) -> None:
    if not _SAFE_GAME_ID.fullmatch(game_id):
        raise ValueError("Game checkpoint id is invalid")
    if not _SHA256_HEX.fullmatch(content_hash):
        raise ValueError("Game checkpoint content hash must be a lowercase SHA-256 digest")


def _runtime_script(
    *,
    runtime: CheckpointRuntime,
    storage_prefix: str,
    legacy_key: str,
) -> str:
    prefix_json = json.dumps(storage_prefix, ensure_ascii=True)
    legacy_json = json.dumps(legacy_key, ensure_ascii=True)
    runtime_json = json.dumps(runtime, ensure_ascii=True)

    if runtime == "aura2d":
        capture_state = "return {x:Number(p.x),y:Number(p.y),score:Number(score),lives:Number(lives),aura:window.AuraRuntimeState?.exportState?.()||null};"
        valid_state = "const s=row.state;return !!s&&finite(s.x)&&finite(s.y)&&finite(s.score)&&finite(s.lives)&&(s.aura===null||s.aura===undefined||Boolean(window.AuraRuntimeState?.validateState?.(s.aura)));"
        apply_state = """
  const s=row.state;
  p.x=Math.max(p.r,Math.min(Math.max(p.r,W-p.r),s.x));
  p.y=Math.max(60+p.r,Math.min(Math.max(60+p.r,H-p.r),s.y));
  score=Math.max(0,Math.trunc(s.score));
  lives=Math.max(1,Math.min(99,Math.trunc(s.lives)));
  const scoreEl=document.getElementById('score');
  if(scoreEl)scoreEl.textContent=`Score ${score} · Lives ${lives}`;
  if(s.aura&&window.AuraRuntimeState?.importState)window.AuraRuntimeState.importState(s.aura);
"""
        legacy_valid = "finite(row.x)&&finite(row.y)&&finite(row.score)&&finite(row.lives)"
        legacy_state = "{x:Number(row.x),y:Number(row.y),score:Number(row.score),lives:Number(row.lives),aura:null}"
    elif runtime == "aura3d":
        capture_state = (
            "return {x:Number(player.position.x),y:Number(player.position.y),z:Number(player.position.z),"
            "yaw:Number(yaw),pitch:Number(pitch),distance:Number(distance),aura:window.AuraRuntimeState?.exportState?.()||null};"
        )
        valid_state = (
            "const s=row.state;return !!s&&finite(s.x)&&finite(s.y)&&finite(s.z)&&finite(s.yaw)&&finite(s.pitch)&&finite(s.distance)"
            "&&(s.aura===null||s.aura===undefined||Boolean(window.AuraRuntimeState?.validateState?.(s.aura)));"
        )
        apply_state = """
  const s=row.state,worldLimit=1000000;
  player.position.x=Math.max(-worldLimit,Math.min(worldLimit,s.x));
  player.position.y=Math.max(-worldLimit,Math.min(worldLimit,s.y));
  player.position.z=Math.max(-worldLimit,Math.min(worldLimit,s.z));
  yaw=s.yaw;
  pitch=Math.max(-.15,Math.min(1.2,s.pitch));
  distance=Math.max(4,Math.min(35,s.distance));
  if(s.aura&&window.AuraRuntimeState?.importState)window.AuraRuntimeState.importState(s.aura);
"""
        legacy_valid = (
            "finite(row.x)&&finite(row.y)&&finite(row.z)&&finite(row.yaw)&&finite(row.pitch)&&finite(row.distance)"
        )
        legacy_state = (
            "{x:Number(row.x),y:Number(row.y),z:Number(row.z),yaw:Number(row.yaw),pitch:Number(row.pitch),distance:Number(row.distance),aura:null}"
        )
    else:  # pragma: no cover - guarded by the public entry point
        raise ValueError(f"Unsupported checkpoint runtime: {runtime}")

    template = """
const checkpointPrefix=__PREFIX__,checkpointLegacyKey=__LEGACY__,checkpointRuntime=__RUNTIME__;
const checkpointSlots=['slot1','slot2','slot3'],checkpointAllSlots=[...checkpointSlots,'autosave'];
const checkpointSlot=document.getElementById('checkpoint-slot'),checkpointName=document.getElementById('checkpoint-name'),checkpointSave=document.getElementById('checkpoint-save'),checkpointLoad=document.getElementById('checkpoint-load'),checkpointContinue=document.getElementById('checkpoint-continue'),checkpointNew=document.getElementById('checkpoint-new'),checkpointReset=document.getElementById('checkpoint-reset'),checkpointStatus=document.getElementById('checkpoint-status');
const finite=v=>typeof v==='number'&&Number.isFinite(v),checkpointKey=slot=>checkpointPrefix+slot;
let checkpointDirty=false,checkpointLastAutosaveSignature='',checkpointInitialState=null;
window.AuraMarkSaveDirty=()=>{checkpointDirty=true};
function checkpointMessage(message){if(checkpointStatus)checkpointStatus.textContent=message}
function checkpointCaptureState(){__CAPTURE_STATE__}
function checkpointValidState(row){__VALID_STATE__}
function checkpointApply(row){
__APPLY_STATE__
}
function checkpointRow(slot,name,state){return {v:2,runtime:checkpointRuntime,slot,name:String(name||'').trim().slice(0,40),savedAt:Date.now(),state}}
function checkpointValidRow(row,slot){return !!row&&row.v===2&&row.runtime===checkpointRuntime&&row.slot===slot&&typeof row.name==='string'&&row.name.length<=40&&finite(row.savedAt)&&row.savedAt>0&&checkpointValidState(row)}
function checkpointRead(slot,quiet=false){
  if(!checkpointAllSlots.includes(slot))return null;
  try{
    const raw=localStorage.getItem(checkpointKey(slot));
    if(!raw)return null;
    const row=JSON.parse(raw);
    if(!checkpointValidRow(row,slot)){
      localStorage.removeItem(checkpointKey(slot));
      if(!quiet)checkpointMessage('Invalid save data was cleared.');
      return null;
    }
    return row;
  }catch(_error){
    try{localStorage.removeItem(checkpointKey(slot))}catch(_ignored){}
    if(!quiet)checkpointMessage('Invalid or unavailable save data was cleared.');
    return null;
  }
}
function checkpointWrite(slot,name,quiet=false){
  if(!checkpointAllSlots.includes(slot))return null;
  try{
    const row=checkpointRow(slot,name,checkpointCaptureState());
    localStorage.setItem(checkpointKey(slot),JSON.stringify(row));
    if(!quiet)checkpointMessage(slot==='autosave'?'Game autosaved.':`${row.name||'Save'} saved.`);
    return row;
  }catch(_error){if(!quiet)checkpointMessage('Game save storage is unavailable.');return null}
}
function checkpointDelete(slot,quiet=false){
  if(!checkpointAllSlots.includes(slot))return;
  try{localStorage.removeItem(checkpointKey(slot));if(!quiet)checkpointMessage('Save slot cleared.')}
  catch(_error){if(!quiet)checkpointMessage('Game save storage is unavailable.')}
}
function checkpointNewest(){
  const rows=checkpointAllSlots.map(slot=>checkpointRead(slot,true)).filter(Boolean);
  rows.sort((a,b)=>b.savedAt-a.savedAt);
  return rows[0]||null;
}
function checkpointFormat(row,slot){
  if(!row)return slot==='slot1'?'Slot 1':slot==='slot2'?'Slot 2':'Slot 3';
  const stamp=new Date(row.savedAt).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
  const fallback=slot==='slot1'?'Slot 1':slot==='slot2'?'Slot 2':'Slot 3';
  return `${row.name||fallback} · ${stamp}`;
}
function checkpointRefresh(){
  const selected=checkpointSlot?.value||'slot1';
  for(const option of checkpointSlot?.options||[]){const row=checkpointRead(option.value,true);option.textContent=checkpointFormat(row,option.value)}
  const selectedRow=checkpointRead(selected,true),newest=checkpointNewest();
  if(checkpointLoad)checkpointLoad.disabled=!selectedRow;
  if(checkpointReset)checkpointReset.disabled=!selectedRow;
  if(checkpointContinue)checkpointContinue.disabled=!newest;
  if(checkpointName&&document.activeElement!==checkpointName)checkpointName.value=selectedRow?.name||'';
  return {selectedRow,newest};
}
function checkpointRestore(row,message){
  if(!row||!checkpointValidRow(row,row.slot))return false;
  checkpointApply(row);checkpointDirty=false;checkpointLastAutosaveSignature=JSON.stringify(row.state);checkpointMessage(message);return true;
}
function checkpointMigrateLegacy(){
  try{
    if(localStorage.getItem(checkpointKey('slot1'))||!checkpointLegacyKey)return;
    const raw=localStorage.getItem(checkpointLegacyKey);if(!raw)return;
    const row=JSON.parse(raw);
    if(row&&row.v===1&&row.runtime===checkpointRuntime&&(__LEGACY_VALID__)){
      const migrated=checkpointRow('slot1','Migrated checkpoint',__LEGACY_STATE__);
      localStorage.setItem(checkpointKey('slot1'),JSON.stringify(migrated));
    }
    localStorage.removeItem(checkpointLegacyKey);
  }catch(_error){try{localStorage.removeItem(checkpointLegacyKey)}catch(_ignored){}}
}
function checkpointAutosave(){
  if(!checkpointDirty||document.visibilityState==='hidden')return;
  const state=checkpointCaptureState(),signature=JSON.stringify(state);if(signature===checkpointLastAutosaveSignature)return;
  try{const row=checkpointRow('autosave','Autosave',state);localStorage.setItem(checkpointKey('autosave'),JSON.stringify(row));checkpointLastAutosaveSignature=signature;checkpointDirty=false;checkpointMessage('Game autosaved.');checkpointRefresh()}catch(_error){}
}
checkpointMigrateLegacy();
checkpointInitialState=checkpointCaptureState();
if(checkpointSlot)checkpointSlot.addEventListener('change',checkpointRefresh);
if(checkpointSave)checkpointSave.addEventListener('click',()=>{const slot=checkpointSlot?.value||'slot1',fallback=slot==='slot1'?'Slot 1':slot==='slot2'?'Slot 2':'Slot 3',name=(checkpointName?.value||'').trim()||fallback;const row=checkpointWrite(slot,name);if(row){checkpointDirty=false;checkpointRefresh()}});
if(checkpointLoad)checkpointLoad.addEventListener('click',()=>{const slot=checkpointSlot?.value||'slot1',row=checkpointRead(slot);if(row)checkpointRestore(row,`${row.name||'Save'} loaded.`);checkpointRefresh()});
if(checkpointContinue)checkpointContinue.addEventListener('click',()=>{const row=checkpointNewest();if(row)checkpointRestore(row,row.slot==='autosave'?'Continued from autosave.':`Continued from ${row.name||'save'}.`);checkpointRefresh()});
if(checkpointNew)checkpointNew.addEventListener('click',()=>{checkpointApply(checkpointRow('autosave','New game',checkpointInitialState));checkpointDirty=false;checkpointMessage('New game state started. Existing saves are preserved.')});
if(checkpointReset)checkpointReset.addEventListener('click',()=>{checkpointDelete(checkpointSlot?.value||'slot1');checkpointRefresh()});
for(const eventName of ['keydown','pointerdown','wheel'])addEventListener(eventName,()=>{checkpointDirty=true},{passive:true});
addEventListener('pointermove',event=>{if(event.buttons)checkpointDirty=true},{passive:true});
setInterval(checkpointAutosave,20000);
addEventListener('pagehide',()=>{if(checkpointDirty){const state=checkpointCaptureState(),signature=JSON.stringify(state);if(signature!==checkpointLastAutosaveSignature){try{localStorage.setItem(checkpointKey('autosave'),JSON.stringify(checkpointRow('autosave','Autosave',state)))}catch(_error){}}}});
checkpointRefresh();
"""
    return (
        template.replace("__PREFIX__", prefix_json)
        .replace("__LEGACY__", legacy_json)
        .replace("__RUNTIME__", runtime_json)
        .replace("__CAPTURE_STATE__", capture_state)
        .replace("__VALID_STATE__", valid_state)
        .replace("__APPLY_STATE__", apply_state.strip())
        .replace("__LEGACY_VALID__", legacy_valid)
        .replace("__LEGACY_STATE__", legacy_state)
    )


def inject_checkpoint_controls(
    html_text: str,
    *,
    runtime: CheckpointRuntime,
    game_id: str,
    content_hash: str,
) -> str:
    """Add hash-scoped local save slots and autosave to a closed Game Forge playtest.

    Saves never leave the browser. The storage prefix includes the exact trusted Game Forge
    integrity hash, so player progress from one authored build cannot be loaded into another.
    Version-1 single checkpoints are migrated into Slot 1 only when the game id, content hash and
    runtime still match exactly. When the closed Aura runtime-state kernel is present, its validated
    quest/inventory/flag/gate/state-machine snapshot is carried inside the same version-2 save row.
    """
    if runtime not in {"aura2d", "aura3d"}:
        raise ValueError(f"Unsupported checkpoint runtime: {runtime}")
    _validate_identity(game_id=game_id, content_hash=content_hash)
    if _CHECKPOINT_BUTTON_ID in html_text:
        return html_text

    controls_start = html_text.find(_CONTROLS_MARKER)
    if controls_start < 0:
        raise ValueError("Game Forge playtest has no media-controls container")
    controls_end = html_text.find("</div>", controls_start)
    if controls_end < 0:
        raise ValueError("Game Forge playtest media-controls container is not closed")
    script_end = html_text.rfind(_SCRIPT_CLOSE)
    if script_end < 0:
        raise ValueError("Game Forge playtest has no runtime script")

    field_style = "background:#0c1324e8;color:#fff;border:1px solid #ffffff33;border-radius:9px;padding:8px 9px"
    controls = (
        f"<select id='checkpoint-slot' aria-label='Game save slot' style='{field_style}'>"
        "<option value='slot1'>Slot 1</option><option value='slot2'>Slot 2</option><option value='slot3'>Slot 3</option></select>"
        f"<input id='checkpoint-name' type='text' maxlength='40' placeholder='Save name' aria-label='Save name' style='{field_style};max-width:150px'>"
        "<button id='checkpoint-save' type='button'>Save slot</button>"
        "<button id='checkpoint-load' type='button'>Load slot</button>"
        "<button id='checkpoint-continue' type='button'>Continue</button>"
        "<button id='checkpoint-new' type='button'>New game</button>"
        "<button id='checkpoint-reset' type='button'>Delete slot</button>"
        "<span id='checkpoint-status' role='status' aria-live='polite'></span>"
    )
    with_controls = html_text[:controls_end] + controls + html_text[controls_end:]

    storage_prefix = f"aura.game-forge.save.v2:{game_id}:{content_hash}:"
    legacy_key = f"aura.game-forge.checkpoint.v1:{game_id}:{content_hash}"
    script = _runtime_script(runtime=runtime, storage_prefix=storage_prefix, legacy_key=legacy_key)
    script_end = with_controls.rfind(_SCRIPT_CLOSE)
    return with_controls[:script_end] + script + with_controls[script_end:]


__all__ = ["CheckpointRuntime", "inject_checkpoint_controls"]
