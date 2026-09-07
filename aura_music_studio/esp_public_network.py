from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from html import escape
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .accounts import AccountStore
from .branding import ENDORSEMENT, PRODUCT_FULL_NAME, TAGLINE
from .owner_identity import owner_actor, owner_session_authorized

router = APIRouter()
accounts = AccountStore()
_DB_PATH = accounts.db_path
_MAX_VIDEO_BYTES = 250 * 1024 * 1024
_ALLOWED_VIDEO_TYPES = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}
_SAFE_TITLE = re.compile(r"[^A-Za-z0-9 .,'&()!_-]+")

_APPLY_LINKS = {
    "USA & Canada": "https://www.tiktok.com/t/ZMhoYM4EM/",
    "UK+": "https://www.tiktok.com/t/ZMhwyNd68/",
    "Australia & New Zealand": "https://www.tiktok.com/t/ZS46fSvqy/",
}

AURA_WELCOME = (
    "Welcome to Elevate Souls Productions Content Creation Command Center, powered by Aura AI. "
    "Elevate Souls Productions is a creator-focused TikTok LIVE Creator Network agency built to help people develop stronger, safer and more purposeful LIVE businesses. "
    "Our work brings together creator support, mentoring, training, performance development, professional standards, creative tools and operational guidance. "
    "Creators already inside the network can use their private ESP areas when their role has been approved. "
    "If you are not currently represented by another TikTok LIVE Creator Network, you can use the official region application choices on this page to apply for consideration. "
    "You can also hear directly from creators, members and the owners through the experience videos published below. "
    "Elevate your soul through purposeful media."
)


