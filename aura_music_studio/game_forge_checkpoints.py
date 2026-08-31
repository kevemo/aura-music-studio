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


def _runtime_script(*, runtime: CheckpointRuntime, storage_key: str) -> str:
    key_json = json.dumps(storage_key, ensure_ascii=True)
    runtime_json = json.dumps(runtime, ensure_ascii=True)

    if runtime == "aura2d":
        capture = "return {v:1,runtime:checkpointRuntime,x:p.x,y:p.y,score:score,lives:lives,savedAt:Date.now()};"
        valid = "return finite(row.x)&&finite(row.y)&&finite(row.score)&&finite(row.lives);"
        restore = """
        p.x=Math.max(p.r,Math.min(Math.max(p.r,W-p.r),row.x));
        p.y=Math.max(60+p.r,Math.min(Math.max(60+p.r,H-p.r),row.y));
        score=Math.max(0,Math.trunc(row.score));
        lives=Math.max(1,Math.min(99,Math.trunc(row.lives)));
        const scoreEl=document.getElementById('score');
        if(scoreEl)scoreEl.textContent=`Score ${score} · Lives ${lives}`;
"""
    elif runtime == "aura3d":
        capture = "return {v:1,runtime:checkpointRuntime,x:Number(player.position.x),y:Number(player.position.y),z:Number(player.position.z),yaw:yaw,pitch:pitch,distance:distance,savedAt:Date.now()};"
        valid = "return finite(row.x)&&finite(row.y)&&finite(row.z)&&finite(row.yaw)&&finite(row.pitch)&&finite(row.distance);"
        restore = """
        const worldLimit=1000000;
        player.position.x=Math.max(-worldLimit,Math.min(worldLimit,row.x));
        player.position.y=Math.max(-worldLimit,Math.min(worldLimit,row.y));
        player.position.z=Math.max(-worldLimit,Math.min(worldLimit,row.z));
        yaw=row.yaw;
        pitch=Math.max(-.15,Math.min(1.2,row.pitch));
        distance=Math.max(4,Math.min(35,row.distance));
"""
    else:  # pragma: no cover - guarded by the public entry point
        raise ValueError(f"Unsupported checkpoint runtime: {runtime}")

    return f"""
const checkpointKey={key_json},checkpointRuntime={runtime_json};
const checkpointSave=document.getElementById('checkpoint-save'),checkpointResume=document.getElementById('checkpoint-resume'),checkpointReset=document.getElementById('checkpoint-reset'),checkpointStatus=document.getElementById('checkpoint-status');
const finite=v=>typeof v==='number'&&Number.isFinite(v);
function checkpointMessage(message){{if(checkpointStatus)checkpointStatus.textContent=message}}
function checkpointRead(){{
  try{{
    const raw=localStorage.getItem(checkpointKey);
    if(!raw)return null;
    const row=JSON.parse(raw);
    if(!row||row.v!==1||row.runtime!==checkpointRuntime||!checkpointValid(row)){{
      localStorage.removeItem(checkpointKey);
      checkpointMessage('Checkpoint was invalid and has been cleared.');
      return null;
    }}
    return row;
  }}catch(_error){{checkpointMessage('Checkpoint storage is unavailable.');return null}}
}}
function checkpointValid(row){{{valid}}}
function checkpointCapture(){{{capture}}}
function checkpointRefresh(){{
  const row=checkpointRead(),available=Boolean(row);
  if(checkpointResume)checkpointResume.disabled=!available;
  if(checkpointReset)checkpointReset.disabled=!available;
  return row;
}}
if(checkpointSave)checkpointSave.addEventListener('click',()=>{{
  try{{localStorage.setItem(checkpointKey,JSON.stringify(checkpointCapture()));checkpointMessage('Checkpoint saved.');checkpointRefresh()}}
  catch(_error){{checkpointMessage('Checkpoint storage is unavailable.')}}
}});
if(checkpointResume)checkpointResume.addEventListener('click',()=>{{
  const row=checkpointRead();if(!row){{checkpointRefresh();return}}
  {restore}
  checkpointMessage('Checkpoint restored.');
}});
if(checkpointReset)checkpointReset.addEventListener('click',()=>{{
  try{{localStorage.removeItem(checkpointKey);checkpointMessage('Checkpoint cleared.')}}
  catch(_error){{checkpointMessage('Checkpoint storage is unavailable.')}}
  checkpointRefresh();
}});
checkpointRefresh();
"""


def inject_checkpoint_controls(
    html_text: str,
    *,
    runtime: CheckpointRuntime,
    game_id: str,
    content_hash: str,
) -> str:
    """Add local-only playtest checkpoint controls without changing the runtime network policy.

    The storage key includes the exact trusted Game Forge integrity hash. A checkpoint therefore
    cannot be resumed into a different authored build, even when the game id is unchanged.
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

    controls = (
        "<button id='checkpoint-save' type='button'>Save checkpoint</button>"
        "<button id='checkpoint-resume' type='button'>Resume checkpoint</button>"
        "<button id='checkpoint-reset' type='button'>Reset checkpoint</button>"
        "<span id='checkpoint-status' role='status' aria-live='polite'></span>"
    )
    with_controls = html_text[:controls_end] + controls + html_text[controls_end:]

    storage_key = f"aura.game-forge.checkpoint.v1:{game_id}:{content_hash}"
    script = _runtime_script(runtime=runtime, storage_key=storage_key)
    script_end = with_controls.rfind(_SCRIPT_CLOSE)
    return with_controls[:script_end] + script + with_controls[script_end:]


__all__ = ["CheckpointRuntime", "inject_checkpoint_controls"]
