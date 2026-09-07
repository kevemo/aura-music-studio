from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .aura_live_overlay_effects import (
    EFFECT_BY_ID,
    MAX_EFFECTS_PER_EVENT,
    _connect,
    _ensure_profile,
    _preferences,
)

router = APIRouter(tags=["Aura LIVE Overlay Effect Packs"])
CUSTOM_PACK_LIMIT = 30


def _pack(
    pack_id: str,
    name: str,
    description: str,
    effects: list[str],
    *,
    intensity: float = 1.0,
    max_effects_per_event: int = 6,
    reduced_motion: bool = False,
) -> dict:
    unknown = [effect_id for effect_id in effects if effect_id not in EFFECT_BY_ID]
    if unknown:
        raise RuntimeError(f"Built-in LIVE effect pack references unknown effect: {unknown[0]}")
    return {
        "id": pack_id,
        "name": name,
        "description": description,
        "enabled_effects": list(dict.fromkeys(effects)),
        "intensity": max(0.25, min(float(intensity), 2.0)),
        "max_effects_per_event": max(1, min(int(max_effects_per_event), MAX_EFFECTS_PER_EVENT)),
        "reduced_motion": bool(reduced_motion),
        "built_in": True,
    }


BUILTIN_PACKS = [
    _pack(
        "clean-minimal",
        "Clean Minimal",
        "Low-clutter professional reactions for interviews, podcasts, education and business LIVE streams.",
        [
            "supporter_halo", "like_ripple", "follower_name_pop", "community_welcome",
            "share_ribbon", "progress_glow", "question_pop", "pinned_glow",
        ],
        intensity=0.65,
        max_effects_per_event=3,
    ),
    _pack(
        "gaming-energy",
        "Gaming Energy",
        "Fast, high-energy reactions for gameplay, raids, gifts, milestones and competitive streams.",
        [
            "gift_cannon", "coin_rain", "diamond_spark", "heart_fountain", "reaction_pop",
            "follower_confetti", "subscriber_fireworks", "share_wave", "goal_fireworks",
            "battle_neon_frame", "battle_clash", "score_surge", "victory_fireworks",
            "battle_lightning", "camera_neon_pulse", "cyber_glitch", "zoom_pop", "screen_shake",
        ],
        intensity=1.45,
        max_effects_per_event=8,
    ),
    _pack(
        "music-stage",
        "Music Stage",
        "Stage-style sparkle, spotlight and supporter celebrations for singers, bands, DJs and musicians.",
        [
            "gift_cannon", "rose_shower", "diamond_spark", "supporter_spotlight", "heart_fountain",
            "subscriber_fireworks", "vip_portal", "community_stars", "goal_confetti",
            "milestone_shockwave", "spotlight_sweep", "star_drift", "sparkle_field",
            "aurora_sweep", "stardust_swirl", "celebration_banner",
        ],
        intensity=1.15,
        max_effects_per_event=6,
    ),
    _pack(
        "just-chatting",
        "Just Chatting",
        "Friendly social reactions that keep conversation readable while still rewarding community activity.",
        [
            "heart_fountain", "follower_confetti", "follower_name_pop", "community_welcome",
            "arrival_sparkles", "share_ribbon", "community_ripple", "comment_spark",
            "question_pop", "poll_pulse", "supporter_halo", "floating_hearts",
        ],
        intensity=0.85,
        max_effects_per_event=4,
    ),
    _pack(
        "battle-mode",
        "Battle Mode",
        "A dedicated TikTok LIVE Match / battle presentation with score, clash and victory reactions.",
        [
            "battle_neon_frame", "battle_clash", "score_surge", "victory_fireworks",
            "battle_lightning", "versus_shockwave", "gift_cannon", "diamond_spark",
            "golden_crown", "milestone_shockwave", "screen_shake", "celebration_flash",
        ],
        intensity=1.6,
        max_effects_per_event=8,
    ),
    _pack(
        "creator-growth",
        "Creator Growth",
        "Follows, shares, subscriptions and community milestones take priority for growth-focused LIVE sessions.",
        [
            "follower_confetti", "follower_name_pop", "subscriber_fireworks", "subscriber_crown",
            "community_welcome", "arrival_sparkles", "share_wave", "share_ribbon",
            "community_stars", "community_ripple", "goal_confetti", "trophy_burst",
            "camera_neon_pulse", "celebration_banner",
        ],
        intensity=1.05,
        max_effects_per_event=5,
    ),
    _pack(
        "cosmic-live",
        "Cosmic LIVE",
        "Shared Sky cosmic presentation with stars, aurora, sparkles, portals and celebration energy.",
        [
            "diamond_spark", "supporter_halo", "vip_portal", "arrival_sparkles", "community_stars",
            "milestone_shockwave", "hologram_sweep", "spotlight_sweep", "cosmic_dust",
            "star_drift", "sparkle_field", "aurora_sweep", "stardust_swirl", "firework_wall",
        ],
        intensity=1.1,
        max_effects_per_event=6,
    ),
    _pack(
        "calm-accessible",
        "Calm & Accessible",
        "Reduced-motion presentation for viewers who prefer quieter visual movement and lower event density.",
        [
            "supporter_halo", "like_ripple", "follower_name_pop", "share_ribbon",
            "progress_glow", "question_pop", "pinned_glow", "poll_pulse",
        ],
        intensity=0.5,
        max_effects_per_event=2,
        reduced_motion=True,
    ),
]
BUILTIN_BY_ID = {pack["id"]: pack for pack in BUILTIN_PACKS}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_schema() -> None:
    with _connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_overlay_custom_effect_packs (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                enabled_json TEXT NOT NULL,
                intensity REAL NOT NULL DEFAULT 1.0,
                max_effects_per_event INTEGER NOT NULL DEFAULT 6,
                reduced_motion INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_live_overlay_custom_effect_packs_user
                ON live_overlay_custom_effect_packs(user_id,updated_at DESC);
            """
        )


_init_schema()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _normalise_effects(values: list[str]) -> list[str]:
    enabled = list(dict.fromkeys(values))
    unknown = [effect_id for effect_id in enabled if effect_id not in EFFECT_BY_ID]
    if unknown:
        raise HTTPException(400, f"Unknown LIVE overlay effect: {unknown[0]}")
    return enabled


def _custom_pack(row) -> dict:
    try:
        enabled = json.loads(str(row["enabled_json"]))
    except Exception:
        enabled = []
    return {
        "id": str(row["id"]),
        "name": str(row["name"]),
        "description": str(row["description"]),
        "enabled_effects": [effect_id for effect_id in enabled if effect_id in EFFECT_BY_ID],
        "intensity": max(0.25, min(float(row["intensity"]), 2.0)),
        "max_effects_per_event": max(1, min(int(row["max_effects_per_event"]), MAX_EFFECTS_PER_EVENT)),
        "reduced_motion": bool(row["reduced_motion"]),
        "built_in": False,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _custom_packs(user_id: str) -> list[dict]:
    with _connect() as con:
        rows = con.execute(
            "SELECT * FROM live_overlay_custom_effect_packs WHERE user_id=? ORDER BY updated_at DESC,id",
            (user_id,),
        ).fetchall()
    return [_custom_pack(row) for row in rows]


def _pack_for_user(user_id: str, pack_id: str) -> dict:
    built_in = BUILTIN_BY_ID.get(pack_id)
    if built_in:
        return dict(built_in)
    with _connect() as con:
        row = con.execute(
            "SELECT * FROM live_overlay_custom_effect_packs WHERE id=? AND user_id=?",
            (pack_id, user_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "LIVE overlay effect pack not found")
    return _custom_pack(row)


def _apply_pack(user_id: str, pack: dict) -> dict:
    enabled = _normalise_effects(list(pack["enabled_effects"]))
    now = _now()
    with _connect() as con:
        con.execute(
            """INSERT INTO live_overlay_effect_preferences(
                   user_id,enabled_json,intensity,max_effects_per_event,reduced_motion,updated_at
               ) VALUES(?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 enabled_json=excluded.enabled_json,
                 intensity=excluded.intensity,
                 max_effects_per_event=excluded.max_effects_per_event,
                 reduced_motion=excluded.reduced_motion,
                 updated_at=excluded.updated_at""",
            (
                user_id,
                json.dumps(enabled),
                max(0.25, min(float(pack["intensity"]), 2.0)),
                max(1, min(int(pack["max_effects_per_event"]), MAX_EFFECTS_PER_EVENT)),
                int(bool(pack["reduced_motion"])),
                now,
            ),
        )
    return _preferences(user_id)


class PackCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    enabled_effects: list[str] | None = Field(default=None, max_length=128)
    intensity: float | None = Field(default=None, ge=0.25, le=2.0)
    max_effects_per_event: int | None = Field(default=None, ge=1, le=MAX_EFFECTS_PER_EVENT)
    reduced_motion: bool | None = None


class PackUpdate(PackCreate):
    pass


@router.get("/api/live-overlays/effect-packs")
def list_effect_packs(request: Request):
    member = _member(request)
    _ensure_profile(member.user_id)
    return {
        "built_in": BUILTIN_PACKS,
        "custom": _custom_packs(member.user_id),
        "current": _preferences(member.user_id),
        "all_effects_selectable": True,
        "pack_application_is_live_runtime_backed": True,
    }


@router.post("/api/live-overlays/effect-packs/{pack_id}/apply")
def apply_effect_pack(pack_id: str, request: Request):
    member = _member(request)
    _ensure_profile(member.user_id)
    pack = _pack_for_user(member.user_id, pack_id)
    current = _apply_pack(member.user_id, pack)
    return {"applied": True, "pack": pack, "current": current}


@router.post("/api/live-overlays/effect-packs/custom")
def create_custom_effect_pack(body: PackCreate, request: Request):
    member = _member(request)
    _ensure_profile(member.user_id)
    current = _preferences(member.user_id)
    enabled = _normalise_effects(body.enabled_effects if body.enabled_effects is not None else current["enabled_effects"])
    intensity = body.intensity if body.intensity is not None else current["intensity"]
    max_per_event = body.max_effects_per_event if body.max_effects_per_event is not None else current["max_effects_per_event"]
    reduced_motion = body.reduced_motion if body.reduced_motion is not None else current["reduced_motion"]
    with _connect() as con:
        count = int(con.execute(
            "SELECT COUNT(*) FROM live_overlay_custom_effect_packs WHERE user_id=?",
            (member.user_id,),
        ).fetchone()[0])
        if count >= CUSTOM_PACK_LIMIT:
            raise HTTPException(409, f"Custom LIVE effect pack limit reached ({CUSTOM_PACK_LIMIT})")
        pack_id = secrets.token_urlsafe(12)
        now = _now()
        con.execute(
            """INSERT INTO live_overlay_custom_effect_packs(
                   id,user_id,name,description,enabled_json,intensity,max_effects_per_event,reduced_motion,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                pack_id,
                member.user_id,
                body.name.strip(),
                body.description.strip(),
                json.dumps(enabled),
                float(intensity),
                int(max_per_event),
                int(bool(reduced_motion)),
                now,
                now,
            ),
        )
        row = con.execute(
            "SELECT * FROM live_overlay_custom_effect_packs WHERE id=? AND user_id=?",
            (pack_id, member.user_id),
        ).fetchone()
    return {"created": True, "pack": _custom_pack(row)}


@router.put("/api/live-overlays/effect-packs/custom/{pack_id}")
def update_custom_effect_pack(pack_id: str, body: PackUpdate, request: Request):
    member = _member(request)
    existing = _pack_for_user(member.user_id, pack_id)
    if existing["built_in"]:
        raise HTTPException(400, "Built-in LIVE effect packs cannot be overwritten")
    enabled = _normalise_effects(body.enabled_effects if body.enabled_effects is not None else existing["enabled_effects"])
    intensity = body.intensity if body.intensity is not None else existing["intensity"]
    max_per_event = body.max_effects_per_event if body.max_effects_per_event is not None else existing["max_effects_per_event"]
    reduced_motion = body.reduced_motion if body.reduced_motion is not None else existing["reduced_motion"]
    now = _now()
    with _connect() as con:
        con.execute(
            """UPDATE live_overlay_custom_effect_packs
               SET name=?,description=?,enabled_json=?,intensity=?,max_effects_per_event=?,reduced_motion=?,updated_at=?
               WHERE id=? AND user_id=?""",
            (
                body.name.strip(),
                body.description.strip(),
                json.dumps(enabled),
                float(intensity),
                int(max_per_event),
                int(bool(reduced_motion)),
                now,
                pack_id,
                member.user_id,
            ),
        )
        row = con.execute(
            "SELECT * FROM live_overlay_custom_effect_packs WHERE id=? AND user_id=?",
            (pack_id, member.user_id),
        ).fetchone()
    return {"updated": True, "pack": _custom_pack(row)}


@router.delete("/api/live-overlays/effect-packs/custom/{pack_id}")
def delete_custom_effect_pack(pack_id: str, request: Request):
    member = _member(request)
    with _connect() as con:
        cur = con.execute(
            "DELETE FROM live_overlay_custom_effect_packs WHERE id=? AND user_id=?",
            (pack_id, member.user_id),
        )
    if not cur.rowcount:
        raise HTTPException(404, "Custom LIVE overlay effect pack not found")
    return {"deleted": True, "pack_id": pack_id}


@router.get("/live-overlay-studio/effect-packs", response_class=HTMLResponse, include_in_schema=False)
def effect_packs_page(request: Request):
    member = _member(request)
    _ensure_profile(member.user_id)
    cards = "".join(
        f"<article class='card'><div class='badge'>BUILT-IN</div><h2>{escape(pack['name'])}</h2>"
        f"<p>{escape(pack['description'])}</p><small>{len(pack['enabled_effects'])} effects · intensity {pack['intensity']:.2g} · max {pack['max_effects_per_event']}/event"
        f"{' · reduced motion' if pack['reduced_motion'] else ''}</small>"
        f"<button class='apply' data-pack='{escape(pack['id'], quote=True)}'>Use this LIVE pack</button></article>"
        for pack in BUILTIN_PACKS
    )
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>LIVE Effect Packs</title><style>
body{{margin:0;background:#070812;color:#fff;font-family:Inter,system-ui,sans-serif}}main{{width:min(1180px,calc(100% - 28px));margin:auto;padding:34px 0 60px}}a{{color:#fff}}h1{{font-size:clamp(2.5rem,6vw,5rem);margin:.12em 0}}.muted,small{{color:#b9c2d5;line-height:1.55}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.card,.panel{{background:#111526;border:1px solid #ffffff20;border-radius:18px;padding:18px}}.card h2{{margin:.35em 0}}.badge{{font-size:.7rem;font-weight:900;color:#efc96b}}button,input,textarea{{font:inherit}}button{{display:block;margin-top:14px;background:linear-gradient(120deg,#efc96b,#9b72ff);color:#140a1e;border:0;border-radius:10px;padding:11px 14px;font-weight:900;cursor:pointer}}input,textarea{{width:100%;box-sizing:border-box;background:#090d18;color:#fff;border:1px solid #ffffff22;border-radius:10px;padding:10px;margin:5px 0 12px}}.panel{{margin:16px 0}}.custom{{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:center;padding:10px 0;border-bottom:1px solid #ffffff12}}.custom button{{margin:0;background:#ffffff0d;color:#fff;border:1px solid #ffffff22}}@media(max-width:900px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><main><a href='/live-overlay-studio/effects'>← LIVE Overlay Effects Library</a><p class='muted'>Shared Sky Streaming Studios · Aura LIVE</p><h1>One-click LIVE Effect Packs</h1><p class='muted'>Choose a complete visual reaction style in one click. Applying a pack updates the same persistent settings consumed by your transparent LIVE browser source, so the new selection becomes active without creating a second overlay source URL.</p><section class='grid'>{cards}</section><section class='panel'><h2>Save your own pack</h2><p class='muted'>Your current effect selection, intensity, event cap and reduced-motion preference will be saved as a reusable pack.</p><label>Pack name</label><input id='name' maxlength='80' placeholder='My LIVE style'><label>Description</label><textarea id='description' maxlength='300' placeholder='What this pack is for'></textarea><button id='save'>Save current LIVE setup</button><p id='status' class='muted'></p></section><section class='panel'><h2>My custom packs</h2><div id='custom' class='muted'>Loading…</div></section></main><script>
const $=x=>document.getElementById(x);async function load(){{const r=await fetch('/api/live-overlays/effect-packs',{{cache:'no-store'}});if(!r.ok)return;const d=await r.json();$('custom').innerHTML='';if(!d.custom.length){{$('custom').textContent='No custom packs saved yet.';return}}for(const p of d.custom){{const row=document.createElement('div');row.className='custom';const text=document.createElement('div');const b=document.createElement('b');b.textContent=p.name;const s=document.createElement('small');s.textContent=`${{p.enabled_effects.length}} effects · intensity ${{p.intensity}}`;text.append(b,document.createElement('br'),s);const actions=document.createElement('div');const use=document.createElement('button');use.textContent='Use';use.onclick=()=>apply(p.id);const del=document.createElement('button');del.textContent='Delete';del.onclick=async()=>{{if(!confirm('Delete this custom LIVE pack?'))return;await fetch('/api/live-overlays/effect-packs/custom/'+encodeURIComponent(p.id),{{method:'DELETE'}});load()}};actions.append(use,del);row.append(text,actions);$('custom').appendChild(row)}}}}
async function apply(id){{const r=await fetch('/api/live-overlays/effect-packs/'+encodeURIComponent(id)+'/apply',{{method:'POST'}});const d=await r.json();if(!r.ok)return alert(d.detail||'Unable to apply pack');$('status').textContent=`Applied: ${{d.pack.name}} · your LIVE source will refresh automatically.`;}}
document.querySelectorAll('.apply').forEach(b=>b.onclick=()=>apply(b.dataset.pack));$('save').onclick=async()=>{{const name=$('name').value.trim();if(!name)return alert('Enter a pack name');const r=await fetch('/api/live-overlays/effect-packs/custom',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{name,description:$('description').value}})}});const d=await r.json();if(!r.ok)return alert(d.detail||'Unable to save pack');$('name').value='';$('description').value='';$('status').textContent='Custom LIVE pack saved.';load()}};load();
</script></body></html>""",
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"},
    )


__all__ = ["BUILTIN_PACKS", "BUILTIN_BY_ID", "router"]
