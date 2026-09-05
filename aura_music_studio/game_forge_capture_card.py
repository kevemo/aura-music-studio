from __future__ import annotations

import json
from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .branding import PRODUCT_FULL_NAME
from .game_forge_store import load_game
from .plans import GAME_PLAYTEST

router = APIRouter(tags=["Aura Game Forge Capture Card"])


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _game(game_id: str):
    try:
        return load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc


@router.get("/api/game-forge/capture-card/capabilities")
def capture_card_capabilities(request: Request):
    member = _member(request)
    if not member.plan.has(GAME_PLAYTEST):
        raise HTTPException(403, "Game display/playtesting is unavailable on this membership")
    return {
        "browser_local_capture": True,
        "requires_secure_context": True,
        "requires_user_media_permission": True,
        "requires_uvc_or_browser_visible_capture_device": True,
        "hdmi_direct_input_without_capture_hardware": False,
        "server_receives_capture_stream": False,
        "recording_enabled_by_default": False,
        "upload_enabled_by_default": False,
        "fullscreen_supported_when_browser_allows": True,
        "picture_in_picture_supported_when_browser_allows": True,
        "popout_supported_when_browser_allows": True,
        "controller_input_routed_to_console": False,
        "note": "The console HDMI feed must enter a capture card/device that the operating system exposes to the browser as a video input. The controller normally stays connected to the console.",
    }


