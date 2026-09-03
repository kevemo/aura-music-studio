from __future__ import annotations

import json
import re
import secrets
from html import escape
from urllib.parse import quote, urlencode

from .game_forge_models import GameDNA

VOICE_MESSAGE_TYPE = "aura-live-command-v1"
VOICE_CHANNEL_QUERY = "aura_live_channel"
MAX_VOICE_COMMAND_CHARS = 240
_CHANNEL_RE = re.compile(r"^[A-Za-z0-9_-]{24,128}$")


def new_voice_channel() -> str:
    """Return a short-lived, unpersisted channel capability for one private playtest host/frame pair."""
    channel = secrets.token_urlsafe(32)
    if not _CHANNEL_RE.fullmatch(channel):  # pragma: no cover - token_urlsafe is constrained to this alphabet.
        raise RuntimeError("Generated Aura live voice channel was outside the accepted capability alphabet")
    return channel


def private_voice_frame_url(game: GameDNA, channel: str) -> str:
    if not _CHANNEL_RE.fullmatch(str(channel)):
        raise ValueError("Invalid Aura live voice channel")
    game_id = quote(str(game.id), safe="")
    query = urlencode({VOICE_CHANNEL_QUERY: channel})
    return f"/api/game-forge/games/{game_id}/playtest-frame?{query}"


def live_voice_host_fragment(*, game: GameDNA, channel: str, frame_id: str) -> str:
    """Render top-level browser speech controls for the authenticated private playtest host.

    The game iframe intentionally remains sandboxed without allow-same-origin. Speech capture therefore
    occurs in the authenticated parent document and only a bounded declarative text command is posted
    into the exact child window. No microphone stream, transcript history, OAuth/session state, or
    arbitrary executable payload is persisted or sent through an application network endpoint.
    """
    if not _CHANNEL_RE.fullmatch(str(channel)):
        raise ValueError("Invalid Aura live voice channel")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", str(frame_id)):
        raise ValueError("Invalid Aura live voice frame id")

    game_id_json = json.dumps(str(game.id), ensure_ascii=True).replace("</", "<\\/")
    channel_json = json.dumps(channel, ensure_ascii=True).replace("</", "<\\/")
    frame_id_json = json.dumps(frame_id, ensure_ascii=True).replace("</", "<\\/")
    message_type_json = json.dumps(VOICE_MESSAGE_TYPE, ensure_ascii=True)
    max_chars = MAX_VOICE_COMMAND_CHARS

    return f"""
<div id='aura-live-voice-controls' role='group' aria-label='Aura live voice controls'>
  <button id='aura-live-voice-button' type='button' title='Speak a bounded live playtest change to Aura'>🎙️ Talk to Aura</button>
  <small id='aura-live-voice-status' role='status' aria-live='polite'>Voice stays in the private creator playtest bridge.</small>
</div>
<script id='aura-live-voice-host-script'>
'use strict';
(()=>{{
const frame=document.getElementById({frame_id_json});
const button=document.getElementById('aura-live-voice-button');
const status=document.getElementById('aura-live-voice-status');
const gameId={game_id_json};
const channel={channel_json};
const messageType={message_type_json};
const maxChars={max_chars};
const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
const say=(value)=>{{status.textContent=String(value||'').slice(0,180)}};
if(!frame||!button||!status) return;
if(!Recognition){{
  button.disabled=true;
  button.title='Browser speech recognition is unavailable; use the typed Aura Live Creation command inside the game.';
  say('Voice recognition is unavailable in this browser. Typed live creation remains available.');
  return;
}}
const recognition=new Recognition();
recognition.continuous=false;
recognition.interimResults=false;
recognition.maxAlternatives=1;
recognition.lang=document.documentElement.lang||'en-GB';
button.addEventListener('click',()=>{{
  try{{recognition.start();button.disabled=true;say('Listening… speak one live playtest change.')}}
  catch(_error){{say('Voice input is already active.')}}
}});
recognition.onend=()=>{{button.disabled=false}};
recognition.onerror=()=>{{button.disabled=false;say('Voice input was unavailable. Typed live creation remains available.')}};
recognition.onresult=(event)=>{{
  const command=String(event.results?.[0]?.[0]?.transcript||'').trim().slice(0,maxChars);
  if(!command){{say('No live command was detected.');return}}
  if(!frame.contentWindow){{say('The private game frame is not ready.');return}}
  frame.contentWindow.postMessage({{type:messageType,game_id:gameId,channel,command}},'*');
  say(`Sent to Aura: ${{command}}`);
}};
}})();
</script>
""".strip()


def voice_permissions_policy() -> str:
    """Permit microphone capture only in this top-level same-origin app, never in embedded origins."""
    return "microphone=(self)"


__all__ = [
    "MAX_VOICE_COMMAND_CHARS",
    "VOICE_CHANNEL_QUERY",
    "VOICE_MESSAGE_TYPE",
    "live_voice_host_fragment",
    "new_voice_channel",
    "private_voice_frame_url",
    "voice_permissions_policy",
]
