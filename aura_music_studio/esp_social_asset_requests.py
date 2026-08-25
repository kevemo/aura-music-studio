from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_niche import require_esp_social_member
from .request_context import reset_current_user_id, set_current_user_id
from .social_management import SocialHouseStore, utc_now

router = APIRouter(tags=["ESP Social Asset Requests"])

_ALLOWED_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif",
    ".mp4", ".mov", ".webm", ".m4v",
    ".mp3", ".wav", ".m4a", ".flac", ".aac",
    ".pdf", ".txt", ".md",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _db_path() -> Path:
    path = Path(os.getenv("AURA_SOCIAL_ASSET_REQUEST_DB", "data/social_asset_requests.sqlite3")).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _asset_root() -> Path:
    root = Path(os.getenv("AURA_SOCIAL_ASSET_ROOT", "data/social_assets")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _max_bytes() -> int:
    raw = os.getenv("AURA_SOCIAL_ASSET_MAX_MB", "200")
    try:
        mb = max(1, min(int(raw), 2048))
    except ValueError:
        mb = 200
    return mb * 1024 * 1024


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _owner_dir(owner_user_id: str) -> str:
    return hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()[:24]


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise ValueError("Unsupported file type")
    return suffix


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(_db_path())
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS social_asset_requests (
            id TEXT PRIMARY KEY,
            token_hash TEXT UNIQUE NOT NULL,
            owner_user_id TEXT NOT NULL,
            space_id TEXT NOT NULL,
            content_id TEXT,
            title TEXT NOT NULL,
            instructions TEXT NOT NULL DEFAULT '',
            expires_at TEXT NOT NULL,
            revoked_at TEXT,
            created_at TEXT NOT NULL,
            last_used_at TEXT
        );
        CREATE TABLE IF NOT EXISTS social_requested_assets (
            id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            media_type TEXT NOT NULL,
            uploader_name TEXT NOT NULL,
            rights_statement TEXT NOT NULL,
            rights_confirmed_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_review',
            created_at TEXT NOT NULL,
            FOREIGN KEY(request_id) REFERENCES social_asset_requests(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_asset_request_owner ON social_asset_requests(owner_user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_requested_assets_request ON social_requested_assets(request_id, created_at DESC);
        """
    )
    con.commit()
    return con


class CreateAssetRequest(BaseModel):
    space_id: str = Field(min_length=1, max_length=140)
    content_id: str | None = Field(default=None, max_length=140)
    title: str = Field(default="Upload requested assets", min_length=1, max_length=300)
    instructions: str = Field(default="", max_length=3000)
    expires_hours: int = Field(default=168, ge=1, le=24 * 90)


def _validate_owner_target(owner_user_id: str, body: CreateAssetRequest) -> None:
    ctx = set_current_user_id(owner_user_id)
    try:
        house = SocialHouseStore().load(body.space_id)
        if body.content_id and not any(item.id == body.content_id for item in house.content):
            raise KeyError(body.content_id)
    finally:
        reset_current_user_id(ctx)


def create_asset_request(owner_user_id: str, body: CreateAssetRequest) -> dict:
    _validate_owner_target(owner_user_id, body)
    token = secrets.token_urlsafe(32)
    request_id = "assetreq_" + secrets.token_hex(12)
    expires = _now() + timedelta(hours=body.expires_hours)
    with _db() as con:
        con.execute(
            """INSERT INTO social_asset_requests
               (id,token_hash,owner_user_id,space_id,content_id,title,instructions,expires_at,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                request_id, _hash(token), owner_user_id, body.space_id, body.content_id,
                body.title.strip(), body.instructions.strip(), expires.isoformat(), utc_now(),
            ),
        )
    return {
        "id": request_id,
        "token": token,
        "url": f"/upload/social-assets/{token}",
        "expires_at": expires.isoformat(),
        "title": body.title.strip(),
    }


def _resolve(token: str) -> dict:
    if not token or len(token) > 300:
        raise PermissionError("Invalid asset request")
    with _db() as con:
        row = con.execute("SELECT * FROM social_asset_requests WHERE token_hash=?", (_hash(token),)).fetchone()
        if row is None:
            raise PermissionError("Asset request not found")
        if row["revoked_at"]:
            raise PermissionError("Asset request has been revoked")
        expires = datetime.fromisoformat(row["expires_at"])
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= _now():
            raise PermissionError("Asset request has expired")
        con.execute("UPDATE social_asset_requests SET last_used_at=? WHERE id=?", (utc_now(), row["id"]))
    return dict(row)


def asset_request_snapshot(token: str) -> dict:
    row = _resolve(token)
    return {
        "id": row["id"],
        "title": row["title"],
        "instructions": row["instructions"],
        "expires_at": row["expires_at"],
        "max_mb": _max_bytes() // (1024 * 1024),
        "allowed_suffixes": sorted(_ALLOWED_SUFFIXES),
    }


async def save_requested_asset(
    token: str,
    upload: UploadFile,
    *,
    uploader_name: str,
    rights_confirmed: bool,
) -> dict:
    request_row = _resolve(token)
    if not rights_confirmed:
        raise ValueError("Rights confirmation is required before uploading")
    suffix = _safe_suffix(upload.filename or "")
    request_id = request_row["id"]
    stored_name = secrets.token_hex(18) + suffix
    relative = Path(_owner_dir(request_row["owner_user_id"])) / request_row["space_id"] / "requests" / request_id / stored_name
    target = (_asset_root() / relative).resolve()
    root = _asset_root()
    if root not in target.parents:
        raise ValueError("Invalid asset path")
    target.parent.mkdir(parents=True, exist_ok=True)
    limit = _max_bytes()
    size = 0
    try:
        with target.open("xb") as handle:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise ValueError(f"File exceeds the {limit // (1024 * 1024)} MB upload limit")
                handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if size <= 0:
        target.unlink(missing_ok=True)
        raise ValueError("Uploaded file is empty")
    asset_id = "asset_" + secrets.token_hex(12)
    safe_uploader = " ".join((uploader_name or "Guest uploader").split())[:120] or "Guest uploader"
    rights_statement = "Uploader confirmed they own or are authorised to provide this asset for the requested ESP content workflow."
    with _db() as con:
        con.execute(
            """INSERT INTO social_requested_assets
               (id,request_id,original_name,stored_name,relative_path,size_bytes,media_type,uploader_name,
                rights_statement,rights_confirmed_at,status,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                asset_id, request_id, (upload.filename or "upload")[:300], stored_name, relative.as_posix(), size,
                (upload.content_type or "application/octet-stream")[:160], safe_uploader,
                rights_statement, utc_now(), "pending_review", utc_now(),
            ),
        )
    return {
        "id": asset_id,
        "original_name": (upload.filename or "upload")[:300],
        "size_bytes": size,
        "status": "pending_review",
        "rights_confirmed": True,
        "attached_to_project": False,
        "published": False,
    }


def list_asset_requests(owner_user_id: str) -> list[dict]:
    with _db() as con:
        rows = con.execute(
            """SELECT r.*,
               (SELECT COUNT(*) FROM social_requested_assets a WHERE a.request_id=r.id) AS asset_count
               FROM social_asset_requests r WHERE r.owner_user_id=? ORDER BY r.created_at DESC LIMIT 200""",
            (owner_user_id,),
        ).fetchall()
    return [
        {
            "id": row["id"], "space_id": row["space_id"], "content_id": row["content_id"],
            "title": row["title"], "instructions": row["instructions"], "expires_at": row["expires_at"],
            "revoked_at": row["revoked_at"], "created_at": row["created_at"], "last_used_at": row["last_used_at"],
            "asset_count": int(row["asset_count"] or 0),
        }
        for row in rows
    ]


def list_requested_assets(owner_user_id: str, request_id: str) -> list[dict]:
    with _db() as con:
        owner = con.execute("SELECT id FROM social_asset_requests WHERE id=? AND owner_user_id=?", (request_id, owner_user_id)).fetchone()
        if owner is None:
            raise KeyError(request_id)
        rows = con.execute(
            "SELECT id,original_name,size_bytes,media_type,uploader_name,rights_statement,rights_confirmed_at,status,created_at FROM social_requested_assets WHERE request_id=? ORDER BY created_at DESC",
            (request_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def revoke_asset_request(owner_user_id: str, request_id: str) -> bool:
    with _db() as con:
        cur = con.execute(
            "UPDATE social_asset_requests SET revoked_at=? WHERE id=? AND owner_user_id=? AND revoked_at IS NULL",
            (utc_now(), request_id, owner_user_id),
        )
        return cur.rowcount > 0


def _member(request: Request):
    member, _membership, _profile = require_esp_social_member(request)
    return member


@router.post("/command-center/api/social/asset-requests")
def create_request(body: CreateAssetRequest, request: Request):
    member = _member(request)
    try:
        return create_asset_request(member.user_id, body)
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except KeyError as exc:
        raise HTTPException(404, "Content item not found") from exc


@router.get("/command-center/api/social/asset-requests")
def requests_list(request: Request):
    member = _member(request)
    return {"requests": list_asset_requests(member.user_id)}


@router.get("/command-center/api/social/asset-requests/{request_id}/assets")
def request_assets(request_id: str, request: Request):
    member = _member(request)
    try:
        return {"assets": list_requested_assets(member.user_id, request_id)}
    except KeyError as exc:
        raise HTTPException(404, "Asset request not found") from exc


@router.delete("/command-center/api/social/asset-requests/{request_id}")
def revoke_request(request_id: str, request: Request):
    member = _member(request)
    if not revoke_asset_request(member.user_id, request_id):
        raise HTTPException(404, "Asset request not found")
    return {"revoked": True}


@router.get("/api/public-assets/{token}", include_in_schema=False)
def public_request_info(token: str):
    try:
        return asset_request_snapshot(token)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/api/public-assets/{token}", include_in_schema=False)
async def public_asset_upload(
    token: str,
    file: UploadFile = File(...),
    uploader_name: str = Form("Guest uploader"),
    rights_confirmed: bool = Form(False),
):
    try:
        return await save_requested_asset(token, file, uploader_name=uploader_name, rights_confirmed=rights_confirmed)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


PAGE_CSS = """
:root{--bg:#04050a;--panel:#101421;--line:#ffffff20;--text:#fff;--muted:#b9c1d2;--gold:#efcc77;--good:#77dda8}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% 0,#3d176b,transparent 30%),#04050a;color:var(--text);font-family:Inter,ui-sans-serif,system-ui,sans-serif}.wrap{width:min(760px,calc(100% - 26px));margin:auto;padding:38px 0 60px}.card{border:1px solid var(--line);border-radius:18px;padding:16px;background:#101421e8;margin:10px 0}.muted{color:var(--muted);line-height:1.55}.btn{border:0;border-radius:11px;padding:10px 13px;background:linear-gradient(110deg,var(--gold),#9d70ff);color:#160d1e;font-weight:900;cursor:pointer}input{width:100%;border:1px solid var(--line);border-radius:10px;padding:10px;background:#070912;color:#fff;font:inherit}.notice{display:none;border:1px solid var(--line);border-radius:10px;padding:10px}.notice.show{display:block}
"""

PAGE_SCRIPT = r"""
const token=location.pathname.split('/').pop(),api='/api/public-assets/'+encodeURIComponent(token),$=id=>document.getElementById(id);function msg(v,b=false){const n=$('notice');n.textContent=v;n.className='notice show';n.style.borderColor=b?'#ff8fa355':''}async function load(){try{const r=await fetch(api),d=await r.json();if(!r.ok)throw new Error(d.detail||'Request unavailable');$('title').textContent=d.title;$('instructions').textContent=d.instructions||'Upload the requested source material.';$('limits').textContent=`Accepted image/video/audio/PDF/text files · max ${d.max_mb} MB each · expires ${d.expires_at}`}catch(e){msg(e.message,true);$('form').style.display='none'}}async function upload(){const file=$('file').files[0];if(!file)return msg('Choose a file.',true);if(!$('rights').checked)return msg('You must confirm you own or are authorised to provide this file.',true);const fd=new FormData();fd.append('file',file);fd.append('uploader_name',$('name').value.trim()||'Guest uploader');fd.append('rights_confirmed','true');$('send').disabled=true;try{const r=await fetch(api,{method:'POST',body:fd}),d=await r.json();if(!r.ok)throw new Error(d.detail||'Upload failed');msg(`${d.original_name} uploaded for ESP review. It has not been published or attached to a project.`);$('file').value=''}catch(e){msg(e.message,true)}finally{$('send').disabled=false}}$('send').onclick=upload;load();
"""


@router.get("/upload/social-assets/{token}", response_class=HTMLResponse, include_in_schema=False)
def upload_page(token: str):
    try:
        asset_request_snapshot(token)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><meta name='referrer' content='no-referrer'><title>ESP Asset Upload</title><style>{PAGE_CSS}</style></head><body><main class='wrap'><div class='muted' style='text-transform:uppercase;letter-spacing:.12em;font-size:.72rem'>Elevate Souls Productions · Private Asset Request</div><h1 id='title'>Asset upload</h1><p id='instructions' class='muted'></p><p id='limits' class='muted'></p><div id='notice' class='notice'></div><section id='form' class='card'><input id='name' maxlength='120' placeholder='Your name'><input id='file' type='file' accept='.jpg,.jpeg,.png,.webp,.gif,.mp4,.mov,.webm,.m4v,.mp3,.wav,.m4a,.flac,.aac,.pdf,.txt,.md'><label class='muted' style='display:block;margin:12px 0'><input id='rights' type='checkbox' style='width:auto'> I confirm I own this asset or am authorised to provide it for this ESP content workflow.</label><button id='send' class='btn'>Upload for ESP review</button><p class='muted' style='font-size:.75rem'>Uploads are quarantined as pending review. Uploading does not publish content or add the file to a Creative House project.</p></section></main><script>{PAGE_SCRIPT}</script></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


__all__ = [
    "router", "CreateAssetRequest", "create_asset_request", "asset_request_snapshot",
    "save_requested_asset", "list_asset_requests", "list_requested_assets", "revoke_asset_request",
]