@router.get("/game-creation/capture-card/{game_id}", response_class=HTMLResponse, include_in_schema=False)
def capture_card_portal(game_id: str, request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        return RedirectResponse(f"/signin?next=/game-creation/capture-card/{escape(game_id, quote=True)}", status_code=303)
    if not member.plan.has(GAME_PLAYTEST):
        return RedirectResponse("/game-creation", status_code=303)
    game = _game(game_id)

    brand = escape(PRODUCT_FULL_NAME)
    title = escape(game.title)
    game_json = json.dumps(game.id)
    page = r"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Capture Card — __BRAND__</title><style>
:root{--bg:#05060b;--panel:#101523;--line:#ffffff22;--gold:#efc96b;--violet:#9b72ff;--green:#77e1a7;--red:#ff91a7;--muted:#bec7d7;--cyan:#5ee4ff}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 8% 0,#501d7066,transparent 30%),radial-gradient(circle at 92% 0,#0a546455,transparent 30%),var(--bg);color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1360px,calc(100% - 24px));margin:auto}.nav{position:sticky;top:0;z-index:20;background:#05070eee;border-bottom:1px solid var(--line)}.navin,.row{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.navin{min-height:64px}.brand{font-weight:950;color:#fff;text-decoration:none}.brand small{display:block;color:var(--gold);font-size:.66rem;letter-spacing:.13em;text-transform:uppercase}.hero{padding:32px 0 16px}.eyebrow{color:var(--gold);font-size:.7rem;font-weight:950;letter-spacing:.14em;text-transform:uppercase}h1{font-size:clamp(2.4rem,5vw,4.8rem);margin:.12em 0;letter-spacing:-.05em}.muted{color:var(--muted);line-height:1.55}.grid{display:grid;grid-template-columns:340px 1fr;gap:14px;padding-bottom:60px}.card{background:linear-gradient(145deg,#12182aee,#090d18ee);border:1px solid var(--line);border-radius:18px;padding:16px;margin-bottom:12px}.screen{background:#000;border:1px solid #ffffff2b;border-radius:18px;overflow:hidden;min-height:420px;display:grid;place-items:center;position:relative}.screen video{width:100%;height:100%;max-height:78vh;object-fit:contain;background:#000}.placeholder{position:absolute;inset:0;display:grid;place-items:center;text-align:center;padding:30px;color:var(--muted);pointer-events:none}.placeholder.hide{display:none}.status{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--line);border-radius:999px;padding:6px 10px;font-size:.78rem}.dot{width:8px;height:8px;border-radius:50%;background:#777}.connected .dot{background:var(--green);box-shadow:0 0 18px #77e1a799}.error .dot{background:var(--red)}select,button,.btn{font:inherit;border:1px solid var(--line);background:#ffffff09;color:#fff;border-radius:10px;padding:10px 12px;font-weight:800}select{width:100%;margin:6px 0 12px;background:#0a0e18}button,.btn{cursor:pointer;text-decoration:none;display:inline-block}.primary{border:0;background:linear-gradient(120deg,var(--gold),var(--violet));color:#160c1d}.danger{border-color:#ff91a766;color:#ffd6df}.actions{display:flex;gap:8px;flex-wrap:wrap}.kv{display:grid;grid-template-columns:1fr auto;gap:8px;border-bottom:1px solid #ffffff12;padding:7px 0}.warning{border-left:3px solid var(--gold)}@media(max-width:900px){.grid{grid-template-columns:1fr}.screen{min-height:300px}}
</style></head><body><nav class='nav'><div class='wrap navin'><a class='brand' href='/game-creation'>__BRAND__<small>Aura Game Forge · Console Capture Display</small></a><div class='actions'><a class='btn' href='/game-creation/play/__GAME_ID__'>Game Forge Playtest</a><a class='btn' href='/game-creation/export/__GAME_ID__'>Export Studio</a></div></div></nav><main class='wrap'><section class='hero'><div class='eyebrow'>HDMI capture card display</div><h1>Play __GAME_TITLE__ alongside your console.</h1><p class='muted'>Connect your console HDMI output to a compatible USB/Thunderbolt capture device, then let your browser use that capture device as a camera source. Video stays in your browser unless you deliberately use another recording or publishing feature.</p></section><section class='grid'><aside><div class='card'><div class='row'><div class='eyebrow'>Connection</div><div id='status' class='status'><span class='dot'></span><span id='statusText'>Not connected</span></div></div><label>Video capture device</label><select id='videoDevice'><option value=''>Select after permission…</option></select><label>Capture audio device</label><select id='audioDevice'><option value=''>None / embedded audio if available</option></select><div class='actions'><button id='connect' class='primary'>Connect capture card</button><button id='refresh'>Refresh devices</button><button id='disconnect' class='danger' disabled>Disconnect</button></div></div><div class='card'><div class='eyebrow'>Display</div><div class='actions'><button id='fullscreen' disabled>Fullscreen</button><button id='pip' disabled>Picture in Picture</button><button id='popout' disabled>Pop-out display</button></div><label style='display:block;margin-top:12px'><input id='audioMonitor' type='checkbox'> Monitor capture audio on this device</label><p class='muted'>Audio monitoring starts off to reduce feedback. For lowest latency, use the capture card/console audio path where possible.</p></div><div class='card'><div class='eyebrow'>Signal</div><div class='kv'><span class='muted'>Resolution</span><b id='resolution'>—</b></div><div class='kv'><span class='muted'>Frame rate</span><b id='fps'>—</b></div><div class='kv'><span class='muted'>Video input</span><b id='videoLabel'>—</b></div><div class='kv'><span class='muted'>Audio input</span><b id='audioLabel'>—</b></div></div><div class='card warning'><div class='eyebrow'>Important</div><p class='muted'>A normal HDMI port on a computer is usually output-only. This feature needs a real capture card/device that appears to the operating system as a video input. The Command Center cannot turn an HDMI-output port into an HDMI-input port.</p><p class='muted'>The browser displays the console feed; it does not send controller commands back through HDMI. Keep your controller connected to the console unless your hardware provides a separate supported input path.</p></div></aside><div><div id='screen' class='screen'><video id='preview' playsinline autoplay muted></video><div id='placeholder' class='placeholder'><div><h2>Waiting for capture card</h2><p>Choose Connect capture card, grant camera permission, then select the capture device. Aura will confirm when a live video track is received.</p></div></div></div><div class='card'><div class='eyebrow'>Privacy & performance</div><p class='muted'>The capture stream is attached directly to this browser tab using MediaDevices. This page does not upload or persist the console feed on the Command Center server. Device identifiers are not sent to the server by this capture page.</p><p class='muted'>Actual latency, resolution, HDR, colour format and frame rate depend on the capture hardware, USB/Thunderbolt bandwidth, console settings, operating system and browser. The page requests a low-latency high-resolution feed but does not make unsupported hardware claims.</p></div></div></section></main><script>
'use strict';
const GAME_ID=__GAME_JSON__;
const $=id=>document.getElementById(id);
let stream=null,pop=null;
const status=(text,kind='')=>{const e=$('status');e.className='status '+kind;$('statusText').textContent=text};
function stopStream(){if(stream){for(const t of stream.getTracks())t.stop()}stream=null;$('preview').srcObject=null;$('placeholder').classList.remove('hide');status('Not connected');for(const id of ['disconnect','fullscreen','pip','popout'])$(id).disabled=id==='disconnect'?true:true;$('resolution').textContent='—';$('fps').textContent='—';$('videoLabel').textContent='—';$('audioLabel').textContent='—';if(pop&&!pop.closed)pop.close();pop=null}
async function enumerate(){if(!navigator.mediaDevices?.enumerateDevices)throw new Error('This browser does not expose media capture devices.');const rows=await navigator.mediaDevices.enumerateDevices();const oldV=$('videoDevice').value,oldA=$('audioDevice').value;const vids=rows.filter(d=>d.kind==='videoinput'),auds=rows.filter(d=>d.kind==='audioinput');$('videoDevice').innerHTML='<option value="">Select video input…</option>'+vids.map((d,i)=>`<option value="${d.deviceId}">${escapeHtml(d.label||`Video input ${i+1}`)}</option>`).join('');$('audioDevice').innerHTML='<option value="">No separate audio input</option>'+auds.map((d,i)=>`<option value="${d.deviceId}">${escapeHtml(d.label||`Audio input ${i+1}`)}</option>`).join('');if(vids.some(d=>d.deviceId===oldV))$('videoDevice').value=oldV;if(auds.some(d=>d.deviceId===oldA))$('audioDevice').value=oldA}
function escapeHtml(v){return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function requestPermission(){const probe=await navigator.mediaDevices.getUserMedia({video:true,audio:false});for(const t of probe.getTracks())t.stop();await enumerate()}
async function connect(){try{if(!window.isSecureContext)throw new Error('Capture devices require HTTPS or localhost.');if(!navigator.mediaDevices?.getUserMedia)throw new Error('Media capture is not supported in this browser.');$('connect').disabled=true;status('Requesting permission…');if(!$('videoDevice').options.length||!$('videoDevice').options[0].nextElementSibling)await requestPermission();const videoId=$('videoDevice').value;if(!videoId){await enumerate();throw new Error('Select the capture card video input, then connect again.')}if(stream)stopStream();const audioId=$('audioDevice').value;const constraints={video:{deviceId:{exact:videoId},width:{ideal:1920},height:{ideal:1080},frameRate:{ideal:60,max:120}},audio:audioId?{deviceId:{exact:audioId},echoCancellation:false,noiseSuppression:false,autoGainControl:false}:false};stream=await navigator.mediaDevices.getUserMedia(constraints);const videoTrack=stream.getVideoTracks()[0];if(!videoTrack||videoTrack.readyState!=='live')throw new Error('The selected device did not provide a live video track.');$('preview').srcObject=stream;$('preview').muted=!$('audioMonitor').checked;await $('preview').play();await new Promise(resolve=>{if($('preview').readyState>=1)return resolve();$('preview').addEventListener('loadedmetadata',resolve,{once:true})});const settings=videoTrack.getSettings();$('resolution').textContent=`${settings.width||$('preview').videoWidth||'?'} × ${settings.height||$('preview').videoHeight||'?'}`;$('fps').textContent=settings.frameRate?`${Math.round(settings.frameRate)} fps`:'Browser did not report';$('videoLabel').textContent=videoTrack.label||'Capture video';const at=stream.getAudioTracks()[0];$('audioLabel').textContent=at?.label||'No audio track';$('placeholder').classList.add('hide');status('Live signal confirmed','connected');$('disconnect').disabled=false;$('fullscreen').disabled=false;$('pip').disabled=!document.pictureInPictureEnabled;$('popout').disabled=false;videoTrack.addEventListener('ended',()=>{status('Capture device disconnected','error');stopStream()},{once:true});await enumerate()}catch(e){status(e?.message||'Capture connection failed','error');if(stream)stopStream()}finally{$('connect').disabled=false}}
$('connect').addEventListener('click',connect);$('refresh').addEventListener('click',async()=>{try{await requestPermission();status('Devices refreshed')}catch(e){status(e?.message||'Unable to read devices','error')}});$('disconnect').addEventListener('click',stopStream);$('audioMonitor').addEventListener('change',()=>{$('preview').muted=!$('audioMonitor').checked;if(pop&&!pop.closed){const v=pop.document.getElementById('capture');if(v)v.muted=!$('audioMonitor').checked}});$('fullscreen').addEventListener('click',async()=>{try{await $('screen').requestFullscreen()}catch(e){status('Fullscreen was blocked by the browser','error')}});$('pip').addEventListener('click',async()=>{try{if(document.pictureInPictureElement)await document.exitPictureInPicture();else await $('preview').requestPictureInPicture()}catch(e){status('Picture in Picture is unavailable','error')}});$('popout').addEventListener('click',()=>{if(!stream)return;pop=window.open('','AuraGameForgeCapture','popup,width=1280,height=760');if(!pop){status('Pop-out was blocked by the browser','error');return}pop.document.open();pop.document.write(`<!doctype html><html><head><title>Console Capture · ${escapeHtml('__GAME_TITLE__')}</title><style>html,body{margin:0;background:#000;width:100%;height:100%;overflow:hidden}video{width:100%;height:100%;object-fit:contain;background:#000}</style></head><body><video id="capture" autoplay playsinline ${$('audioMonitor').checked?'':'muted'}></video></body></html>`);pop.document.close();const v=pop.document.getElementById('capture');v.srcObject=stream;v.muted=!$('audioMonitor').checked;v.play().catch(()=>{});pop.focus()});if(navigator.mediaDevices?.addEventListener)navigator.mediaDevices.addEventListener('devicechange',()=>enumerate().catch(()=>{}));window.addEventListener('beforeunload',stopStream);enumerate().catch(()=>{});
</script></body></html>"""
    return HTMLResponse(
        page.replace("__BRAND__", brand)
        .replace("__GAME_TITLE__", title)
        .replace("__GAME_ID__", escape(game.id, quote=True))
        .replace("__GAME_JSON__", game_json),
        headers={"Cache-Control": "private, no-store", "Permissions-Policy": "camera=(self), microphone=(self)"},
    )


__all__ = ["router"]
