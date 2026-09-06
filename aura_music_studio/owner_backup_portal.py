from __future__ import annotations

import json
import os
from html import escape
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .backup import StudioBackupManager
from .backup_scheduler import BackupScheduler
from .branding import PRODUCT_FULL_NAME
from .owner_auth import owner_authorized
from .protected_data_auth import authorize_protected_action
from .protected_data_portal import router as protected_data_router
from .studio_settings import StudioSettings

router = APIRouter()
# Mount the step-up surface before the Recovery Vault routes are copied into the
# canonical production application.
router.include_router(protected_data_router)
manager = StudioBackupManager()
settings = StudioSettings()


def _authorized(request: Request, action: str | None = None) -> bool:
    """Compatibility owner check plus protected-action admission.

    The no-action form is retained for the shared owner-auth bridge contract and tests only.
    Every live Recovery Vault route supplies an explicit action and therefore requires both
    a valid opaque Owner session and the independent Protected Data step-up/audit admission.
    """
    if not owner_authorized(request):
        return False
    if action is None:
        return True
    try:
        return authorize_protected_action(request, action) is not None
    except Exception:
        # Protected consequential operations fail closed if the audit ledger cannot be written.
        return False


def _locked() -> RedirectResponse:
    response = RedirectResponse("/owner/protected-data", status_code=303)
    response.headers["Cache-Control"] = "no-store"
    return response


