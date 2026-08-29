from __future__ import annotations

from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .branding import PRODUCT_FULL_NAME

router = APIRouter(tags=["Aura LIVE Setup Wizard"])


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


@router.get("/live-overlays", include_in_schema=False)
def live_overlays_alias():
    return RedirectResponse("/live-overlay-studio/setup", status_code=308)


@router.get("/api/live-overlays/setup-checklist")
def setup_checklist(request: Request):
    member = _member(request)
    return JSONResponse(
        {
            "tier": member.plan.id,
            "steps": [
                {"id": "sound", "title": "Choose LIVE audio", "required": True},
                {"id": "voice", "title": "Choose welcome / TTS voice", "required": False},
                {"id": "scene", "title": "Design your overlay scene", "required": True},
                {"id": "source", "title": "Add one Link / Browser Source", "required": True},
                {"id": "test", "title": "Run synthetic LIVE tests", "required": True},
                {"id": "relay", "title": "Connect an approved LIVE event relay", "required": False},
            ],
            "native_tiktok_audio_control": False,
            "note": "Mute TikTok gift alert sounds controls Aura overlay gift SFX, not undocumented native TikTok LIVE Studio application audio.",
        },
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/live-overlay-studio/setup", response_class=HTMLResponse, include_in_schema=False)
def setup_wizard(request: Request):
    member = _member(request)
    plan = escape(member.plan.name)
    product = escape(PRODUCT_FULL_NAME)
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Aura LIVE Easy Setup</title><style>
:root{{--bg:#080a13;--card:#121827;--line:#ffffff1f;--muted:#aeb8cd;--gold:#efc96b;--violet:#9b72ff}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 10% 0,#28184b55,transparent 35%),var(--bg);color:#fff;font-family:Inter,system-ui,sans-serif}}main{{max-width:1040px;margin:auto;padding:32px 18px 70px}}header{{display:flex;justify-content:space-between;gap:15px;align-items:center;margin-bottom:28px}}h1{{font-size:clamp(2rem,6vw,3.8rem);line-height:.98;margin:18px 0}}.muted{{color:var(--muted)}}.badge{{display:inline-block;border:1px solid #ffffff28;background:#ffffff0c;border-radius:999px;padding:6px 10px}}.step{{display:grid;grid-template-columns:48px 1fr;gap:15px;background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;margin:13px 0}}.n{{width:42px;height:42px;display:grid;place-items:center;border-radius:50%;font-weight:900;background:linear-gradient(135deg,var(--gold),var(--violet));color:#10101a}}button,a.btn,select,input{{font:inherit}}button,a.btn{{display:inline-block;border:0;border-radius:10px;padding:11px 15px;background:linear-gradient(120deg,var(--gold),var(--violet));color:#10101a;font-weight:900;text-decoration:none;cursor:pointer;margin:4px 5px 4px 0}}button.secondary,a.secondary{{background:#181f31;color:#fff;border:1px solid #ffffff25}}.switches{{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}}code{{display:block;word-break:break-all;color:var(--gold);background:#05070d;padding:12px;border-radius:10px;margin:8px 0}}.status{{font-size:.9rem;color:var(--muted);min-height:1.3em}}.good{{color:#91efaa}}.warn{{color:#ffd179}}@media(max-width:650px){{header{{align-items:flex-start;flex-direction:column}}.step{{grid-template-columns:1fr}}}}
</style></head><body><main><header><div><b>{product}</b><div class='muted'>Aura LIVE Overlay Studio · easiest setup</div></div><span class='badge'>{plan} tier</span></header><h1>Go from zero to LIVE overlay in five simple steps.</h1><p class='muted'>No scripts. No manual coding. Design it visually, choose how Aura sounds, paste one private source URL into TikTok LIVE Studio or OBS, and test before you go live.</p>
<section class='step'><div class='n'>1</div><div><h2>Choose what your LIVE can hear</h2><p class='muted'>Gift visuals stay active even when sound effects are muted. Gift TTS is independent.</p><div class='switches'><button id='giftMute' class='secondary'>Mute TikTok gift alert sounds</button><button id='giftTts' class='secondary'>Toggle gift TTS</button><button id='allMute' class='secondary'>Mute all overlay audio</button></div><p id='audioStatus' class='status'></p></div></section>
<section class='step'><div class='n'>2</div><div><h2>Pick Aura's voice</h2><p class='muted'>Browser voice works without a server TTS provider. Basic/Pro can use configured Aura speech. Pro can use a consent-approved cloned voice profile when your existing voice rights gate and provider are configured.</p><select id='voice'><option value='browser'>Browser / device voice</option><option value='aura'>Aura AI voice</option><option value='clone'>Consent-approved cloned voice</option></select><button id='saveVoice'>Use this voice</button><p id='voiceStatus' class='status'></p></div></section>
<section class='step'><div class='n'>3</div><div><h2>Design the screen</h2><p class='muted'>Drag alerts, goals, chat/event boxes, leaderboards, ticker, battle/poll cards, camera frames, captions, supporter spotlight and more onto a vertical canvas.</p><a class='btn' href='/live-overlay-studio/editor'>Open visual designer</a><a class='btn secondary' href='/live-overlay-studio/automations'>Build automations</a></div></section>
<section class='step'><div class='n'>4</div><div><h2>Create one private source URL</h2><p class='muted'>Use the advanced source for your designed scene, reactive particles, goals, leaderboard, ticker and safe automation actions. Rotating it invalidates the previous URL immediately.</p><button id='source'>Generate / rotate source</button><code id='sourceUrl'>Your private source URL will appear here.</code><p class='status warn'>Treat this URL like a password. Do not post it publicly.</p></div></section>
<section class='step'><div class='n'>5</div><div><h2>Test it before LIVE</h2><p class='muted'>Send synthetic joins, gifts, likes and comments through the same event engine without pretending they came from TikTok.</p><div class='switches'><button data-test='viewer_joined' class='secondary'>Test welcome</button><button data-test='gift' class='secondary'>Test gift</button><button data-test='like_milestone' class='secondary'>Test likes</button><button data-test='comment' class='secondary'>Test comment TTS</button></div><p id='testStatus' class='status'></p></div></section>
<section class='step'><div class='n'>+</div><div><h2>Optional: connect real LIVE events</h2><p class='muted'>Aura provides a secure normalized relay endpoint with replay protection and rate limiting. It does not pretend TikTok is directly connected. Only connect a maintainable, policy-compliant and ESP-approved event source.</p><button id='relay' class='secondary'>Generate / rotate relay URL</button><code id='relayUrl'>No relay URL revealed yet.</code><p id='relayStatus' class='status'></p></div></section>
<p><a class='btn secondary' href='/live-overlay-studio'>Advanced control room</a> <a class='btn secondary' href='/live-overlay-studio/advanced-source'>Advanced source page</a></p></main><script>
let P={{}};const $=id=>document.getElementById(id);async function load(){{let r=await fetch('/api/live-overlay/profile'),d=await r.json();P=d.profile||{{}};$('voice').value=P.voice_mode||'browser';audio()}}function audio(){{$('giftMute').textContent=P.gift_sound_muted?'Unmute Aura gift sounds':'Mute TikTok gift alert sounds';$('giftTts').textContent=P.tts_gifts_enabled?'Turn gift TTS off':'Turn gift TTS on';$('allMute').textContent=P.all_audio_muted?'Unmute all overlay audio':'Mute all overlay audio';$('audioStatus').textContent='Aura gift SFX: '+(P.gift_sound_muted?'muted':'on')+' · Gift TTS: '+(P.tts_gifts_enabled?'on':'off')+' · All overlay audio: '+(P.all_audio_muted?'muted':'available')}}async function save(body){{let r=await fetch('/api/live-overlay/profile',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify(body)}}),d=await r.json();if(!r.ok)throw new Error(d.detail||'Unable to save');P=d.profile;audio();return d}}$('giftMute').onclick=()=>save({{gift_sound_muted:!P.gift_sound_muted}}).catch(e=>alert(e.message));$('giftTts').onclick=()=>save({{tts_gifts_enabled:!P.tts_gifts_enabled}}).catch(e=>alert(e.message));$('allMute').onclick=()=>save({{all_audio_muted:!P.all_audio_muted}}).catch(e=>alert(e.message));$('saveVoice').onclick=async()=>{{try{{await save({{voice_mode:$('voice').value}});$('voiceStatus').textContent='Voice saved.'}}catch(e){{$('voiceStatus').textContent=e.message}}}};$('source').onclick=async()=>{{let r=await fetch('/api/live-overlays/advanced-source/rotate',{{method:'POST'}}),d=await r.json();$('sourceUrl').textContent=r.ok?d.source_url:(d.detail||'Unable to create source')}};document.querySelectorAll('[data-test]').forEach(b=>b.onclick=async()=>{{let type=b.dataset.test,p={{username:'Aura Test Viewer',gift_name:'Rose',gift_count:1,coins:1,message:'Hello Aura!',likes:500,progress:.5,target:1000,is_follower:true}};let r=await fetch('/api/live-overlays/simulate',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{event_type:type,payload:p}})}});$('testStatus').textContent=r.ok?'Synthetic '+type.replaceAll('_',' ')+' queued. Watch your source.':'Test failed.'}});$('relay').onclick=async()=>{{let r=await fetch('/api/live-overlays/connector/rotate',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{label:'Aura LIVE approved relay'}})}}),d=await r.json();$('relayUrl').textContent=r.ok?d.ingest_url:(d.detail||'Unable to create relay');$('relayStatus').textContent=r.ok?'Relay URL returned once. Store it securely in your approved event relay.':''}};load();
</script></body></html>""",
        headers={"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow"},
    )