def _connect():
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _media_root() -> Path:
    root = Path(os.getenv("ESP_PUBLIC_MEDIA_ROOT", "data/esp_public_media"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _init_schema() -> None:
    with _connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS esp_public_testimonials (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                speaker_name TEXT NOT NULL,
                speaker_type TEXT NOT NULL CHECK(speaker_type IN ('creator','esp_member','owner')),
                caption TEXT NOT NULL DEFAULT '',
                media_filename TEXT NOT NULL,
                media_type TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                published INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 100,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_by TEXT NOT NULL,
                published_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_esp_public_testimonials_published
            ON esp_public_testimonials(published,sort_order,created_at DESC);
            """
        )


_init_schema()


def _clean_text(value: str, limit: int) -> str:
    return (value or "").strip()[:limit]


def _clean_title(value: str, limit: int = 120) -> str:
    value = _SAFE_TITLE.sub("", _clean_text(value, limit))
    if not value:
        raise ValueError("A title is required")
    return value


def _list_public() -> list[dict]:
    with _connect() as con:
        rows = con.execute(
            "SELECT * FROM esp_public_testimonials WHERE published=1 ORDER BY sort_order ASC,created_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def _list_all() -> list[dict]:
    with _connect() as con:
        rows = con.execute("SELECT * FROM esp_public_testimonials ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]


def _record(record_id: str) -> dict | None:
    with _connect() as con:
        row = con.execute("SELECT * FROM esp_public_testimonials WHERE id=?", (record_id,)).fetchone()
    return dict(row) if row else None


def _public_css() -> str:
    return """
    :root{--bg:#08050d;--panel:#17101f;--line:#ffffff1f;--accent:#f0c76d;--violet:#8d69e8;--muted:#c9bfd2;--good:#7cdda3}
    *{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 90% 0,#3a1c5a,transparent 28%),radial-gradient(circle at 8% 0,#3a2511,transparent 24%),var(--bg);color:white;font-family:Inter,ui-sans-serif,system-ui,sans-serif}.wrap{width:min(1240px,calc(100% - 28px));margin:auto;padding:22px 0 60px}a{text-decoration:none;color:inherit}.nav,.row{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.brand{font-weight:950}.brand small{display:block;color:var(--accent);font-size:.72rem;letter-spacing:.12em;text-transform:uppercase}.btn,button{display:inline-block;border:1px solid var(--line);border-radius:11px;padding:10px 13px;background:#ffffff0a;color:#fff;font-weight:850;cursor:pointer}.primary{border:0;background:linear-gradient(120deg,var(--accent),var(--violet));color:#150d1d}.hero{padding:58px 0 24px;display:grid;grid-template-columns:1.2fr .8fr;gap:20px}.card{border:1px solid var(--line);border-radius:20px;background:#17101fe8;padding:20px;margin:12px 0}.eyebrow{color:var(--accent);font-size:.74rem;font-weight:950;letter-spacing:.14em;text-transform:uppercase}h1{font-size:clamp(2.8rem,7vw,5.3rem);letter-spacing:-.055em;line-height:.96;margin:.16em 0}h2{font-size:clamp(1.7rem,4vw,2.5rem);margin:.2em 0 .45em}.muted{color:var(--muted);line-height:1.62}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.apply{border-color:#f0c76d66}.video-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.video-card video{width:100%;aspect-ratio:9/16;max-height:550px;background:#000;border-radius:14px}.pill{display:inline-block;border:1px solid var(--line);padding:4px 8px;border-radius:999px;font-size:.75rem;color:var(--accent)}.fine{font-size:.83rem;color:var(--muted)}@media(max-width:850px){.hero,.grid3,.video-grid{grid-template-columns:1fr}}"""


def _page(body: str, title: str = "Elevate Souls Productions Creator Network") -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='index,follow'><title>{escape(title)}</title><style>{_public_css()}</style></head>"
        f"<body><main class='wrap'><nav class='nav'><a class='brand' href='/'>{escape(PRODUCT_FULL_NAME)}<small>{escape(ENDORSEMENT)}</small></a><div class='row'><a class='btn' href='/'>Home</a><a class='btn' href='/pricing'>Pricing</a><a class='btn primary' href='/esp-network'>ESP Creator Network</a></div></nav>{body}<footer class='fine' style='margin-top:34px;border-top:1px solid var(--line);padding-top:22px'>{escape(TAGLINE)}</footer></main></body></html>"
    )


@router.get("/esp-network", response_class=HTMLResponse, include_in_schema=False)
def esp_network_public():
    testimonial_cards = "".join(
        f"""<article class='card video-card'><video controls preload='metadata' playsinline src='/esp-network/testimonials/{escape(row['id'], quote=True)}/media'></video><p><span class='pill'>{escape(row['speaker_type'].replace('_',' ').title())}</span></p><h3>{escape(row['title'])}</h3><p><b>{escape(row['speaker_name'])}</b></p><p class='muted'>{escape(row['caption'])}</p></article>"""
        for row in _list_public()
    ) or "<div class='card'><p class='muted'>Creator and owner experience videos will appear here as they are published by Elevate Souls Productions.</p></div>"

    apply_cards = "".join(
        f"""<article class='card apply'><div class='eyebrow'>{escape(region)}</div><h3>Apply to Elevate Souls Productions</h3><p class='muted'>Use this regional TikTok application link if you are eligible and are not already represented by another TikTok LIVE Creator Network.</p><a class='btn primary' rel='noopener noreferrer' target='_blank' href='{escape(url, quote=True)}'>Apply on TikTok</a></article>"""
        for region, url in _APPLY_LINKS.items()
    )

    narration = escape(AURA_WELCOME, quote=True)
    body = f"""
    <section class='hero'><div><div class='eyebrow'>Elevate Souls Productions · TikTok LIVE Creator Network</div><h1>Build your LIVE journey with purpose.</h1><p class='muted'>Elevate Souls Productions supports creators with structured mentoring, creator development, training, performance guidance, professional standards, creative resources and a private operational ecosystem designed to help members build sustainable LIVE-host businesses.</p><div class='row' style='justify-content:flex-start'><button class='primary' id='auraSpeak'>🔊 Hear Aura's welcome</button><button id='auraStop'>Stop narration</button></div></div><aside class='card'><div class='eyebrow'>Aura welcome</div><h2>Welcome to the network.</h2><p class='muted' id='auraWelcome'>{escape(AURA_WELCOME)}</p></aside></section>
    <section class='card'><div class='eyebrow'>What Elevate Souls Productions does</div><h2>Creator development, support and professional growth.</h2><p class='muted'>As a TikTok LIVE Creator Network agency, Elevate Souls Productions works with eligible LIVE creators to support development and consistency. The network environment combines onboarding, mentoring, training, progress tracking, campaign and opportunity visibility, creator and agent support, community standards, safety guidance and performance-led coaching. The Command Center also gives approved ESP members private tools that are separate from ordinary paid creative memberships.</p><p class='muted'>The goal is not simply to get people to go LIVE. It is to help creators understand how to build stronger shows, communicate professionally, develop their niche and community, learn platform-safe practices, use their data constructively and keep improving over time. The owner and mentoring teams can review progress while creators retain their own identity, content and audience relationships.</p></section>
    <section><div class='eyebrow'>Apply by region</div><h2>Not currently with another Creator Network?</h2><p class='muted'>Choose your region below to open the relevant TikTok application link. Eligibility and acceptance remain subject to TikTok and Elevate Souls Productions review; submitting a form does not guarantee acceptance.</p><div class='grid3'>{apply_cards}</div></section>
    <section style='margin-top:36px'><div class='eyebrow'>Real network experiences</div><h2>Hear from creators, ESP members and the owners.</h2><p class='muted'>These videos are uploaded and published through the Mary / Kev Owner Command Center so visitors can hear directly from people speaking about their own experience.</p><div class='video-grid'>{testimonial_cards}</div></section>
    <script>
    (()=>{{const text={narration!r};const speak=document.getElementById('auraSpeak');const stop=document.getElementById('auraStop');function chooseVoice(){{const voices=speechSynthesis.getVoices();return voices.find(v=>/female|samantha|serena|aria|sonia|ava/i.test(v.name))||voices.find(v=>/^en/i.test(v.lang))||voices[0];}}speak?.addEventListener('click',()=>{{speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(text);u.voice=chooseVoice();u.rate=.95;u.pitch=1.02;speechSynthesis.speak(u);}});stop?.addEventListener('click',()=>speechSynthesis.cancel());}})();
    </script>"""
    return _page(body)


@router.get("/creator-network", include_in_schema=False)
def creator_network_alias():
    return RedirectResponse("/esp-network", status_code=308)


@router.get("/esp-network/testimonials/{record_id}/media", include_in_schema=False)
def testimonial_media(record_id: str):
    row = _record(record_id)
    if not row or not row["published"]:
        return HTMLResponse("Not found", status_code=404)
    path = (_media_root() / row["media_filename"]).resolve()
    root = _media_root().resolve()
    if root not in path.parents or not path.is_file():
        return HTMLResponse("Not found", status_code=404)
    return FileResponse(path, media_type=row["media_type"], filename=None)


@router.get("/owner/network-stories", response_class=HTMLResponse, include_in_schema=False)
def owner_network_stories(request: Request, message: str = ""):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    rows = "".join(
        f"""<tr><td>{escape(row['title'])}<br><small>{escape(row['speaker_name'])}</small></td><td>{escape(row['speaker_type'].replace('_',' ').title())}</td><td>{'Published' if row['published'] else 'Draft'}</td><td><form method='post' action='/owner/network-stories/{escape(row['id'], quote=True)}/publish'><input type='hidden' name='published' value='{'0' if row['published'] else '1'}'><button>{'Unpublish' if row['published'] else 'Publish'}</button></form></td></tr>"""
        for row in _list_all()
    ) or "<tr><td colspan='4'>No experience videos uploaded yet.</td></tr>"
    return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Network Stories</title><style>{_public_css()}input,select,textarea{{width:100%;padding:10px;border:1px solid var(--line);background:#09070e;color:#fff;border-radius:9px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid var(--line);text-align:left}}</style></head><body><main class='wrap'><div class='row'><a class='btn' href='/owner/dashboard'>Owner Command Center</a><a class='btn' href='/esp-network' target='_blank'>View public page</a></div><h1>Network Experience Videos</h1>{f'<div class="card">{escape(message)}</div>' if message else ''}<div class='grid3'><div class='card'><h2>Upload a story</h2><form method='post' enctype='multipart/form-data' action='/owner/network-stories/upload'><p><label>Title<input name='title' required maxlength='120'></label></p><p><label>Speaker name<input name='speaker_name' required maxlength='120'></label></p><p><label>Speaker type<select name='speaker_type'><option value='creator'>Creator</option><option value='esp_member'>ESP member</option><option value='owner'>Owner</option></select></label></p><p><label>Caption<textarea name='caption' maxlength='1200'></textarea></label></p><p><label>Video<input type='file' name='video' accept='video/mp4,video/webm,video/quicktime' required></label></p><button class='primary'>Upload as draft</button></form></div><div class='card'><h2>Publishing rules</h2><p class='muted'>Uploads stay private drafts until Mary or Kev publishes them. Use only videos you have permission to publish. The site stores an integrity hash and an opaque media identifier; visitor URLs do not expose server filesystem paths.</p></div></div><div class='card' style='overflow:auto'><table><tr><th>Story</th><th>Speaker</th><th>Status</th><th>Action</th></tr>{rows}</table></div></main></body></html>""")


@router.post("/owner/network-stories/upload", include_in_schema=False)
async def owner_upload_story(request: Request):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    form = await request.form()
    upload = form.get("video")
    if not isinstance(upload, UploadFile):
        return RedirectResponse("/owner/network-stories?message=Video+upload+is+required", status_code=303)
    media_type = (upload.content_type or "").lower()
    suffix = _ALLOWED_VIDEO_TYPES.get(media_type)
    if not suffix:
        return RedirectResponse("/owner/network-stories?message=Unsupported+video+format", status_code=303)
    try:
        title = _clean_title(str(form.get("title") or ""))
        speaker_name = _clean_title(str(form.get("speaker_name") or ""))
        speaker_type = str(form.get("speaker_type") or "creator").strip().lower()
        if speaker_type not in {"creator", "esp_member", "owner"}:
            raise ValueError("Invalid speaker type")
        caption = _clean_text(str(form.get("caption") or ""), 1200)
    except ValueError as exc:
        return RedirectResponse(f"/owner/network-stories?message={escape(str(exc), quote=True)}", status_code=303)

    record_id = uuid4().hex
    filename = f"{record_id}{suffix}"
    target = _media_root() / filename
    digest = hashlib.sha256()
    total = 0
    try:
        with target.open("xb") as fh:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_VIDEO_BYTES:
                    raise ValueError("Video exceeds the 250 MB upload limit")
                digest.update(chunk)
                fh.write(chunk)
        if total < 1024:
            raise ValueError("Video file is empty or invalid")
        with _connect() as con:
            con.execute("""INSERT INTO esp_public_testimonials
                (id,title,speaker_name,speaker_type,caption,media_filename,media_type,sha256,published,sort_order,created_by)
                VALUES (?,?,?,?,?,?,?,?,0,100,?)""",
                (record_id, title, speaker_name, speaker_type, caption, filename, media_type, digest.hexdigest(), owner_actor(request)[:120]))
    except (ValueError, OSError, sqlite3.Error) as exc:
        target.unlink(missing_ok=True)
        return RedirectResponse(f"/owner/network-stories?message={escape(str(exc), quote=True)}", status_code=303)
    return RedirectResponse("/owner/network-stories?message=Video+uploaded+as+private+draft", status_code=303)


@router.post("/owner/network-stories/{record_id}/publish", include_in_schema=False)
async def owner_publish_story(record_id: str, request: Request):
    if not owner_session_authorized(request):
        return RedirectResponse("/owner", status_code=303)
    form = await request.form()
    published = str(form.get("published") or "0") == "1"
    with _connect() as con:
        row = con.execute("SELECT id FROM esp_public_testimonials WHERE id=?", (record_id,)).fetchone()
        if not row:
            return RedirectResponse("/owner/network-stories?message=Story+not+found", status_code=303)
        con.execute("UPDATE esp_public_testimonials SET published=?,published_at=CASE WHEN ?=1 THEN CURRENT_TIMESTAMP ELSE NULL END WHERE id=?", (int(published), int(published), record_id))
    return RedirectResponse("/owner/network-stories?message=Publishing+status+updated", status_code=303)