def _root() -> Path:
    root = Path(os.getenv("LSS_BACKUP_DIR", "backups")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _files() -> list[Path]:
    return sorted(
        [
            p
            for p in _root().iterdir()
            if p.is_file()
            and (p.suffix.lower() == ".zip" or p.name.lower().endswith(".zip.age"))
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _scheduler_status() -> dict:
    scheduler = BackupScheduler()
    status: dict = {}
    if scheduler.status_path.is_file():
        try:
            value = json.loads(scheduler.status_path.read_text(encoding="utf-8"))
            status = value if isinstance(value, dict) else {}
        except Exception:
            status = {}
    return {"configuration": scheduler.configuration(), "status": status}


def _page(body: str, *, status_code: int = 200) -> HTMLResponse:
    response = HTMLResponse(
        f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Recovery Vault — {escape(PRODUCT_FULL_NAME)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#09050d;font-family:Inter,system-ui,sans-serif;color:#fff}}.wrap{{max-width:1050px;margin:auto;padding:24px}}.top,.row{{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}}a{{color:inherit}}.btn,button{{display:inline-block;border:1px solid #553363;border-radius:10px;background:#22122d;color:#fff;padding:10px 14px;text-decoration:none;font-weight:850;cursor:pointer}}.primary{{background:linear-gradient(135deg,#ffe7a6,#e8ba59,#b67a23);color:#160b18}}.card{{background:#150b1d;border:1px solid #50305d;border-radius:18px;padding:20px;margin:16px 0}}.muted{{color:#cdbfd4;line-height:1.55}}.good{{color:#86e0a8}}.warn{{color:#ffd07a}}.bad{{color:#ff9aa9}}input[type=text],input[type=number]{{width:100%;padding:11px;border-radius:9px;border:1px solid #523263;background:#09050d;color:#fff}}label{{display:block;margin:10px 0}}code{{word-break:break-all}}.file{{border-top:1px solid #ffffff15;padding:12px 0}}.file:first-child{{border-top:0}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}@media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><div class='wrap'>{body}</div></body></html>""",
        status_code=status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@router.get("/owner/backups", response_class=HTMLResponse)
def backup_dashboard(request: Request):
    if not _authorized(request, "recovery_vault:view"):
        return _locked()
    rows = []
    for path in _files():
        size = path.stat().st_size
        label = f"{size / (1024 ** 3):.2f} GB" if size >= 1024 ** 3 else f"{size / (1024 ** 2):.1f} MB"
        encrypted = path.name.lower().endswith(".age")
        state = "encrypted" if encrypted else "legacy plaintext"
        state_class = "good" if encrypted else "warn"
        rows.append(
            f"<div class='file'><div class='row'><div><b>{escape(path.name)}</b><br><span class='muted'>{escape(label)}</span> · <span class='{state_class}'>{state}</span></div><a class='btn' href='/owner/backups/download/{escape(path.name, quote=True)}'>Download</a></div></div>"
        )
    listing = "".join(rows) if rows else "<p class='muted'>No Recovery Vault backups have been created yet.</p>"

    scheduler = _scheduler_status()
    config = scheduler["configuration"]
    state = scheduler["status"]
    last = state.get("last_result") if isinstance(state.get("last_result"), dict) else {}
    requested = bool(config["enabled"])
    operational = bool(config.get("operational"))
    if operational:
        status_label, status_class = "Operational", "good"
    elif requested:
        status_label, status_class = "Blocked — encryption not configured", "bad"
    else:
        status_label, status_class = "Disabled", "warn"

    last_message = "No automatic encrypted backup has run yet."
    if last:
        if last.get("ok") is True:
            last_message = f"Last automatic backup succeeded: {last.get('backup') or 'encrypted archive created'}"
        elif last.get("ok") is False:
            last_message = f"Last automatic backup blocked/failed: {last.get('error') or last.get('reason') or 'unknown error'}"
        elif last.get("reason"):
            last_message = str(last.get("reason"))

    checked_enabled = "checked" if requested else ""
    checked_outputs = "checked" if config["include_outputs"] else ""
    checked_work = "checked" if config["include_work"] else ""
    encryption_ready = bool(config.get("encryption_ready"))

    body = f"""<div class='top'><div><div style='color:#ffe29a;font-weight:900'>PROTECTED DATA AUTHORITY</div><h1>Recovery Vault</h1><p class='muted'>Portable encrypted copies of the platform database and private project tree. Deployment secrets are deliberately excluded.</p></div><div><a class='btn' href='/owner/dashboard'>Owner Dashboard</a><form method='post' action='/owner/protected-data/lock' style='display:inline;margin-left:8px'><button type='submit'>Lock Protected Data</button></form></div></div>
<div class='card'><div class='row'><div><h2>Automatic encrypted backups</h2><p class='muted'>Rhiannon rotates encrypted backups on ESP-controlled storage. A public <code>age</code> recipient must be configured before the schedule can operate.</p></div><b class='{status_class}'>{escape(status_label)}</b></div>
<div class='grid'><div><b>Interval</b><h3>{config['interval_hours']} hours</h3></div><div><b>Retention</b><h3>{config['retention_count']} archives</h3></div><div><b>Encryption</b><h3>{'AGE ready' if encryption_ready else 'Not configured'}</h3></div></div>
<p class='muted'>{escape(last_message)}</p>
<form method='post' action='/owner/backups/schedule'>
<label><input type='checkbox' name='enabled' value='true' {checked_enabled}> Enable automatic encrypted backup rotation</label>
<label>Run every N hours<input type='number' name='interval_hours' min='1' max='720' value='{config['interval_hours']}' required></label>
<label>Keep newest N automatic archives<input type='number' name='retention_count' min='1' max='365' value='{config['retention_count']}' required></label>
<label><input type='checkbox' name='include_outputs' value='true' {checked_outputs}> Include finished masters/stems</label>
<label><input type='checkbox' name='include_work' value='true' {checked_work}> Include work files/generated takes</label>
<button class='primary' type='submit'>Save Recovery Schedule</button></form>
<p class='muted'>Scheduled encryption uses <code>LSS_BACKUP_AGE_RECIPIENT</code> from deployment secrets. The private decryption identity is never stored by the dashboard.</p></div>
<div class='card'><h2>Create encrypted backup now</h2><p class='muted'>The account/billing/job database uses SQLite's backup API. Project files are checksummed as they enter the archive. The resulting plaintext ZIP is removed after successful <code>age</code> encryption.</p><form method='post' action='/owner/backups/create'>
<label><input type='checkbox' name='include_outputs' value='true' checked> Include finished masters/stems/outputs</label>
<label><input type='checkbox' name='include_work' value='true' checked> Include work files, revisions and generated takes</label>
<label>Optional one-time <code>age</code> public recipient override<input type='text' name='age_recipient' placeholder='age1...'></label>
<button class='primary' type='submit'>Create Encrypted Backup</button></form><p class='muted'>If no override is supplied, the deployment <code>LSS_BACKUP_AGE_RECIPIENT</code> is used. If neither exists, creation fails closed.</p></div>
<div class='card'><h2>Stored owner backups</h2>{listing}</div>
<div class='card'><h2>Restore safety</h2><p class='warn'>Restore is intentionally not exposed as a one-click web action. Stop the Studio and workers first, then use <code>aura restore-backup ... --offline-confirmed</code>. Checksums are verified and the existing database/project tree is preserved before replacement.</p></div>"""
    return _page(body)


@router.post("/owner/backups/schedule")
def save_backup_schedule(
    request: Request,
    enabled: str | None = Form(default=None),
    interval_hours: int = Form(24),
    retention_count: int = Form(7),
    include_outputs: str | None = Form(default=None),
    include_work: str | None = Form(default=None),
):
    if not _authorized(request, "recovery_vault:update_schedule"):
        return _locked()
    if enabled and not (os.getenv("LSS_BACKUP_AGE_RECIPIENT") or "").strip():
        return _page(
            """<div class='card'><h1>Schedule not enabled</h1><p class='bad'>Encrypted automatic backups require a configured <code>LSS_BACKUP_AGE_RECIPIENT</code>.</p><p class='muted'>No plaintext fallback was enabled.</p><a class='btn' href='/owner/backups'>Back to Recovery Vault</a></div>""",
            status_code=409,
        )
    try:
        settings.update_many(
            {
                "auto_backup_enabled": bool(enabled),
                "auto_backup_interval_hours": interval_hours,
                "auto_backup_keep": retention_count,
                "auto_backup_include_outputs": bool(include_outputs),
                "auto_backup_include_work": bool(include_work),
            },
            updated_by="Protected Data Recovery Vault",
        )
    except (ValueError, KeyError):
        return RedirectResponse("/owner/backups", status_code=303)
    return RedirectResponse("/owner/backups", status_code=303)


@router.post("/owner/backups/create", response_class=HTMLResponse)
def create_backup(
    request: Request,
    include_outputs: str | None = Form(default=None),
    include_work: str | None = Form(default=None),
    age_recipient: str = Form(default=""),
):
    if not _authorized(request, "recovery_vault:create_backup"):
        return _locked()
    recipient = age_recipient.strip() or (os.getenv("LSS_BACKUP_AGE_RECIPIENT") or "").strip()
    if not recipient:
        return _page(
            """<div class='card'><h1>Backup blocked</h1><p class='bad'>No public <code>age</code> recipient is configured.</p><p class='muted'>Recovery Vault does not fall back to plaintext backup creation.</p><a class='btn' href='/owner/backups'>Back to Recovery Vault</a></div>""",
            status_code=409,
        )
    try:
        result = manager.create(
            include_outputs=bool(include_outputs),
            include_work=bool(include_work),
            age_recipient=recipient,
            keep_plain_when_encrypted=False,
        )
        archive = result.get("encrypted_backup")
        if not archive or result.get("backup"):
            raise RuntimeError("Recovery Vault did not finish in encrypted-only state")
        body = f"""<div class='card'><h1>Encrypted backup complete</h1><p class='good'>Rhiannon created and verified the encrypted Recovery Vault archive.</p><p><code>{escape(str(archive))}</code></p><p class='muted'>Database + project state: included. Deployment .env/DDNS/SMTP/payment/provider secrets: excluded. Plaintext archive: removed after encryption.</p><a class='btn primary' href='/owner/backups'>Back to Recovery Vault</a></div>"""
    except Exception as exc:
        body = f"""<div class='card'><h1>Backup failed</h1><p class='bad'>{escape(type(exc).__name__ + ': ' + str(exc))}</p><a class='btn' href='/owner/backups'>Back to Recovery Vault</a></div>"""
    return _page(body)


@router.get("/owner/backups/download/{filename}")
def download_backup(filename: str, request: Request):
    if not _authorized(request, "recovery_vault:download_backup"):
        return _locked()
    safe = Path(filename).name
    if safe != filename or not (
        safe.lower().endswith(".zip") or safe.lower().endswith(".zip.age")
    ):
        return HTMLResponse("Not found", status_code=404, headers={"Cache-Control": "no-store"})
    root = _root()
    target = (root / safe).resolve()
    if root not in target.parents or not target.is_file():
        return HTMLResponse("Not found", status_code=404, headers={"Cache-Control": "no-store"})
    return FileResponse(
        target,
        filename=target.name,
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )
