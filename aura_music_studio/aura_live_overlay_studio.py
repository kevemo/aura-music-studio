from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import tempfile
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import quote

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .branding import PRODUCT_FULL_NAME
from .plans import APPROVED_VOICE_DUPLICATION
from .speech import AuraSpeechService

router = APIRouter(tags=["Aura LIVE Overlay Studio"])

DB_PATH = Path(os.getenv("AURA_LIVE_OVERLAY_DB", "data/aura_live_overlay.sqlite3"))
EVENT_LIMIT = 1000
SOURCE_TOKEN_BYTES = 32
ALLOWED_EVENT_TYPES = {
    "viewer_joined", "follow", "subscribe", "gift", "share", "like", "like_milestone",
    "comment", "battle_start", "battle_progress", "battle_end", "poll", "treasure_chest",
    "question", "pinned_message", "live_shopping", "intro", "custom",
}

TIER_MATRIX = {
    "free": {
        "name": "Free",
        "max_rules": 5,
        "custom_sounds": False,
        "aura_voice": False,
        "voice_clone": False,
        "advanced_widgets": False,
        "automation": False,
    },
    "base": {
        "name": "Basic",
        "max_rules": 30,
        "custom_sounds": True,
        "aura_voice": True,
        "voice_clone": False,
        "advanced_widgets": True,
        "automation": True,
    },
    "pro": {
        "name": "Pro",
        "max_rules": None,
        "custom_sounds": True,
        "aura_voice": True,
        "voice_clone": True,
        "advanced_widgets": True,
        "automation": True,
    },
}

