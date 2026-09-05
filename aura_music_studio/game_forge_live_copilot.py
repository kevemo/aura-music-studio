from __future__ import annotations

import json

from .game_forge_live_voice import (
    VOICE_CHANNEL_QUERY,
    VOICE_MESSAGE_TYPE,
    VOICE_READY_MESSAGE_TYPE,
    install_live_voice_host_bridge,
)
from .game_forge_models import GameDNA

_MARKER = "id='aura-live-copilot'"
_BODY_CLOSE = "</body></html>"
_MAX_COMMAND_CHARS = 240
_MAX_HISTORY = 20
_MAX_DYNAMIC_OBJECTS = 32


def _runtime_kind(game: GameDNA) -> str:
    return "aura3d" if game.dimension == "3d" and game.engine_target == "aura3d" else "aura2d"


def inject_live_copilot(html: str, *, game: GameDNA) -> str:
    """Inject a creator-only, browser-local Aura live-play copilot.

    The generated bridge is inert by default and activates only on the authenticated private
    playtest-frame URL for this exact game. Public gallery frames and exported play.html files keep
    the bridge hidden/inert. Commands are a closed declarative mutation set: no eval, generated
    source, fetch/XHR/WebSocket path, remote assets, or persistent Game DNA mutation is introduced.
    """
    if _MARKER in html:
        return html
    if _BODY_CLOSE not in html:
        raise ValueError("Game Forge playtest document boundary changed; Aura live copilot injection requires review")

    config = json.dumps(
        {
            "version": 1,
            "game_id": str(game.id),
            "runtime": _runtime_kind(game),
            "max_command_chars": _MAX_COMMAND_CHARS,
            "max_history": _MAX_HISTORY,
            "max_dynamic_objects": _MAX_DYNAMIC_OBJECTS,
            "project_persistence": False,
            "external_network_access": False,
            "arbitrary_code_execution": False,
            "voice_message_type": VOICE_MESSAGE_TYPE,
            "voice_ready_message_type": VOICE_READY_MESSAGE_TYPE,
            "voice_channel_query": VOICE_CHANNEL_QUERY,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).replace("</", "<\\/")

    fragment = r"""
<style id='aura-live-copilot-style'>
#aura-live-scenery{display:none;position:fixed;inset:0;z-index:3;pointer-events:none;transition:background .45s ease,backdrop-filter .45s ease}
#aura-live-scenery[data-mode='day']{background:transparent}
#aura-live-scenery[data-mode='night']{background:linear-gradient(180deg,#04112b88,#01040dcc);backdrop-filter:brightness(.64) saturate(.82)}
#aura-live-scenery[data-mode='sunset']{background:linear-gradient(180deg,#ff985944,#812f6555 55%,#11082766)}
#aura-live-scenery[data-mode='storm']{background:linear-gradient(180deg,#26384c88,#070b12aa);backdrop-filter:brightness(.72) contrast(1.12) saturate(.7)}
#aura-live-scenery[data-mode='fog']{background:linear-gradient(180deg,#dce7ee66,#9eadb877);backdrop-filter:contrast(.78) brightness(1.06)}
#aura-live-scenery[data-mode='neon']{background:linear-gradient(135deg,#ff25d322,#00d9ff24,#6b45ff22);backdrop-filter:saturate(1.35) contrast(1.08)}
#aura-live-copilot{display:none;position:fixed;right:12px;top:12px;z-index:30;width:min(390px,calc(100vw - 24px));padding:10px;border:1px solid #ffffff38;border-radius:14px;background:#050713e8;color:#fff;font:13px/1.35 system-ui,sans-serif;box-shadow:0 14px 42px #0009;backdrop-filter:blur(12px)}
#aura-live-copilot strong{display:block;font-size:14px;margin-bottom:3px}
#aura-live-copilot small{display:block;color:#bbc6dc;margin-bottom:8px}
#aura-live-copilot-row{display:flex;gap:6px}
#aura-live-copilot input{min-width:0;flex:1;background:#0b1020;color:#fff;border:1px solid #ffffff2f;border-radius:9px;padding:8px 9px}
#aura-live-copilot button{background:#18213b;color:#fff;border:1px solid #ffffff30;border-radius:9px;padding:8px 9px;font-weight:800;cursor:pointer}
#aura-live-copilot-status{margin-top:7px;color:#cfe6ff;min-height:18px}
html[data-aura-live-creator='1'] #aura-live-scenery,html[data-aura-live-creator='1'] #aura-live-copilot{display:block}
</style>
<div id='aura-live-scenery' data-mode='day' aria-hidden='true'></div>
<div id='aura-live-copilot' role='region' aria-label='Aura live creation copilot'>
  <strong>✨ Aura Live Creation</strong>
  <small>Creator playtest only · reversible · not saved to the project</small>
  <div id='aura-live-copilot-row'>
    <input id='aura-live-command' maxlength='240' autocomplete='off' placeholder='Try: make it stormy, more hazards, faster player'>
    <button id='aura-live-mic' type='button' title='Speak to Aura'>🎙️</button>
    <button id='aura-live-apply' type='button'>Apply</button>
    <button id='aura-live-undo' type='button'>Undo</button>
  </div>
  <div id='aura-live-copilot-status' role='status' aria-live='polite'>Ready for a live playtest change.</div>
</div>
<script id='aura-live-copilot-config' type='application/json'>__CONFIG__</script>
<script id='aura-live-copilot-script'>
'use strict';
(()=>{
const liveCfg=JSON.parse(document.getElementById('aura-live-copilot-config').textContent||'{}');
const livePanel=document.getElementById('aura-live-copilot');
const liveExpectedPath=`/api/game-forge/games/${encodeURIComponent(String(liveCfg.game_id||''))}/playtest-frame`;
if(location.pathname!==liveExpectedPath){
  document.getElementById('aura-live-scenery')?.remove();
  livePanel?.remove();
  return;
}
document.documentElement.dataset.auraLiveCreator='1';
const liveInput=document.getElementById('aura-live-command');
const liveApply=document.getElementById('aura-live-apply');
const liveUndo=document.getElementById('aura-live-undo');
const liveMic=document.getElementById('aura-live-mic');
const liveStatus=document.getElementById('aura-live-copilot-status');
const liveScenery=document.getElementById('aura-live-scenery');
const liveHistory=[];
const liveMaxHistory=Math.max(1,Math.min(20,Number(liveCfg.max_history)||20));
const liveMaxObjects=Math.max(1,Math.min(32,Number(liveCfg.max_dynamic_objects)||32));
const liveMaxCommand=Math.max(1,Math.min(240,Number(liveCfg.max_command_chars)||240));
const liveVoiceRaw=new URLSearchParams(location.search).get(String(liveCfg.voice_channel_query||''))||'';
const liveVoiceChannel=/^[A-Za-z0-9_-]{24,128}$/.test(liveVoiceRaw)?liveVoiceRaw:'';
const live2D=()=>liveCfg.runtime==='aura2d';
const liveHasPlayer=()=>live2D()&&typeof p!=='undefined'&&p&&Number.isFinite(Number(p.s));
const liveStars=()=>live2D()&&typeof stars!=='undefined'&&Array.isArray(stars)?stars:null;
const liveHazards=()=>live2D()&&typeof haz!=='undefined'&&Array.isArray(haz)?haz:null;
function liveSay(message){liveStatus.textContent=String(message||'').slice(0,220)}
function liveTheme(){return String(liveScenery.dataset.mode||'day')}
function liveSnapshot(){
  return {
    theme:liveTheme(),
    speed:liveHasPlayer()?Number(p.s):null,
    stars:liveStars()?.length??null,
    hazards:liveHazards()?.length??null
  };
}
function liveResize(list,target,type){
  if(!list||!Number.isFinite(Number(target)))return false;
  const wanted=Math.max(0,Math.min(liveMaxObjects,Math.trunc(Number(target))));
  if(list.length>wanted)list.splice(wanted);
  else if(list.length<wanted&&typeof spawn==='function')spawn(list,wanted,type);
  return list.length===wanted;
}
function liveRestore(snapshot){
  if(!snapshot||typeof snapshot!=='object')return false;
  liveScenery.dataset.mode=String(snapshot.theme||'day');
  if(snapshot.speed!==null&&liveHasPlayer())p.s=Math.max(80,Math.min(520,Number(snapshot.speed)||260));
  if(snapshot.stars!==null)liveResize(liveStars(),snapshot.stars,'star');
  if(snapshot.hazards!==null)liveResize(liveHazards(),snapshot.hazards,'haz');
  return true;
}
function liveRemember(){
  liveHistory.push(liveSnapshot());
  if(liveHistory.length>liveMaxHistory)liveHistory.shift();
}
function liveSetTheme(mode){
  const allowed=new Set(['day','night','sunset','storm','fog','neon']);
  if(!allowed.has(mode))return false;
  liveRemember();
  liveScenery.dataset.mode=mode;
  liveSay(`Aura changed the playtest scenery to ${mode}. Say "undo" to roll it back.`);
  return true;
}
function liveAdjustSpeed(multiplier,label){
  if(!liveHasPlayer()){liveSay('Player-speed live tuning is available in Aura2D playtests.');return false}
  liveRemember();
  p.s=Math.max(80,Math.min(520,Number(p.s)*multiplier));
  liveSay(`Aura made the player ${label}. This is a playtest-only change.`);
  return true;
}
function liveAdjustObjects(kind,delta){
  const list=kind==='stars'?liveStars():liveHazards(),type=kind==='stars'?'star':'haz';
  if(!list){liveSay('Dynamic pickup/hazard tuning is available in Aura2D playtests.');return false}
  liveRemember();
  const before=list.length,target=Math.max(0,Math.min(liveMaxObjects,before+delta));
  liveResize(list,target,type);
  liveSay(`Aura changed ${kind==='stars'?'collectibles':'hazards'} from ${before} to ${list.length}.`);
  return true;
}
function liveUndoLast(){
  const previous=liveHistory.pop();
  if(!previous){liveSay('There is no live playtest change to undo.');return false}
  liveRestore(previous);
  liveSay('Aura rolled back the last live playtest change.');
  return true;
}
function liveHelp(){
  liveSay('Try: night, sunset, storm, fog, neon, day, faster/slower player, more/fewer collectibles, more/fewer hazards, harder, easier, or undo.');
  return true;
}
function liveCommand(raw){
  const text=String(raw||'').trim().slice(0,liveMaxCommand);
  const q=text.toLowerCase().replace(/\s+/g,' ');
  if(!q){liveSay('Tell Aura what you want to change while you play.');return false}
  if(/\b(undo|roll back|rollback|revert)\b/.test(q))return liveUndoLast();
  if(/\b(help|what can you do|commands)\b/.test(q))return liveHelp();
  if(/\b(night|nighttime|moonlight)\b/.test(q))return liveSetTheme('night');
  if(/\b(sunset|golden hour|dusk)\b/.test(q))return liveSetTheme('sunset');
  if(/\b(storm|stormy|thunder|rainy)\b/.test(q))return liveSetTheme('storm');
  if(/\b(fog|foggy|mist|misty)\b/.test(q))return liveSetTheme('fog');
  if(/\b(neon|cyberpunk)\b/.test(q))return liveSetTheme('neon');
  if(/\b(day|daylight|clear weather|clear sky)\b/.test(q))return liveSetTheme('day');
  if(/\b(harder|more difficult)\b/.test(q)){const changed=liveAdjustObjects('hazards',3);if(changed&&liveHasPlayer())p.s=Math.max(80,Math.min(520,Number(p.s)*.92));return changed}
  if(/\b(easier|less difficult)\b/.test(q)){const changed=liveAdjustObjects('hazards',-2);if(changed&&liveHasPlayer())p.s=Math.max(80,Math.min(520,Number(p.s)*1.08));return changed}
  if(/\b(faster|speed up|quicker)\b/.test(q)&&/\b(player|character|movement|move)\b/.test(q))return liveAdjustSpeed(1.2,'faster');
  if(/\b(slower|slow down)\b/.test(q)&&/\b(player|character|movement|move)\b/.test(q))return liveAdjustSpeed(.82,'slower');
  if(/\b(more|add|increase)\b/.test(q)&&/\b(star|stars|pickup|pickups|collectible|collectibles)\b/.test(q))return liveAdjustObjects('stars',3);
  if(/\b(fewer|less|remove|reduce)\b/.test(q)&&/\b(star|stars|pickup|pickups|collectible|collectibles)\b/.test(q))return liveAdjustObjects('stars',-3);
  if(/\b(more|add|increase)\b/.test(q)&&/\b(hazard|hazards|enemy|enemies|danger)\b/.test(q))return liveAdjustObjects('hazards',2);
  if(/\b(fewer|less|remove|reduce)\b/.test(q)&&/\b(hazard|hazards|enemy|enemies|danger)\b/.test(q))return liveAdjustObjects('hazards',-2);
  liveSay('That live instruction is outside the safe playtest mutation set. Ask Aura for "help" to see supported changes.');
  return false;
}
if(liveVoiceChannel){
  liveMic.hidden=true;
  liveMic.setAttribute('aria-hidden','true');
  window.addEventListener('message',event=>{
    const data=event.data;
    if(event.source!==window.parent||!data||typeof data!=='object')return;
    if(data.type!==liveCfg.voice_message_type||data.game_id!==String(liveCfg.game_id)||data.channel!==liveVoiceChannel)return;
    if(typeof data.command!=='string'||data.command.length>liveMaxCommand)return;
    const command=data.command.trim();
    if(!command)return;
    liveInput.value=command;
    liveCommand(command);
  });
  window.parent.postMessage({
    type:liveCfg.voice_ready_message_type,
    game_id:String(liveCfg.game_id),
    channel:liveVoiceChannel
  },'*');
}
liveApply.addEventListener('click',()=>liveCommand(liveInput.value));
liveUndo.addEventListener('click',liveUndoLast);
liveInput.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();liveCommand(liveInput.value)}});
const LiveSpeechRecognition=window.SpeechRecognition||window.webkitSpeechRecognition;
if(!LiveSpeechRecognition){
  liveMic.hidden=true;
  liveMic.setAttribute('aria-hidden','true');
}else{
  const recognition=new LiveSpeechRecognition();
  recognition.continuous=false;
  recognition.interimResults=false;
  recognition.maxAlternatives=1;
  recognition.lang=document.documentElement.lang||'en-GB';
  liveMic.addEventListener('click',()=>{
    try{recognition.start();liveSay('Listening… speak your live playtest change.')}catch(_error){liveSay('Voice input is already active.')}
  });
  recognition.onresult=event=>{
    const transcript=String(event.results?.[0]?.[0]?.transcript||'').slice(0,liveMaxCommand);
    liveInput.value=transcript;
    liveCommand(transcript);
  };
  recognition.onerror=()=>liveSay('Browser voice input was unavailable. Type the instruction instead.');
}
window.AuraLiveCreation=Object.freeze({
  version:1,
  command:liveCommand,
  undo:liveUndoLast,
  snapshot:()=>Object.freeze({...liveSnapshot()}),
  contract:Object.freeze({
    creator_playtest_only:true,
    project_persistence:false,
    external_network_access:false,
    arbitrary_code_execution:false,
    max_history:liveMaxHistory,
    max_dynamic_objects:liveMaxObjects
  })
});
livePanel.dataset.ready='true';
})();
</script>
"""
    return html.replace(_BODY_CLOSE, fragment.replace("__CONFIG__", config) + _BODY_CLOSE, 1)


install_live_voice_host_bridge()

__all__ = ["inject_live_copilot"]
