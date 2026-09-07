from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .branding import PRODUCT_FULL_NAME

router = APIRouter(tags=["Aura LIVE Overlay Studio Advanced"])
DB_PATH = Path(os.getenv("AURA_LIVE_OVERLAY_DB", "data/aura_live_overlay.sqlite3"))
MEDIA_ROOT = Path(os.getenv("AURA_LIVE_OVERLAY_MEDIA_ROOT", "data/aura_live_overlay_media"))
MAX_MEDIA_BYTES = 50 * 1024 * 1024
MEDIA_TYPES = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif",
    "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/ogg": ".ogg",
    "video/mp4": ".mp4", "video/webm": ".webm",
}
EVENT_TYPES = {
    "viewer_joined", "follow", "subscribe", "gift", "share", "like", "like_milestone", "comment",
    "battle_start", "battle_progress", "battle_end", "poll", "treasure_chest", "question", "pinned_message",
    "live_shopping", "intro", "custom",
}
ACTIONS = {
    "show_widget", "hide_widget", "play_media", "play_sound", "speak", "set_text", "increment_goal",
    "start_timer", "add_timer_seconds", "spin_wheel", "spotlight_viewer", "switch_scene", "set_theme",
}
TIER_LIMITS = {
    "free": {"rules": 5, "scenes": 1, "goals": 2, "media": 5, "widgets": 8, "advanced": False},
    "base": {"rules": 30, "scenes": 5, "goals": 12, "media": 50, "widgets": 30, "advanced": True},
    "pro": {"rules": 250, "scenes": 30, "goals": 100, "media": 250, "widgets": 100, "advanced": True},
}


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
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    with _connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_overlay_scenes (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                width INTEGER NOT NULL DEFAULT 1080,
                height INTEGER NOT NULL DEFAULT 1920,
                layout_json TEXT NOT NULL DEFAULT '[]',
                active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_live_overlay_scenes_user ON live_overlay_scenes(user_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS live_overlay_rules (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                condition_json TEXT NOT NULL DEFAULT '{}',
                actions_json TEXT NOT NULL DEFAULT '[]',
                cooldown_seconds INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_live_overlay_rules_user ON live_overlay_rules(user_id, enabled, updated_at DESC);
            CREATE TABLE IF NOT EXISTS live_overlay_goals (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                metric TEXT NOT NULL,
                target REAL NOT NULL,
                current REAL NOT NULL DEFAULT 0,
                reset_mode TEXT NOT NULL DEFAULT 'manual',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS live_overlay_media (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_live_overlay_media_user ON live_overlay_media(user_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS live_overlay_session_stats (
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                gift_count INTEGER NOT NULL DEFAULT 0,
                gift_value REAL NOT NULL DEFAULT 0,
                likes INTEGER NOT NULL DEFAULT 0,
                shares INTEGER NOT NULL DEFAULT 0,
                comments INTEGER NOT NULL DEFAULT 0,
                follows INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id, username)
            );
            """
        )


_init_schema()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _limits(plan_id: str) -> dict:
    return dict(TIER_LIMITS.get((plan_id or "free").lower(), TIER_LIMITS["free"]))


def _count(con: sqlite3.Connection, table: str, user_id: str) -> int:
    if table not in {"live_overlay_scenes", "live_overlay_rules", "live_overlay_goals", "live_overlay_media"}:
        raise ValueError("Unsupported table")
    return int(con.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (user_id,)).fetchone()[0])


class WidgetPlacement(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    widget: str = Field(min_length=1, max_length=80)
    x: float = Field(default=0, ge=0, le=100)
    y: float = Field(default=0, ge=0, le=100)
    w: float = Field(default=30, ge=2, le=100)
    h: float = Field(default=12, ge=2, le=100)
    z: int = Field(default=1, ge=0, le=999)
    visible: bool = True
    settings: dict = Field(default_factory=dict)


class SceneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    width: int = Field(default=1080, ge=320, le=7680)
    height: int = Field(default=1920, ge=320, le=7680)


class SceneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    layout: list[WidgetPlacement] | None = Field(default=None, max_length=100)
    active: bool | None = None


class RuleAction(BaseModel):
    action: str
    params: dict = Field(default_factory=dict)


class RuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    event_type: str
    conditions: dict = Field(default_factory=dict)
    actions: list[RuleAction] = Field(min_length=1, max_length=12)
    cooldown_seconds: int = Field(default=0, ge=0, le=3600)
    enabled: bool = True


class GoalCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    metric: str = Field(pattern="^(gifts|gift_value|likes|shares|follows|subscribers|custom)$")
    target: float = Field(gt=0, le=1000000000)
    current: float = Field(default=0, ge=0, le=1000000000)
    reset_mode: str = Field(default="manual", pattern="^(manual|per_live|daily|weekly|monthly)$")


class GoalPatch(BaseModel):
    current: float | None = Field(default=None, ge=0, le=1000000000)
    target: float | None = Field(default=None, gt=0, le=1000000000)
    enabled: bool | None = None


def _safe_conditions(data: dict) -> dict:
    allowed = {"gift_name", "min_coins", "username", "message_contains", "is_follower", "is_subscriber", "min_gift_count"}
    out = {}
    for key, value in data.items():
        if key not in allowed:
            continue
        if isinstance(value, str):
            out[key] = value[:120]
        elif isinstance(value, (int, float, bool)):
            out[key] = value
    return out


def _safe_actions(actions: list[RuleAction], plan_id: str) -> list[dict]:
    out = []
    for item in actions:
        if item.action not in ACTIONS:
            raise HTTPException(400, f"Unsupported overlay action: {item.action}")
        if plan_id == "free" and item.action in {"play_media", "switch_scene", "spin_wheel", "set_theme"}:
            raise HTTPException(403, f"{item.action} requires Basic or Pro")
        params = {}
        for key, value in item.params.items():
            if key not in {"widget_id", "media_id", "text", "seconds", "goal_id", "scene_id", "theme", "volume"}:
                continue
            if isinstance(value, str):
                params[key] = value[:300]
            elif isinstance(value, (int, float, bool)):
                params[key] = value
        out.append({"action": item.action, "params": params})
    return out


@router.get("/live-overlay-studio/editor", response_class=HTMLResponse, include_in_schema=False)
def editor(request: Request):
    member = _member(request)
    limits = _limits(member.plan.id)
    return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Aura LIVE Overlay Designer</title><style>
body{{margin:0;background:#070812;color:#fff;font-family:Inter,system-ui,sans-serif}}header{{padding:20px 26px;border-bottom:1px solid #ffffff18;display:flex;justify-content:space-between;gap:12px;align-items:center}}main{{display:grid;grid-template-columns:260px 1fr 300px;height:calc(100vh - 76px)}}aside{{padding:16px;border-right:1px solid #ffffff18;overflow:auto}}aside:last-child{{border-right:0;border-left:1px solid #ffffff18}}button,input,select{{background:#151a2a;color:#fff;border:1px solid #ffffff24;border-radius:9px;padding:9px;font:inherit}}button{{cursor:pointer}}.canvas-wrap{{overflow:auto;padding:25px;background:#0b0d16}}#canvas{{position:relative;width:540px;height:960px;margin:auto;background:linear-gradient(#101426,#080a11);border:1px solid #ffffff25;box-shadow:0 20px 80px #0008}}.widget{{position:absolute;background:#7c5cff33;border:1px solid #bbaaff99;border-radius:10px;padding:8px;min-width:70px;min-height:40px;cursor:move;overflow:hidden}}.item{{padding:9px;margin:6px 0;border:1px solid #ffffff18;border-radius:8px;cursor:pointer}}.muted{{color:#adb7cd;font-size:.88rem}}@media(max-width:950px){{main{{grid-template-columns:1fr;height:auto}}aside{{border:0!important}}}}
</style></head><body><header><div><b>Aura LIVE Overlay Designer</b><div class='muted'>{escape(PRODUCT_FULL_NAME)} · {escape(member.plan.name)} tier</div></div><div><a href='/live-overlay-studio' style='color:#fff'>Control room</a></div></header><main><aside><h3>Widgets</h3><div id='catalog'></div><h3>Scenes</h3><button onclick='newScene()'>+ New scene</button><div id='scenes'></div></aside><section class='canvas-wrap'><div id='canvas'></div></section><aside><h3>Selected widget</h3><div id='props' class='muted'>Select a widget to edit its label, position and size.</div><hr style='border-color:#ffffff18'><h3>Automation</h3><p class='muted'>Create event → action rules for gifts, follows, likes, shares, joins, subscriptions, battles, polls and more.</p><button onclick="location.href='/live-overlay-studio/automations'">Open Automations</button><h3>Tier capacity</h3><p class='muted'>Scenes: {limits['scenes']} · Rules: {limits['rules']} · Media items: {limits['media']}</p></aside></main><script>
let scene=null,selected=null;const catalog=['alert_box','welcome','gift_feed','chat_box','event_list','like_goal','gift_goal','subscriber_goal','leaderboard','stats','announcement','gift_combo','countdown','spin_wheel','supporter_spotlight','battle','poll','pinned_message','shopping','lower_third','social_rotator','camera_frame','captions'];
function el(tag,c,t){{let x=document.createElement(tag);if(c)x.className=c;if(t)x.textContent=t;return x}}
function renderCatalog(){{let c=document.getElementById('catalog');catalog.forEach(w=>{{let x=el('div','item',w.replaceAll('_',' '));x.onclick=()=>addWidget(w);c.appendChild(x)}})}}
async function loadScenes(){{let d=await (await fetch('/api/live-overlays/scenes')).json();let box=document.getElementById('scenes');box.innerHTML='';d.scenes.forEach(s=>{{let x=el('div','item',s.name+(s.active?' · LIVE':''));x.onclick=()=>loadScene(s.id);box.appendChild(x)}});if(!scene&&d.scenes.length)loadScene(d.scenes[0].id)}}
async function newScene(){{let name=prompt('Scene name','Main LIVE');if(!name)return;let r=await fetch('/api/live-overlays/scenes',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{name}})}});if(!r.ok)alert((await r.json()).detail);scene=(await r.json()).scene;render();loadScenes()}}
async function loadScene(id){{scene=(await (await fetch('/api/live-overlays/scenes/'+id)).json()).scene;render()}}
function addWidget(widget){{if(!scene)return alert('Create a scene first');scene.layout.push({{id:crypto.randomUUID(),widget,x:5,y:5,w:35,h:10,z:scene.layout.length+1,visible:true,settings:{{label:widget.replaceAll('_',' ')}}}});save();render()}}
function render(){{let c=document.getElementById('canvas');c.innerHTML='';if(!scene)return;scene.layout.forEach(w=>{{let x=el('div','widget',w.settings?.label||w.widget);x.style.left=w.x+'%';x.style.top=w.y+'%';x.style.width=w.w+'%';x.style.height=w.h+'%';x.onclick=e=>{{e.stopPropagation();selected=w;props()}};drag(x,w);c.appendChild(x)}})}}
function drag(node,w){{node.onpointerdown=e=>{{let r=node.parentElement.getBoundingClientRect(),sx=e.clientX,sy=e.clientY,ox=w.x,oy=w.y;node.setPointerCapture(e.pointerId);node.onpointermove=m=>{{w.x=Math.max(0,Math.min(100-w.w,ox+(m.clientX-sx)/r.width*100));w.y=Math.max(0,Math.min(100-w.h,oy+(m.clientY-sy)/r.height*100));render()}};node.onpointerup=()=>save()}}}}
function props(){{let p=document.getElementById('props');p.innerHTML='';if(!selected)return;p.innerHTML=`<label>Label</label><input id="lbl" value="${{(selected.settings?.label||selected.widget).replaceAll('"','&quot;')}}"><p>X <input id="x" type="number" value="${{selected.x}}"> Y <input id="y" type="number" value="${{selected.y}}"></p><p>W <input id="w" type="number" value="${{selected.w}}"> H <input id="h" type="number" value="${{selected.h}}"></p><button id="apply">Apply</button> <button id="del">Delete</button>`;document.getElementById('apply').onclick=()=>{{selected.settings=selected.settings||{{}};selected.settings.label=document.getElementById('lbl').value.slice(0,100);['x','y','w','h'].forEach(k=>selected[k]=Number(document.getElementById(k).value));save();render()}};document.getElementById('del').onclick=()=>{{scene.layout=scene.layout.filter(x=>x.id!==selected.id);selected=null;save();render();props()}}}}
async function save(){{if(!scene)return;let r=await fetch('/api/live-overlays/scenes/'+scene.id,{{method:'PATCH',headers:{{'content-type':'application/json'}},body:JSON.stringify({{layout:scene.layout}})}});if(!r.ok)console.error(await r.text())}}
renderCatalog();loadScenes();
</script></body></html>""")


@router.get("/live-overlay-studio/automations", response_class=HTMLResponse, include_in_schema=False)
def automations_page(request: Request):
    member = _member(request)
    return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Aura LIVE Automations</title><style>body{{margin:0;background:#080a13;color:#fff;font-family:Inter,system-ui;padding:24px}}.wrap{{max-width:1050px;margin:auto}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:15px}}.card{{padding:18px;background:#131827;border:1px solid #ffffff1d;border-radius:14px;margin:12px 0}}input,select,textarea,button{{width:100%;box-sizing:border-box;background:#0b0e18;color:#fff;border:1px solid #ffffff22;border-radius:8px;padding:10px;margin:5px 0;font:inherit}}button{{cursor:pointer;font-weight:800}}.muted{{color:#adb7cd}}@media(max-width:800px){{.grid{{grid-template-columns:1fr}}}}</style></head><body><div class='wrap'><a href='/live-overlay-studio/editor' style='color:#fff'>← Designer</a><h1>Aura LIVE Automations</h1><p class='muted'>Build simple WHEN → IF → DO rules without code. Nothing here can run arbitrary scripts or shell commands.</p><div class='grid'><div class='card'><h2>New rule</h2><input id='name' placeholder='Example: Universe celebration'><select id='event'><option>gift</option><option>follow</option><option>subscribe</option><option>share</option><option>like_milestone</option><option>viewer_joined</option><option>comment</option><option>battle_start</option><option>battle_end</option><option>poll</option></select><input id='gift' placeholder='Optional exact gift name'><input id='coins' type='number' placeholder='Optional minimum coins'><select id='action'><option value='show_widget'>Show widget</option><option value='play_media'>Play uploaded media</option><option value='play_sound'>Play sound</option><option value='speak'>Speak message</option><option value='increment_goal'>Increment goal</option><option value='add_timer_seconds'>Add timer seconds</option><option value='spin_wheel'>Spin wheel</option><option value='spotlight_viewer'>Spotlight viewer</option><option value='switch_scene'>Switch scene</option></select><input id='text' placeholder='Action text / widget ID / media ID'><button onclick='saveRule()'>Create automation</button></div><div><h2>Current rules</h2><div id='rules'></div></div></div></div><script>
async function load(){{let d=await (await fetch('/api/live-overlays/rules')).json();let x=document.getElementById('rules');x.innerHTML='';d.rules.forEach(r=>{{let c=document.createElement('div');c.className='card';c.innerHTML='<b>'+r.name+'</b><p class="muted">WHEN '+r.event_type+' → '+r.actions.map(a=>a.action).join(', ')+'</p>';x.appendChild(c)}})}}
async function saveRule(){{let conditions={{}};if(gift.value)conditions.gift_name=gift.value;if(coins.value)conditions.min_coins=Number(coins.value);let a=action.value,p={{}};if(a==='speak')p.text=text.value;else if(a==='play_media'||a==='play_sound')p.media_id=text.value;else if(a==='switch_scene')p.scene_id=text.value;else if(a==='increment_goal')p.goal_id=text.value;else if(a==='show_widget')p.widget_id=text.value;else if(a==='add_timer_seconds')p.seconds=Number(text.value)||30;let r=await fetch('/api/live-overlays/rules',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{name:name.value,event_type:event.value,conditions,actions:[{{action:a,params:p}}]}})}});if(!r.ok)return alert((await r.json()).detail);load()}}load();</script></body></html>""")


@router.get("/api/live-overlays/capabilities")
def capabilities(request: Request):
    member = _member(request)
    return {
        "tier": member.plan.id,
        "limits": _limits(member.plan.id),
        "event_types": sorted(EVENT_TYPES),
        "actions": sorted(ACTIONS),
        "browser_source": True,
        "drag_drop_editor": True,
        "scenes": True,
        "automations": True,
        "goals": True,
        "leaderboards": True,
        "media_library": True,
        "rotators": True,
        "gift_sound_mute": True,
        "all_audio_mute": True,
        "tts_controls": True,
        "provider_connection_claimed": False,
        "provider_note": "A production TikTok LIVE event connector still requires a separately validated event source. The simulator and overlay engine do not pretend to be TikTok data.",
    }


@router.get("/api/live-overlays/scenes")
def list_scenes(request: Request):
    member = _member(request)
    with _connect() as con:
        rows = con.execute("SELECT * FROM live_overlay_scenes WHERE user_id=? ORDER BY active DESC,updated_at DESC", (member.user_id,)).fetchall()
    return {"scenes": [{**dict(r), "active": bool(r["active"]), "layout": json.loads(r["layout_json"])} for r in rows]}


@router.post("/api/live-overlays/scenes")
def create_scene(body: SceneCreate, request: Request):
    member = _member(request); limits = _limits(member.plan.id)
    with _connect() as con:
        if _count(con, "live_overlay_scenes", member.user_id) >= limits["scenes"]:
            raise HTTPException(403, "Scene limit reached for this membership tier")
        sid = secrets.token_urlsafe(12); active = 1 if _count(con, "live_overlay_scenes", member.user_id) == 0 else 0
        con.execute("INSERT INTO live_overlay_scenes(id,user_id,name,width,height,layout_json,active,created_at,updated_at) VALUES(?,?,?,?,?,'[]',?,?,?)", (sid, member.user_id, body.name, body.width, body.height, active, _now(), _now()))
    return get_scene(sid, request)


@router.get("/api/live-overlays/scenes/{scene_id}")
def get_scene(scene_id: str, request: Request):
    member = _member(request)
    with _connect() as con:
        row = con.execute("SELECT * FROM live_overlay_scenes WHERE id=? AND user_id=?", (scene_id, member.user_id)).fetchone()
    if not row: raise HTTPException(404, "Scene not found")
    d = dict(row); d["active"] = bool(d["active"]); d["layout"] = json.loads(d.pop("layout_json")); return {"scene": d}


@router.patch("/api/live-overlays/scenes/{scene_id}")
def patch_scene(scene_id: str, body: SceneUpdate, request: Request):
    member = _member(request); limits = _limits(member.plan.id)
    with _connect() as con:
        row = con.execute("SELECT * FROM live_overlay_scenes WHERE id=? AND user_id=?", (scene_id, member.user_id)).fetchone()
        if not row: raise HTTPException(404, "Scene not found")
        layout = json.loads(row["layout_json"])
        if body.layout is not None:
            if len(body.layout) > limits["widgets"]: raise HTTPException(403, "Widget limit reached for this tier")
            layout = [w.model_dump() for w in body.layout]
        if body.active:
            con.execute("UPDATE live_overlay_scenes SET active=0 WHERE user_id=?", (member.user_id,))
        con.execute("UPDATE live_overlay_scenes SET name=?,layout_json=?,active=?,updated_at=? WHERE id=? AND user_id=?", (body.name or row["name"], json.dumps(layout, separators=(",", ":")), int(body.active if body.active is not None else row["active"]), _now(), scene_id, member.user_id))
    return get_scene(scene_id, request)


@router.get("/api/live-overlays/rules")
def list_rules(request: Request):
    member = _member(request)
    with _connect() as con: rows = con.execute("SELECT * FROM live_overlay_rules WHERE user_id=? ORDER BY updated_at DESC", (member.user_id,)).fetchall()
    return {"rules": [{**dict(r), "conditions": json.loads(r["condition_json"]), "actions": json.loads(r["actions_json"]), "enabled": bool(r["enabled"])} for r in rows]}


@router.post("/api/live-overlays/rules")
def create_rule(body: RuleCreate, request: Request):
    member = _member(request); limits = _limits(member.plan.id)
    if body.event_type not in EVENT_TYPES: raise HTTPException(400, "Unsupported LIVE event type")
    safe_actions = _safe_actions(body.actions, member.plan.id)
    with _connect() as con:
        if _count(con, "live_overlay_rules", member.user_id) >= limits["rules"]: raise HTTPException(403, "Automation rule limit reached for this membership tier")
        rid = secrets.token_urlsafe(12)
        con.execute("INSERT INTO live_overlay_rules(id,user_id,name,event_type,condition_json,actions_json,cooldown_seconds,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (rid, member.user_id, body.name, body.event_type, json.dumps(_safe_conditions(body.conditions)), json.dumps(safe_actions), body.cooldown_seconds, int(body.enabled), _now(), _now()))
    return {"created": True, "rule_id": rid}


@router.delete("/api/live-overlays/rules/{rule_id}")
def delete_rule(rule_id: str, request: Request):
    member = _member(request)
    with _connect() as con:
        cur = con.execute("DELETE FROM live_overlay_rules WHERE id=? AND user_id=?", (rule_id, member.user_id))
    if not cur.rowcount: raise HTTPException(404, "Rule not found")
    return {"deleted": True}


@router.get("/api/live-overlays/goals")
def list_goals(request: Request):
    member = _member(request)
    with _connect() as con: rows = con.execute("SELECT * FROM live_overlay_goals WHERE user_id=? ORDER BY updated_at DESC", (member.user_id,)).fetchall()
    return {"goals": [{**dict(r), "enabled": bool(r["enabled"]), "progress": min(1.0, float(r["current"])/float(r["target"])) if r["target"] else 0} for r in rows]}


@router.post("/api/live-overlays/goals")
def create_goal(body: GoalCreate, request: Request):
    member = _member(request); limits = _limits(member.plan.id)
    with _connect() as con:
        if _count(con, "live_overlay_goals", member.user_id) >= limits["goals"]: raise HTTPException(403, "Goal limit reached for this membership tier")
        gid = secrets.token_urlsafe(12)
        con.execute("INSERT INTO live_overlay_goals(id,user_id,name,metric,target,current,reset_mode,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,1,?,?)", (gid, member.user_id, body.name, body.metric, body.target, body.current, body.reset_mode, _now(), _now()))
    return {"created": True, "goal_id": gid}


@router.patch("/api/live-overlays/goals/{goal_id}")
def patch_goal(goal_id: str, body: GoalPatch, request: Request):
    member = _member(request)
    with _connect() as con:
        row = con.execute("SELECT * FROM live_overlay_goals WHERE id=? AND user_id=?", (goal_id, member.user_id)).fetchone()
        if not row: raise HTTPException(404, "Goal not found")
        con.execute("UPDATE live_overlay_goals SET current=?,target=?,enabled=?,updated_at=? WHERE id=? AND user_id=?", (body.current if body.current is not None else row["current"], body.target if body.target is not None else row["target"], int(body.enabled if body.enabled is not None else row["enabled"]), _now(), goal_id, member.user_id))
    return {"updated": True}


@router.get("/api/live-overlays/leaderboards")
def leaderboards(request: Request):
    member = _member(request)
    with _connect() as con: rows = con.execute("SELECT username,gift_count,gift_value,likes,shares,comments,follows FROM live_overlay_session_stats WHERE user_id=? ORDER BY gift_value DESC,likes DESC LIMIT 100", (member.user_id,)).fetchall()
    data = [dict(r) for r in rows]
    return {
        "top_gifters_value": sorted(data, key=lambda x: x["gift_value"], reverse=True)[:20],
        "top_gifters_count": sorted(data, key=lambda x: x["gift_count"], reverse=True)[:20],
        "top_likers": sorted(data, key=lambda x: x["likes"], reverse=True)[:20],
        "top_sharers": sorted(data, key=lambda x: x["shares"], reverse=True)[:20],
        "top_chatters": sorted(data, key=lambda x: x["comments"], reverse=True)[:20],
    }


@router.get("/api/live-overlays/media")
def list_media(request: Request):
    member = _member(request)
    with _connect() as con: rows = con.execute("SELECT id,original_name,mime_type,byte_size,sha256,created_at FROM live_overlay_media WHERE user_id=? ORDER BY created_at DESC", (member.user_id,)).fetchall()
    return {"media": [dict(r) for r in rows]}


@router.post("/api/live-overlays/media")
async def upload_media(request: Request, file: UploadFile = File(...)):
    member = _member(request); limits = _limits(member.plan.id)
    mime = (file.content_type or "").split(";",1)[0].lower()
    if mime not in MEDIA_TYPES: raise HTTPException(415, "Only approved image, audio and video overlay media is supported")
    with _connect() as con:
        if _count(con, "live_overlay_media", member.user_id) >= limits["media"]: raise HTTPException(403, "Overlay media limit reached for this tier")
    media_id = secrets.token_urlsafe(12); stored = media_id + MEDIA_TYPES[mime]; user_root = MEDIA_ROOT / hashlib.sha256(member.user_id.encode()).hexdigest()[:24]; user_root.mkdir(parents=True, exist_ok=True); target = user_root / stored
    size = 0; digest = hashlib.sha256()
    try:
        with target.open("xb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk: break
                size += len(chunk)
                if size > MAX_MEDIA_BYTES: raise HTTPException(413, "Overlay media is limited to 50 MB")
                digest.update(chunk); out.write(chunk)
    except Exception:
        target.unlink(missing_ok=True); raise
    with _connect() as con:
        con.execute("INSERT INTO live_overlay_media(id,user_id,original_name,stored_name,mime_type,byte_size,sha256,created_at) VALUES(?,?,?,?,?,?,?,?)", (media_id, member.user_id, Path(file.filename or "media").name[:180], stored, mime, size, digest.hexdigest(), _now()))
    return {"id": media_id, "mime_type": mime, "byte_size": size, "sha256": digest.hexdigest()}


@router.get("/api/live-overlays/media/{media_id}")
def media_file(media_id: str, request: Request):
    member = _member(request)
    with _connect() as con: row = con.execute("SELECT stored_name,mime_type FROM live_overlay_media WHERE id=? AND user_id=?", (media_id, member.user_id)).fetchone()
    if not row: raise HTTPException(404, "Media not found")
    user_root = MEDIA_ROOT / hashlib.sha256(member.user_id.encode()).hexdigest()[:24]; target = (user_root / row["stored_name"]).resolve()
    if user_root.resolve() not in target.parents or not target.is_file(): raise HTTPException(404, "Media not found")
    return FileResponse(target, media_type=row["mime_type"], headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@router.get("/api/live-overlays/provider-status")
def provider_status(request: Request):
    _member(request)
    return JSONResponse({"connected": False, "provider_connection_claimed": False, "simulator_available": True, "production_connector_required": True, "message": "The overlay engine is ready. A separately validated TikTok LIVE event source must be connected before real LIVE events can drive it."}, headers={"Cache-Control": "no-store"})