WIDGET_CATALOG = [
    ("alert_box", "Alert Box", "free"),
    ("welcome", "Welcome / VIP arrival", "free"),
    ("gift_feed", "Gift feed", "free"),
    ("chat_box", "Chat box", "free"),
    ("event_list", "Recent events", "free"),
    ("like_goal", "Like goal", "free"),
    ("follower_goal", "Follower goal", "free"),
    ("gift_goal", "Gift / coin goal", "base"),
    ("subscriber_goal", "Subscriber goal", "base"),
    ("share_goal", "Share goal", "base"),
    ("custom_goal", "Custom goal", "base"),
    ("top_gifters", "Top gifters leaderboard", "base"),
    ("top_likers", "Top likers leaderboard", "base"),
    ("last_supporter", "Last gifter / follower / liker / subscriber", "base"),
    ("viewer_count", "Viewer count", "base"),
    ("gift_combo", "Gift combo / streak", "base"),
    ("countdown", "Countdown / subathon timer", "base"),
    ("spin_wheel", "Spin wheel", "base"),
    ("supporter_spotlight", "Supporter spotlight", "base"),
    ("battle", "LIVE Match / battle board", "pro"),
    ("poll", "Poll card", "pro"),
    ("pinned_message", "Pinned message", "pro"),
    ("shopping", "LIVE shopping card", "pro"),
    ("lower_third", "Lower thirds / CTA", "pro"),
    ("social_rotator", "Social handle rotator", "pro"),
    ("camera_frame", "Camera frame / reactive border", "pro"),
    ("captions", "Live caption layer", "pro"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _init_schema() -> None:
    with _connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_overlay_profiles (
                user_id TEXT PRIMARY KEY,
                source_token_hash TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT 'My LIVE Overlay',
                orientation TEXT NOT NULL DEFAULT 'portrait',
                theme TEXT NOT NULL DEFAULT 'cosmic',
                gift_sound_muted INTEGER NOT NULL DEFAULT 0,
                all_audio_muted INTEGER NOT NULL DEFAULT 0,
                tts_chat_enabled INTEGER NOT NULL DEFAULT 0,
                tts_gifts_enabled INTEGER NOT NULL DEFAULT 1,
                welcome_enabled INTEGER NOT NULL DEFAULT 1,
                welcome_once INTEGER NOT NULL DEFAULT 1,
                welcome_audience TEXT NOT NULL DEFAULT 'followers',
                welcome_template TEXT NOT NULL DEFAULT 'Welcome {username}! Great to have you here.',
                gift_template TEXT NOT NULL DEFAULT 'Thank you {username} for the {gift_name}!',
                min_gift_coins INTEGER NOT NULL DEFAULT 1,
                voice_mode TEXT NOT NULL DEFAULT 'browser',
                voice_profile_id TEXT,
                tts_volume REAL NOT NULL DEFAULT 0.85,
                user_cooldown_seconds INTEGER NOT NULL DEFAULT 8,
                max_tts_queue INTEGER NOT NULL DEFAULT 6,
                max_tts_chars INTEGER NOT NULL DEFAULT 220,
                profanity_filter INTEGER NOT NULL DEFAULT 1,
                active_widgets_json TEXT NOT NULL DEFAULT '["alert_box","welcome","gift_feed","like_goal"]',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS live_overlay_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_live_overlay_events_user_id_id
                ON live_overlay_events(user_id, id);
            """
        )


_init_schema()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _tier(plan_id: str) -> dict:
    return dict(TIER_MATRIX.get((plan_id or "").lower(), TIER_MATRIX["free"]))


def _profile(user_id: str) -> dict:
    with _connect() as con:
        row = con.execute("SELECT * FROM live_overlay_profiles WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        raise KeyError(user_id)
    out = dict(row)
    out["gift_sound_muted"] = bool(out["gift_sound_muted"])
    out["all_audio_muted"] = bool(out["all_audio_muted"])
    out["tts_chat_enabled"] = bool(out["tts_chat_enabled"])
    out["tts_gifts_enabled"] = bool(out["tts_gifts_enabled"])
    out["welcome_enabled"] = bool(out["welcome_enabled"])
    out["welcome_once"] = bool(out["welcome_once"])
    out["profanity_filter"] = bool(out["profanity_filter"])
    out["active_widgets"] = json.loads(out.pop("active_widgets_json"))
    out.pop("source_token_hash", None)
    return out


def _ensure_profile(user_id: str) -> tuple[dict, str | None]:
    try:
        return _profile(user_id), None
    except KeyError:
        raw = secrets.token_urlsafe(SOURCE_TOKEN_BYTES)
        with _connect() as con:
            con.execute(
                "INSERT INTO live_overlay_profiles(user_id,source_token_hash,updated_at) VALUES(?,?,?)",
                (user_id, _hash_token(raw), _now()),
            )
        return _profile(user_id), raw


def _user_for_source(token: str) -> str:
    digest = _hash_token(token)
    with _connect() as con:
        row = con.execute("SELECT user_id FROM live_overlay_profiles WHERE source_token_hash=?", (digest,)).fetchone()
    if not row:
        raise HTTPException(404, "Overlay source not found")
    return str(row["user_id"])


def _redact_payload(payload: dict) -> dict:
    allowed = {
        "username", "display_name", "gift_name", "gift_id", "gift_count", "coins", "diamonds",
        "message", "likes", "viewer_count", "followers", "subscribers", "shares", "team", "result",
        "progress", "target", "avatar_url", "is_follower", "is_subscriber", "is_moderator", "is_team",
        "is_top_gifter", "title", "label",
    }
    out = {}
    for key, value in payload.items():
        if key not in allowed:
            continue
        if isinstance(value, str):
            out[key] = value[:500]
        elif isinstance(value, (int, float, bool)) or value is None:
            out[key] = value
    return out


class OverlayProfileUpdate(BaseModel):
    gift_sound_muted: bool | None = None
    all_audio_muted: bool | None = None
    tts_chat_enabled: bool | None = None
    tts_gifts_enabled: bool | None = None
    welcome_enabled: bool | None = None
    welcome_once: bool | None = None
    welcome_audience: str | None = Field(default=None, max_length=32)
    welcome_template: str | None = Field(default=None, min_length=1, max_length=300)
    gift_template: str | None = Field(default=None, min_length=1, max_length=300)
    min_gift_coins: int | None = Field(default=None, ge=0, le=1000000)
    voice_mode: str | None = Field(default=None, max_length=16)
    voice_profile_id: str | None = Field(default=None, max_length=120)
    tts_volume: float | None = Field(default=None, ge=0, le=1)
    user_cooldown_seconds: int | None = Field(default=None, ge=0, le=300)
    max_tts_queue: int | None = Field(default=None, ge=1, le=50)
    max_tts_chars: int | None = Field(default=None, ge=20, le=500)
    profanity_filter: bool | None = None
    active_widgets: list[str] | None = Field(default=None, max_length=40)


class TestEvent(BaseModel):
    event_type: str
    username: str = Field(default="Aura Test Viewer", max_length=80)
    gift_name: str = Field(default="Rose", max_length=80)
    coins: int = Field(default=1, ge=0, le=1000000)
    message: str = Field(default="This is an Aura LIVE Overlay Studio test.", max_length=500)


def _widget_allowed(widget: str, plan_id: str) -> bool:
    row = next((r for r in WIDGET_CATALOG if r[0] == widget), None)
    if not row:
        return False
    required = row[2]
    order = {"free": 0, "base": 1, "pro": 2}
    return order.get(plan_id, 0) >= order[required]


@router.get("/live-overlay-studio", response_class=HTMLResponse, include_in_schema=False)
def live_overlay_studio(request: Request):
    member = _member(request)
    profile, fresh_token = _ensure_profile(member.user_id)
    tier = _tier(member.plan.id)
    source_hint = fresh_token or "ROTATE_TO_REVEAL_A_NEW_PRIVATE_URL"
    widgets = "".join(
        f"<span class='chip {'locked' if not _widget_allowed(key, member.plan.id) else ''}'>{escape(name)}</span>"
        for key, name, _ in WIDGET_CATALOG
    )
    page = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Aura LIVE Overlay Studio — {escape(PRODUCT_FULL_NAME)}</title><style>
body{{margin:0;background:#070812;color:#fff;font-family:Inter,system-ui,sans-serif}}.wrap{{width:min(1180px,calc(100% - 28px));margin:auto}}.hero{{padding:40px 0 20px}}h1{{font-size:clamp(2.5rem,6vw,5rem);margin:.1em 0}}.grid{{display:grid;grid-template-columns:1.1fr .9fr;gap:16px}}.card{{background:#111526;border:1px solid #ffffff20;border-radius:18px;padding:18px;margin-bottom:14px}}.row{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}button,.btn,select,input{{font:inherit;border-radius:10px;border:1px solid #ffffff26;background:#ffffff0d;color:#fff;padding:10px 12px}}button,.btn{{cursor:pointer;font-weight:850}}.primary{{background:linear-gradient(120deg,#efc96b,#9b72ff);color:#160b22;border:0}}.danger{{border-color:#ff8ca6;color:#ffdce4}}.muted{{color:#bdc6d8;line-height:1.5}}.chip{{display:inline-block;padding:7px 9px;margin:4px;border-radius:999px;background:#ffffff0c;border:1px solid #ffffff18;font-size:.8rem}}.locked{{opacity:.45}}label{{display:block;margin:10px 0 4px;font-weight:750}}input[type=checkbox]{{width:auto}}.toggle{{display:flex;justify-content:space-between;gap:10px;align-items:center;padding:11px 0;border-bottom:1px solid #ffffff12}}code{{word-break:break-all;color:#efc96b}}@media(max-width:800px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><main class='wrap'><section class='hero'><div style='color:#efc96b;font-weight:900'>Powered by Aura AI · LIVE Creator Tools</div><h1>Aura LIVE Overlay Studio</h1><p class='muted'>One control room and one private browser/link source for TikTok LIVE Studio, OBS and compatible broadcast software. Build alerts, goals, leaderboards, speech welcomes and interactive scenes without coding.</p></section><section class='grid'><div><div class='card'><h2>Live audio controls</h2><div class='toggle'><div><b>Mute TikTok gift alert sounds</b><div class='muted'>Keeps gift visuals active while muting Aura's gift sound effects, so gift sounds do not interrupt your conversation. Gift TTS remains a separate control.</div></div><button id='giftMute' class='danger'>{'Unmute gift sounds' if profile['gift_sound_muted'] else 'Mute gift sounds'}</button></div><div class='toggle'><div><b>Mute all overlay audio</b><div class='muted'>Emergency silence for sound effects and Aura/TTS.</div></div><button id='allMute'>{'Unmute all' if profile['all_audio_muted'] else 'Mute all'}</button></div><div class='toggle'><label><input id='giftTts' type='checkbox' {'checked' if profile['tts_gifts_enabled'] else ''}> Speak gift thank-yous</label><label><input id='chatTts' type='checkbox' {'checked' if profile['tts_chat_enabled'] else ''}> Read allowed chat</label></div></div><div class='card'><h2>Welcome people naturally</h2><label><input id='welcomeEnabled' type='checkbox' {'checked' if profile['welcome_enabled'] else ''}> Spoken welcome enabled</label><label>Who gets welcomed</label><select id='welcomeAudience'><option value='followers'>Followers + supporters</option><option value='subscribers'>Subscribers</option><option value='vip'>Subscribers, moderators, team & top gifters</option><option value='all'>Everyone (not recommended on busy lives)</option></select><label>Welcome line</label><input id='welcomeTemplate' style='width:100%' value='{escape(profile['welcome_template'], quote=True)}'><p class='muted'>Aura can say the viewer name once per session. Busy-room filters and cooldowns prevent a constant wall of speech.</p></div><div class='card'><h2>Voice</h2><label>Speech engine</label><select id='voiceMode'><option value='browser'>Standard device voice</option><option value='aura' {'disabled' if not tier['aura_voice'] else ''}>Aura voice (Basic/Pro)</option><option value='clone' {'disabled' if not tier['voice_clone'] else ''}>Consent-approved cloned voice (Pro)</option></select><p class='muted'>Voice cloning is never enabled merely by typing a person's name. Pro clone mode still requires an approved voice profile and a configured synthesis provider.</p></div><div class='card'><h2>Widgets</h2>{widgets}<p class='muted'>Locked chips show widgets available on higher tiers. The runtime uses the same event stream so adding a widget does not require reconnecting the LIVE.</p></div></div><aside><div class='card'><h2>Fast setup</h2><ol class='muted'><li>Create or rotate your private source URL.</li><li>In TikTok LIVE Studio choose <b>Add source → Link</b>; in OBS/Streamlabs choose <b>Browser Source</b>.</li><li>Paste the URL, size it to your scene, then use the event simulator before going LIVE.</li></ol><button id='rotate' class='primary'>Generate new private source URL</button><p id='source'><code>{escape(source_hint)}</code></p><p class='muted'>The URL is a bearer secret. Rotating it immediately invalidates the previous source.</p><hr style='border:0;border-top:1px solid #ffffff18;margin:18px 0'><h3>Real LIVE event relay</h3><p class='muted'>Configure an ESP-approved normalized provider adapter to feed documented LIVE events into your overlay. Provider access remains external until separately authorized and configured; this does not claim a direct TikTok connection.</p><a class='btn' href='/live-overlay-studio/connector'>Configure LIVE Event Relay</a></div><div class='card'><h2>Test before LIVE</h2><div class='row'><button data-test='gift'>Gift</button><button data-test='viewer_joined'>Welcome</button><button data-test='follow'>Follow</button><button data-test='subscribe'>Subscribe</button><button data-test='like_milestone'>Like goal</button></div><p id='testStatus' class='muted'>Tests use synthetic events and are clearly separated from real LIVE data.</p></div><div class='card'><h2>Your tier</h2><p><b>{escape(tier['name'])}</b></p><p class='muted'>Alert rules: {'Unlimited' if tier['max_rules'] is None else tier['max_rules']}. Custom sounds: {'Yes' if tier['custom_sounds'] else 'Upgrade required'}. Advanced widgets: {'Yes' if tier['advanced_widgets'] else 'Upgrade required'}.</p></div></aside></section></main><script>
const P={json.dumps(profile)}; const $=x=>document.getElementById(x); $('welcomeAudience').value=P.welcome_audience; $('voiceMode').value=P.voice_mode;
async function save(p){{const r=await fetch('/api/live-overlay/profile',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify(p)}});if(!r.ok)alert((await r.json()).detail||'Unable to save');return r}}
$('giftMute').onclick=async()=>{{const v=!P.gift_sound_muted; if((await save({{gift_sound_muted:v}})).ok){{P.gift_sound_muted=v;$('giftMute').textContent=v?'Unmute gift sounds':'Mute gift sounds'}}}};
$('allMute').onclick=async()=>{{const v=!P.all_audio_muted;if((await save({{all_audio_muted:v}})).ok){{P.all_audio_muted=v;$('allMute').textContent=v?'Unmute all':'Mute all'}}}};
$('giftTts').onchange=e=>save({{tts_gifts_enabled:e.target.checked}});$('chatTts').onchange=e=>save({{tts_chat_enabled:e.target.checked}});$('welcomeEnabled').onchange=e=>save({{welcome_enabled:e.target.checked}});$('welcomeAudience').onchange=e=>save({{welcome_audience:e.target.value}});$('welcomeTemplate').onchange=e=>save({{welcome_template:e.target.value}});$('voiceMode').onchange=e=>save({{voice_mode:e.target.value}});
$('rotate').onclick=async()=>{{const r=await fetch('/api/live-overlay/rotate-source',{{method:'POST'}});const d=await r.json();if(r.ok)$('source').innerHTML='<code>'+d.source_url.replaceAll('&','&amp;').replaceAll('<','&lt;')+'</code>';else alert(d.detail||'Unable to rotate')}};
document.querySelectorAll('[data-test]').forEach(b=>b.onclick=async()=>{{const type=b.dataset.test;const r=await fetch('/api/live-overlay/test-event',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{event_type:type}})}});$('testStatus').textContent=r.ok?'Test queued. Watch your browser source.':'Test failed';}});
</script></body></html>"""
    return HTMLResponse(page, headers={"Cache-Control": "private, no-store"})


@router.get("/api/live-overlay/profile")
def get_overlay_profile(request: Request):
    member = _member(request)
    profile, _ = _ensure_profile(member.user_id)
    return {"profile": profile, "tier": _tier(member.plan.id), "widgets": WIDGET_CATALOG}


@router.post("/api/live-overlay/profile")
def update_overlay_profile(request: Request, update: OverlayProfileUpdate):
    member = _member(request)
    _ensure_profile(member.user_id)
    values = update.model_dump(exclude_none=True)
    voice_mode = values.get("voice_mode")
    if voice_mode and voice_mode not in {"browser", "aura", "clone"}:
        raise HTTPException(400, "Unsupported voice mode")
    tier = _tier(member.plan.id)
    if voice_mode == "aura" and not tier["aura_voice"]:
        raise HTTPException(403, "Aura overlay voice requires Basic or Pro")
    if voice_mode == "clone":
        if not tier["voice_clone"] or not member.plan.has(APPROVED_VOICE_DUPLICATION):
            raise HTTPException(403, "Consent-approved cloned LIVE voice requires Pro")
        if not (values.get("voice_profile_id") or _profile(member.user_id).get("voice_profile_id")):
            raise HTTPException(400, "Select an approved consent-backed voice profile before clone mode")
    if "welcome_audience" in values and values["welcome_audience"] not in {"all", "followers", "subscribers", "vip"}:
        raise HTTPException(400, "Unsupported welcome audience")
    if "active_widgets" in values:
        requested = list(dict.fromkeys(values["active_widgets"]))
        denied = [w for w in requested if not _widget_allowed(w, member.plan.id)]
        if denied:
            raise HTTPException(403, f"These widgets require a higher tier: {', '.join(denied[:8])}")
        values["active_widgets_json"] = json.dumps(requested)
        del values["active_widgets"]
    if not values:
        return {"profile": _profile(member.user_id)}
    bool_fields = {"gift_sound_muted", "all_audio_muted", "tts_chat_enabled", "tts_gifts_enabled", "welcome_enabled", "welcome_once", "profanity_filter"}
    for key in list(values):
        if key in bool_fields:
            values[key] = int(bool(values[key]))
    allowed_columns = {
        "gift_sound_muted", "all_audio_muted", "tts_chat_enabled", "tts_gifts_enabled", "welcome_enabled",
        "welcome_once", "welcome_audience", "welcome_template", "gift_template", "min_gift_coins", "voice_mode",
        "voice_profile_id", "tts_volume", "user_cooldown_seconds", "max_tts_queue", "max_tts_chars",
        "profanity_filter", "active_widgets_json",
    }
    values = {k: v for k, v in values.items() if k in allowed_columns}
    values["updated_at"] = _now()
    assignments = ",".join(f"{k}=?" for k in values)
    with _connect() as con:
        con.execute(f"UPDATE live_overlay_profiles SET {assignments} WHERE user_id=?", [*values.values(), member.user_id])
    return {"profile": _profile(member.user_id)}


@router.post("/api/live-overlay/rotate-source")
def rotate_overlay_source(request: Request):
    member = _member(request)
    _ensure_profile(member.user_id)
    raw = secrets.token_urlsafe(SOURCE_TOKEN_BYTES)
    with _connect() as con:
        con.execute(
            "UPDATE live_overlay_profiles SET source_token_hash=?,updated_at=? WHERE user_id=?",
            (_hash_token(raw), _now(), member.user_id),
        )
    base = str(request.base_url).rstrip("/")
    return {"source_url": f"{base}/live-overlay/source/{quote(raw, safe='')}"}


@router.post("/api/live-overlay/test-event")
def test_overlay_event(request: Request, event: TestEvent):
    member = _member(request)
    _ensure_profile(member.user_id)
    if event.event_type not in ALLOWED_EVENT_TYPES:
        raise HTTPException(400, "Unsupported LIVE event type")
    payload = {
        "username": event.username,
        "display_name": event.username,
        "gift_name": event.gift_name,
        "gift_count": 1,
        "coins": event.coins,
        "message": event.message,
        "likes": 500,
        "progress": 0.5,
        "target": 1000,
        "is_follower": True,
    }
    with _connect() as con:
        con.execute(
            "INSERT INTO live_overlay_events(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            (member.user_id, event.event_type, json.dumps(_redact_payload(payload)), _now()),
        )
        con.execute(
            "DELETE FROM live_overlay_events WHERE user_id=? AND id NOT IN (SELECT id FROM live_overlay_events WHERE user_id=? ORDER BY id DESC LIMIT ?)",
            (member.user_id, member.user_id, EVENT_LIMIT),
        )
    return {"queued": True, "synthetic": True}


@router.get("/live-overlay/source/{token}", response_class=HTMLResponse, include_in_schema=False)
def overlay_source(token: str):
    _user_for_source(token)
    token_json = json.dumps(token)
    page = r"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><style>html,body{margin:0;width:100%;height:100%;background:transparent;overflow:hidden;font-family:Inter,Arial,sans-serif;color:white}.alert{position:absolute;left:50%;top:16%;transform:translate(-50%,-20px) scale(.96);opacity:0;min-width:320px;max-width:82%;padding:18px 24px;border-radius:22px;background:linear-gradient(135deg,#180f2de8,#0c2634e8);border:1px solid #ffffff35;box-shadow:0 15px 55px #0009;text-align:center;transition:.35s}.alert.show{opacity:1;transform:translate(-50%,0) scale(1)}.alert b{font-size:1.35rem;color:#efc96b}.goal{position:absolute;bottom:7%;left:8%;right:8%;height:34px;border-radius:18px;background:#070812cc;border:1px solid #ffffff33;overflow:hidden}.fill{height:100%;width:0;background:linear-gradient(90deg,#efc96b,#9b72ff);transition:.5s}.goal span{position:absolute;inset:0;display:grid;place-items:center;font-weight:900;text-shadow:0 1px 5px #000}.giftfeed{position:absolute;right:4%;top:28%;width:min(320px,40%);display:grid;gap:7px}.gift{background:#090b16d9;border:1px solid #ffffff22;border-radius:14px;padding:10px 12px;animation:in .3s ease}.watermark{position:absolute;left:2%;bottom:2%;font-size:11px;opacity:.6}@keyframes in{from{opacity:0;transform:translateX(15px)}} </style></head><body><div id='alert' class='alert'></div><div id='giftfeed' class='giftfeed'></div><div id='goal' class='goal'><div id='fill' class='fill'></div><span id='goalText'>Aura LIVE Overlay Studio</span></div><div class='watermark'>Powered by Aura AI</div><script>
'use strict'; const TOKEN=__TOKEN__; let after=0, cfg={}, welcomed=new Set(), busy=false, speechQueue=[], lastSpoken=new Map(); const $=id=>document.getElementById(id);
const clean=v=>String(v??'').slice(0,500); function format(t,p){return clean(t).replaceAll('{username}',clean(p.username||p.display_name||'friend')).replaceAll('{gift_name}',clean(p.gift_name||'gift')).replaceAll('{gift_count}',clean(p.gift_count||1)).replaceAll('{coins}',clean(p.coins||0))}
async function config(){try{const r=await fetch('/live-overlay/source/'+encodeURIComponent(TOKEN)+'/config',{cache:'no-store'});if(r.ok)cfg=await r.json()}catch{}}
function allowedWelcome(p){if(cfg.welcome_audience==='all')return true;if(cfg.welcome_audience==='subscribers')return !!p.is_subscriber;if(cfg.welcome_audience==='vip')return !!(p.is_subscriber||p.is_moderator||p.is_team||p.is_top_gifter);return !!(p.is_follower||p.is_subscriber||p.is_moderator||p.is_team||p.is_top_gifter)}
function show(text){const e=$('alert');e.replaceChildren(document.createTextNode(clean(text)));e.classList.add('show');setTimeout(()=>e.classList.remove('show'),3500)}
function beep(){if(cfg.all_audio_muted||cfg.gift_sound_muted)return;try{const C=window.AudioContext||window.webkitAudioContext,a=new C(),o=a.createOscillator(),g=a.createGain();o.frequency.value=660;g.gain.setValueAtTime(.10*a.destination.maxChannelCount||.1,a.currentTime);g.gain.exponentialRampToValueAtTime(.001,a.currentTime+.32);o.connect(g);g.connect(a.destination);o.start();o.stop(a.currentTime+.34)}catch{}}
function queueSpeech(text){if(cfg.all_audio_muted||!text)return;const key=text.slice(0,80),now=Date.now(),wait=(cfg.user_cooldown_seconds||8)*1000;if(lastSpoken.has(key)&&now-lastSpoken.get(key)<wait)return;lastSpoken.set(key,now);if(speechQueue.length>=(cfg.max_tts_queue||6))return;speechQueue.push(clean(text).slice(0,cfg.max_tts_chars||220));drain()}
async function drain(){if(busy||!speechQueue.length)return;busy=true;const text=speechQueue.shift();try{if(cfg.voice_mode==='browser'||!cfg.voice_mode){await new Promise(res=>{const u=new SpeechSynthesisUtterance(text);u.volume=Number(cfg.tts_volume??.85);u.onend=u.onerror=res;speechSynthesis.speak(u)})}else{const r=await fetch('/live-overlay/source/'+encodeURIComponent(TOKEN)+'/speech',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text})});if(r.ok){const blob=await r.blob(),url=URL.createObjectURL(blob),a=new Audio(url);a.volume=Number(cfg.tts_volume??.85);await a.play();await new Promise(res=>{a.onended=a.onerror=res});URL.revokeObjectURL(url)}}}catch{}finally{busy=false;drain()}}
function giftFeed(p){const d=document.createElement('div');d.className='gift';d.textContent=`${clean(p.username||'Viewer')} · ${clean(p.gift_name||'Gift')} ×${clean(p.gift_count||1)}`;$('giftfeed').prepend(d);while($('giftfeed').children.length>5)$('giftfeed').lastChild.remove()}
function handle(e){const p=e.payload||{},type=e.event_type;if(type==='gift'){giftFeed(p);beep();show(`${p.username||'Viewer'} sent ${p.gift_name||'a gift'}!`);if(cfg.tts_gifts_enabled&&(Number(p.coins||0)>=Number(cfg.min_gift_coins||0)))queueSpeech(format(cfg.gift_template||'Thank you {username} for the {gift_name}!',p))}else if(type==='viewer_joined'&&cfg.welcome_enabled&&allowedWelcome(p)){const k=clean(p.username||p.display_name);if(!cfg.welcome_once||!welcomed.has(k)){welcomed.add(k);show(`Welcome ${k}!`);queueSpeech(format(cfg.welcome_template||'Welcome {username}!',p))}}else if(type==='comment'){if(cfg.tts_chat_enabled)queueSpeech(`${clean(p.username)} says ${clean(p.message)}`)}else if(type==='like_milestone'||type==='like'){const progress=Math.max(0,Math.min(1,Number(p.progress||0)));$('fill').style.width=(progress*100)+'%';$('goalText').textContent=`Likes ${clean(p.likes||'')} / ${clean(p.target||'')}`;if(type==='like_milestone')show('Like milestone reached!')}else{show(`${clean(p.username||'Someone')} · ${type.replaceAll('_',' ')}`)}}
async function poll(){try{const r=await fetch('/live-overlay/source/'+encodeURIComponent(TOKEN)+'/events?after='+after,{cache:'no-store'});if(r.ok){const d=await r.json();for(const e of d.events){after=Math.max(after,e.id);handle(e)}}}catch{}finally{setTimeout(poll,750)}} config().then(poll);setInterval(config,5000);
</script></body></html>""".replace("__TOKEN__", token_json)
    return HTMLResponse(page, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"})


@router.get("/live-overlay/source/{token}/config", include_in_schema=False)
def source_config(token: str):
    user_id = _user_for_source(token)
    profile = _profile(user_id)
    safe = {k: profile[k] for k in (
        "gift_sound_muted", "all_audio_muted", "tts_chat_enabled", "tts_gifts_enabled", "welcome_enabled",
        "welcome_once", "welcome_audience", "welcome_template", "gift_template", "min_gift_coins", "voice_mode",
        "tts_volume", "user_cooldown_seconds", "max_tts_queue", "max_tts_chars", "profanity_filter", "active_widgets",
    )}
    return JSONResponse(safe, headers={"Cache-Control": "no-store"})


@router.get("/live-overlay/source/{token}/events", include_in_schema=False)
def source_events(token: str, after: int = 0):
    user_id = _user_for_source(token)
    after = max(0, int(after))
    with _connect() as con:
        rows = con.execute(
            "SELECT id,event_type,payload_json,created_at FROM live_overlay_events WHERE user_id=? AND id>? ORDER BY id ASC LIMIT 50",
            (user_id, after),
        ).fetchall()
    return JSONResponse(
        {"events": [{"id": r["id"], "event_type": r["event_type"], "payload": json.loads(r["payload_json"]), "created_at": r["created_at"]} for r in rows]},
        headers={"Cache-Control": "no-store"},
    )


class SourceSpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


@router.post("/live-overlay/source/{token}/speech", include_in_schema=False)
def source_speech(token: str, body: SourceSpeakRequest):
    user_id = _user_for_source(token)
    profile = _profile(user_id)
    if profile["all_audio_muted"]:
        raise HTTPException(409, "Overlay audio is muted")
    mode = profile["voice_mode"]
    if mode == "browser":
        raise HTTPException(400, "Browser voice is synthesized in the browser source")
    fd, filename = tempfile.mkstemp(prefix="aura-live-tts-", suffix=".wav")
    os.close(fd)
    target = Path(filename)
    try:
        if mode == "clone":
            url = (os.getenv("AURA_LIVE_CLONE_TTS_URL") or "").rstrip("/")
            secret = (os.getenv("AURA_LIVE_CLONE_TTS_SECRET") or "").strip()
            voice_profile_id = profile.get("voice_profile_id")
            if not url or not secret or not voice_profile_id:
                raise HTTPException(503, "Consent-approved cloned LIVE voice provider is not configured")
            response = requests.post(
                f"{url}/synthesize",
                json={"text": body.text, "voice_profile_id": voice_profile_id},
                headers={"Authorization": f"Bearer {secret}"},
                timeout=60,
            )
            response.raise_for_status()
            target.write_bytes(response.content)
        else:
            AuraSpeechService().speak(body.text, target)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(503, f"LIVE speech synthesis unavailable: {type(exc).__name__}") from exc
    return FileResponse(target, media_type="audio/wav", filename="Aura_LIVE_Speech.wav", headers={"Cache-Control": "no-store"})


@router.get("/api/live-overlay/capabilities")
def overlay_capabilities(request: Request):
    member = _member(request)
    return {
        "product": "Aura LIVE Overlay Studio",
        "plan": member.plan.id,
        "tier": _tier(member.plan.id),
        "single_browser_source": True,
        "tiktok_live_studio_link_source": True,
        "obs_browser_source": True,
        "streamlabs_browser_source": True,
        "gift_sound_mute_without_hiding_gifts": True,
        "all_overlay_audio_mute": True,
        "tts_chat": True,
        "tts_gifts": True,
        "viewer_welcome_once_per_session": True,
        "welcome_audience_filters": ["all", "followers", "subscribers", "vip"],
        "browser_voice": True,
        "aura_voice": bool(_tier(member.plan.id)["aura_voice"]),
        "consent_approved_clone_voice": bool(_tier(member.plan.id)["voice_clone"] and member.plan.has(APPROVED_VOICE_DUPLICATION)),
        "event_simulator": True,
        "provider_connection_claimed": False,
        "native_tiktok_live_studio_audio_control_claimed": False,
        "note": "The mute button controls Aura LIVE Overlay Studio gift effects. It does not claim undocumented control over TikTok LIVE Studio's own native application audio.",
    }


__all__ = ["router", "TIER_MATRIX", "WIDGET_CATALOG"]
