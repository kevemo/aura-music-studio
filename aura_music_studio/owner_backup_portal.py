from __future__ import annotations

import os
import secrets
from html import escape
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .backup import StudioBackupManager
from .branding import PRODUCT_FULL_NAME

router = APIRouter()
ADMIN_COOKIE = "lss_admin_session"


def _authorized(request: Request) -> bool:
    configured = os.getenv("LSS_ADMIN_KEY") or ""
    supplied = request.cookies.get(ADMIN_COOKIE) or ""
    return bool(configured and supplied and secrets.compare_digest(configured, supplied))


def _root() -> Path:
    root = Path(os.getenv("LSS_BACKUP_DIR", "backups")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _files() -> list[Path]:
    return sorted(
        [p for p in _root().iterdir() if p.is_file() and (p.suffix.lower() == ".zip" or p.name.lower().endswith(".zip.age"))],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _page(body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>ESP Backups — {escape(PRODUCT_FULL_NAME)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;font-family:Inter,system-ui,sans-serif;color:#fff}}.wrap{{max-width:1050px;margin:auto;padding:24px}}.top,.row{{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}}a{{color:inherit}}.btn,button{{display:inline-block;border:1px solid #553363;border-radius:10px;background:#22122d;color:#fff;padding:10px 14px;text-decoration:none;font-weight:850;cursor:pointer}}.primary{{background:linear-gradient(135deg,#ffe7a6,#e8ba59,#b67a23);color:#160b18}}.card{{background:#150b1d;border:1px solid #50305d;border-radius:18px;padding:20px;margin:16px 0}}.muted{{color:#cdbfd4;line-height:1.55}}.good{{color:#86e0a8}}.warn{{color:#ffd07a}}input[type=text]{{width:100%;padding:11px;border-radius:9px;border:1px solid #523263;background:#09050d;color:#fff}}label{{display:block;margin:10px 0}}code{{word-break:break-all}}.file{{border-top:1px solid #ffffff15;padding:12px 0}}.file:first-child{{border-top:0}}
</style></head><body><div class='wrap'>{body}</div></body></html>""")


@router.get("/owner/backups", response_class=HTMLResponse)
def backup_dashboard(request: Request):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    rows = []
    for path in _files():
        size = path.stat().st_size
        label = f"{size / (1024 ** 3):.2f} GB" if size >= 1024 ** 3 else f"{size / (1024 ** 2):.1f} MB"
        rows.append(
            f"<div class='file'><div class='row'><div><b>{escape(path.name)}</b><br><span class='muted'>{escape(label)}</span></div><a class='btn' href='/owner/backups/download/{escape(path.name, quote=True)}'>Download</a></div></div>"
        )
    listing = "".join(rows) if rows else "<p class='muted'>No Studio backups have been created yet.</p>"
    body = f"""<div class='top'><div><div style='color:#ffe29a;font-weight:900'>ESP OWNER CONTROL</div><h1>Backup & Migration</h1><p class='muted'>Portable copies of the Studio database and private project tree. Deployment secrets are deliberately excluded.</p></div><a class='btn' href='/owner/dashboard'>Owner Dashboard</a></div>
<div class='card'><h2>Create verified backup</h2><p class='muted'>The account/billing/job database uses SQLite's backup API. Project files are checksummed as they enter the archive. For the cleanest full-project snapshot, avoid starting new uploads/renders while this runs.</p><form method='post' action='/owner/backups/create'>
<label><input type='checkbox' name='include_outputs' value='true' checked> Include finished masters/stems/outputs</label>
<label><input type='checkbox' name='include_work' value='true' checked> Include work files, revisions and generated takes</label>
<label>Optional <code>age</code> public recipient (encrypts the archive; do not enter a private key here)<input type='text' name='age_recipient' placeholder='age1...'></label>
<button class='primary' type='submit'>Create Backup</button></form></div>
<div class='card'><h2>Stored owner backups</h2>{listing}</div>
<div class='card'><h2>Restore safety</h2><p class='warn'>Restore is intentionally not exposed as a one-click web action. Stop the Studio and worker first, then use <code>aura restore-backup ... --offline-confirmed</code>. Aura verifies every checksum and preserves the old database/project tree before replacement.</p></div>"""
    return _page(body)


@router.post("/owner/backups/create", response_class=HTMLResponse)
def create_backup(
    request: Request,
    include_outputs: str | None = Form(default=None),
    include_work: str | None = Form(default=None),
    age_recipient: str = Form(default=""),
):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    try:
        result = StudioBackupManager().create(
            include_outputs=bool(include_outputs),
            include_work=bool(include_work),
            age_recipient=age_recipient.strip() or None,
        )
        archive = result.get("encrypted_backup") or result.get("backup")
        body = f"""<div class='card'><h1>Backup complete</h1><p class='good'>Aura created and verified the Studio archive.</p><p><code>{escape(str(archive))}</code></p><p class='muted'>Database + project state: included. Deployment .env/DDNS/SMTP/payment/provider secrets: excluded.</p><a class='btn primary' href='/owner/backups'>Back to Backups</a></div>"""
    except Exception as exc:
        body = f"""<div class='card'><h1>Backup failed</h1><p style='color:#ff9aa9'>{escape(type(exc).__name__ + ': ' + str(exc))}</p><a class='btn' href='/owner/backups'>Back to Backups</a></div>"""
    return _page(body)


@router.get("/owner/backups/download/{filename}")
def download_backup(filename: str, request: Request):
    if not _authorized(request):
        return RedirectResponse("/owner", status_code=303)
    safe = Path(filename).name
    if safe != filename or not (safe.lower().endswith(".zip") or safe.lower().endswith(".zip.age")):
        return HTMLResponse("Not found", status_code=404)
    root = _root()
    target = (root / safe).resolve()
    if root not in target.parents or not target.is_file():
        return HTMLResponse("Not found", status_code=404)
    return FileResponse(target, filename=target.name, media_type="application/octet-stream", headers={"Cache-Control": "no-store"})
