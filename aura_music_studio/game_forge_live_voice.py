from __future__ import annotations

import json
import re
import secrets
from html import escape
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from fastapi.responses import HTMLResponse

VOICE_MESSAGE_TYPE = "aura-live-command-v1"
VOICE_READY_MESSAGE_TYPE = "aura-live-ready-v1"
VOICE_CHANNEL_QUERY = "aura_live_channel"
MAX_VOICE_COMMAND_CHARS = 240
_FRAME_ID = "aura-game-playtest-frame"
_CHANNEL_RE = re.compile(r"^[A-Za-z0-9_-]{24,128}$")
_PRIVATE_FRAME_RE = re.compile(r"^/api/game-forge/games/([^/?#]+)/playtest-frame$")


def new_voice_channel() -> str:
    """Return an unpersisted capability token for one private playtest host/frame pair."""
    channel = secrets.token_urlsafe(32)
    if not _CHANNEL_RE.fullmatch(channel):  # pragma: no cover - token_urlsafe uses this alphabet.
        raise RuntimeError("Generated Aura live voice channel was outside the accepted capability alphabet")
    return channel


def private_voice_frame_url(frame_url: str, channel: str) -> str:
    """Bind an existing private playtest frame URL to one ephemeral voice channel."""
    if not _CHANNEL_RE.fullmatch(str(channel)):
        raise ValueError("Invalid Aura live voice channel")
    parts = urlsplit(str(frame_url))
    if parts.scheme or parts.netloc or parts.fragment or not _PRIVATE_FRAME_RE.fullmatch(parts.path):
        raise ValueError("Aura live voice can bind only a private Game Forge playtest frame")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query[VOICE_CHANNEL_QUERY] = str(channel)
    return urlunsplit(("", "", parts.path, urlencode(query), ""))


def live_voice_host_fragment(*, game_id: str, channel: str, frame_id: str = _FRAME_ID) -> str:
    """Render top-level speech capture that sends only bounded text into the sandboxed game.

    The Web Speech API is browser-provided and may use browser/vendor speech services. This bridge
    does not persist microphone audio or transcripts and does not send either through an application
    endpoint. Only the resulting bounded declarative command crosses into the exact child window.
    """
    if not _CHANNEL_RE.fullmatch(str(channel)):
        raise ValueError("Invalid Aura live voice channel")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", str(frame_id)):
        raise ValueError("Invalid Aura live voice frame id")

    game_id_json = json.dumps(str(game_id), ensure_ascii=True).replace("</", "<\\/")
    channel_json = json.dumps(str(channel), ensure_ascii=True).replace("</", "<\\/")
    frame_id_json = json.dumps(str(frame_id), ensure_ascii=True).replace("</", "<\\/")
    message_type_json = json.dumps(VOICE_MESSAGE_TYPE, ensure_ascii=True)
    ready_type_json = json.dumps(VOICE_READY_MESSAGE_TYPE, ensure_ascii=True)

    return f"""
<style id='aura-live-voice-host-style'>
#aura-live-voice-controls{{position:fixed;z-index:40;top:8px;right:152px;display:flex;align-items:center;gap:8px;max-width:min(460px,45vw)}}
#aura-live-voice-controls button{{white-space:nowrap}}
#aura-live-voice-status{{max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#c4cada}}
@media(max-width:800px){{#aura-live-voice-controls{{right:8px;top:64px;max-width:calc(100vw - 16px);background:#03050ae8;padding:6px;border-radius:9px}}#aura-live-voice-status{{max-width:180px}}}}
</style>
<div id='aura-live-voice-controls' role='group' aria-label='Aura live voice controls'>
  <button id='aura-live-voice-button' type='button' disabled title='Speak one bounded live playtest change to Aura'>🎙️ Talk to Aura</button>
  <small id='aura-live-voice-status' role='status' aria-live='polite'>Connecting Aura to the private playtest…</small>
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
const readyType={ready_type_json};
const maxChars={MAX_VOICE_COMMAND_CHARS};
const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
const speechAvailable=Boolean(Recognition);
let frameReady=false;
const say=(value)=>{{if(status)status.textContent=String(value||'').slice(0,180)}};
if(!frame||!button||!status)return;
window.addEventListener('message',event=>{{
  const data=event.data;
  if(event.source!==frame.contentWindow||!data||typeof data!=='object')return;
  if(data.type!==readyType||data.game_id!==gameId||data.channel!==channel)return;
  frameReady=true;
  if(speechAvailable)button.disabled=false;
  say(speechAvailable?'Aura voice bridge ready.':'Voice recognition is unavailable here. Typed live creation still works.');
}});
if(!Recognition){{
  button.disabled=true;
  button.title='Browser speech recognition is unavailable; use the typed Aura Live Creation command inside the game.';
  return;
}}
const recognition=new Recognition();
recognition.continuous=false;
recognition.interimResults=false;
recognition.maxAlternatives=1;
recognition.lang=document.documentElement.lang||'en-GB';
button.addEventListener('click',()=>{{
  if(!frameReady){{say('Aura is still connecting to the private playtest.');return}}
  try{{recognition.start();button.disabled=true;say('Listening… speak one live playtest change.')}}
  catch(_error){{say('Voice input is already active.')}}
}});
recognition.onend=()=>{{button.disabled=!frameReady}};
recognition.onerror=()=>{{button.disabled=!frameReady;say('Voice input was unavailable. Typed live creation still works.')}};
recognition.onresult=(event)=>{{
  const command=String(event.results?.[0]?.[0]?.transcript||'').trim().slice(0,maxChars);
  if(!command){{say('No live command was detected.');return}}
  if(!frameReady||!frame.contentWindow){{say('The private game frame is not ready.');return}}
  frame.contentWindow.postMessage({{type:messageType,game_id:gameId,channel,command}},'*');
  say(`Sent to Aura: ${{command}}`);
}};
}})();
</script>
""".strip()


