from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .accounts import AccountStore
from .audit import AuditLedger
from .tenant_storage import ROOT

router = APIRouter(prefix="/privacy", tags=["Member Privacy"])
store = AccountStore()
audit = AuditLedger(store)
COOKIE_NAME = "lss_session"


class DeleteAccountRequest(BaseModel):
    password: str = Field(min_length=10, max_length=512)
    confirmation: str


def _session_user(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    user = store.resolve_session(token)
    if not user:
        raise HTTPException(401, "Sign in required")
    return user


def _safe_rows(
    table: str,
    user_id: str,
    *,
    user_column: str = "user_id",
    omit: set[str] | None = None,
) -> list[dict]:
    omit = omit or set()
    # Table/column values are internal constants from _member_export, never request values.
    try:
        with sqlite3.connect(store.db_path) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                f"SELECT * FROM {table} WHERE {user_column}=?",
                (user_id,),
            ).fetchall()
        return [{key: row[key] for key in row.keys() if key not in omit} for row in rows]
    except sqlite3.OperationalError:
        return []


def _member_export(user: dict, destination: Path) -> None:
    safe_user = {
        key: value
        for key, value in user.items()
        if key not in {"password_salt", "password_hash"}
    }
    data = {
        "account": safe_user,
        "membership_requests": _safe_rows("membership_requests", user["id"], omit={"token_hash"}),
        "usage_events": _safe_rows("usage_events", user["id"]),
        "song_slots": _safe_rows("song_slots", user["id"]),
        "subscription_state": _safe_rows("subscription_state", user["id"]),
        "subscription_payments": _safe_rows("subscription_payments", user["id"]),
        "production_jobs": _safe_rows("studio_jobs", user["id"]),
        "admin_actions_about_account": _safe_rows(
            "admin_audit_log",
            user["id"],
            user_column="subject_user_id",
        ),
    }

    member_projects = (ROOT / "members" / user["id"]).resolve()
    members_root = (ROOT / "members").resolve()
    if members_root not in member_projects.parents:
        raise RuntimeError("Invalid member project path")

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "account-data.json",
            json.dumps(data, indent=2, default=str, ensure_ascii=False),
        )
        if member_projects.exists():
            for path in sorted(p for p in member_projects.rglob("*") if p.is_file()):
                archive.write(
                    path,
                    arcname=f"projects/{path.relative_to(member_projects).as_posix()}",
                )


def _cleanup(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


@router.get("/export")
def export_data(request: Request, background_tasks: BackgroundTasks):
    user = _session_user(request)
    handle = tempfile.NamedTemporaryFile(prefix="lss-member-export-", suffix=".zip", delete=False)
    handle.close()
    target = Path(handle.name)
    try:
        _member_export(user, target)
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(500, f"Could not create member export: {type(exc).__name__}: {exc}") from exc
    audit.append(actor="member-self-service", action="member_data_export", subject_user_id=user["id"])
    background_tasks.add_task(_cleanup, str(target))
    return FileResponse(
        target,
        media_type="application/zip",
        filename="ESP_Live_Sound_Studio_Member_Data.zip",
        background=background_tasks,
    )


@router.delete("/account")
def delete_account(payload: DeleteAccountRequest, request: Request, response: Response):
    user = _session_user(request)
    if payload.confirmation.strip().upper() != "DELETE MY ACCOUNT":
        raise HTTPException(400, "Type DELETE MY ACCOUNT to confirm permanent deletion")
    authenticated = store.authenticate(user["email"], payload.password)
    if not authenticated or authenticated["id"] != user["id"]:
        raise HTTPException(403, "Password confirmation failed")

    member_projects = (ROOT / "members" / user["id"]).resolve()
    members_root = (ROOT / "members").resolve()
    if members_root not in member_projects.parents:
        raise HTTPException(500, "Invalid member storage path")

    # Audit record intentionally survives account deletion; it contains only the opaque user id,
    # plan/status and no password, email or uploaded content.
    audit.append(
        actor="member-self-service",
        action="account_deleted",
        subject_user_id=user["id"],
        details={"plan_id": user.get("plan_id"), "status": user.get("status")},
    )

    try:
        with store._connect() as con:
            con.execute("DELETE FROM users WHERE id=?", (user["id"],))
        if member_projects.exists():
            shutil.rmtree(member_projects)
    except Exception as exc:
        raise HTTPException(500, f"Account deletion failed: {type(exc).__name__}: {exc}") from exc

    response.delete_cookie(COOKIE_NAME)
    return {
        "deleted": True,
        "message": "Your Live Sound Studio account and private project storage were deleted.",
    }