def voice_permissions_policy() -> str:
    """Permit microphone capture in the top-level same-origin app, not delegated to the iframe."""
    return "microphone=(self)"


def install_live_voice_host_bridge() -> bool:
    """Wrap the existing Game Forge host page without changing its sandbox contract.

    Installation is idempotent. Public gallery hosts are delegated byte-for-byte to the existing
    implementation; only the authenticated private playtest host receives the ephemeral voice bridge.
    """
    from . import game_forge_api as foundation_game_api

    current = foundation_game_api._host_page
    if getattr(current, "_aura_live_voice_host_bridge", False):
        return False
    original = current

    def host_with_live_voice(
        title: str,
        frame_url: str,
        *,
        rating_line: str,
        popout: bool,
        return_url: str = "/game-creation",
        popout_url: str | None = None,
    ) -> HTMLResponse:
        response = original(
            title,
            frame_url,
            rating_line=rating_line,
            popout=popout,
            return_url=return_url,
            popout_url=popout_url,
        )
        parts = urlsplit(str(frame_url))
        match = _PRIVATE_FRAME_RE.fullmatch(parts.path)
        if parts.scheme or parts.netloc or parts.fragment or match is None:
            return response

        channel = new_voice_channel()
        voice_frame_url = private_voice_frame_url(frame_url, channel)
        game_id = unquote(match.group(1))
        html = response.body.decode("utf-8")
        old_frame = (
            f"<iframe src='{escape(frame_url, quote=True)}' sandbox='allow-scripts allow-pointer-lock' "
            "referrerpolicy='no-referrer' allow='gamepad'></iframe>"
        )
        new_frame = (
            f"<iframe id='{_FRAME_ID}' src='{escape(voice_frame_url, quote=True)}' "
            "sandbox='allow-scripts allow-pointer-lock' referrerpolicy='no-referrer' allow='gamepad'></iframe>"
        )
        if html.count(old_frame) != 1 or html.count("</body></html>") != 1:
            raise RuntimeError("Game Forge private host document boundary changed; Aura live voice bridge requires review")
        html = html.replace(old_frame, new_frame, 1)
        html = html.replace(
            "</body></html>",
            live_voice_host_fragment(game_id=game_id, channel=channel) + "</body></html>",
            1,
        )
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type", "permissions-policy"}
        }
        headers["Permissions-Policy"] = voice_permissions_policy()
        return HTMLResponse(html, status_code=response.status_code, headers=headers)

    setattr(host_with_live_voice, "_aura_live_voice_host_bridge", True)
    setattr(host_with_live_voice, "_aura_live_voice_original", original)
    foundation_game_api._host_page = host_with_live_voice
    return True


__all__ = [
    "MAX_VOICE_COMMAND_CHARS",
    "VOICE_CHANNEL_QUERY",
    "VOICE_MESSAGE_TYPE",
    "VOICE_READY_MESSAGE_TYPE",
    "install_live_voice_host_bridge",
    "live_voice_host_fragment",
    "new_voice_channel",
    "private_voice_frame_url",
    "voice_permissions_policy",
]
