from __future__ import annotations

import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from .accounts import AccountStore
from .owner_identity import actor_from_request, admin_authorized

router = APIRouter(tags=["Workspace Theme"])
MEMBER_COOKIE = "lss_session"
PREVIEW_MINUTES = 30
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

DEFAULT_THEME: dict[str, Any] = {
    "background": "#050308",
    "surface": "#160d1d",
    "surface_alt": "#21102c",
    "accent": "#e7b953",
    "accent_alt": "#a53add",
    "text": "#fffaf0",
    "muted": "#cdbfd4",
    "radius_px": 18,
    "font_scale": 1.0,
    "density": "comfortable",
    "motion": "full",
    "background_style": "cosmic",
    "font_style": "system",
    "aura_glow": "balanced",
}

ENUMS = {
    "density": {"compact", "comfortable", "spacious"},
    "motion": {"reduced", "balanced", "full"},
    "background_style": {"solid", "gradient", "cosmic", "minimal"},
    "font_style": {"system", "rounded", "serif", "mono"},
    "aura_glow": {"subtle", "balanced", "radiant"},
}
COLOR_FIELDS = {"background", "surface", "surface_alt", "accent", "accent_alt", "text", "muted"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _loads(raw: str | None, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else dict(fallback or {})
    except Exception:
        return dict(fallback or {})


def validate_theme_patch(patch: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise ValueError("Theme changes must be an object")
    unknown = set(patch) - set(DEFAULT_THEME)
    if unknown:
        raise ValueError(f"Unsupported theme fields: {', '.join(sorted(unknown))}")
    clean: dict[str, Any] = {}
    for key, value in patch.items():
        if key in COLOR_FIELDS:
            text = str(value or "").strip()
            if not HEX_COLOR.fullmatch(text):
                raise ValueError(f"{key} must be a six-digit hex colour")
            clean[key] = text.lower()
        elif key == "radius_px":
            number = int(value)
            if number < 0 or number > 40:
                raise ValueError("radius_px must be between 0 and 40")
            clean[key] = number
        elif key == "font_scale":
            number = float(value)
            if number < 0.85 or number > 1.3:
                raise ValueError("font_scale must be between 0.85 and 1.30")
            clean[key] = round(number, 3)
        elif key in ENUMS:
            text = str(value or "").strip().lower()
            if text not in ENUMS[key]:
                raise ValueError(f"Unsupported {key}: {text}")
            clean[key] = text
    return clean


def normalize_theme(value: dict[str, Any] | None) -> dict[str, Any]:
    theme = dict(DEFAULT_THEME)
    if value:
        theme.update(validate_theme_patch(value))
    return theme


def theme_css(theme: dict[str, Any]) -> str:
    value = normalize_theme(theme)
    font = {
        "system": "Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif",
        "rounded": "ui-rounded,'Arial Rounded MT Bold',Inter,system-ui,sans-serif",
        "serif": "Georgia,'Times New Roman',serif",
        "mono": "ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace",
    }[value["font_style"]]
    density_gap = {"compact": "0.78", "comfortable": "1", "spacious": "1.18"}[value["density"]]
    glow = {"subtle": "0.16", "balanced": "0.32", "radiant": "0.56"}[value["aura_glow"]]
    motion = {"reduced": "0.01ms", "balanced": "180ms", "full": "280ms"}[value["motion"]]
    background = {
        "solid": f"{value['background']}",
        "minimal": f"linear-gradient(180deg,{value['background']},{value['surface']})",
        "gradient": f"radial-gradient(circle at 15% 0%,{value['accent_alt']}33,transparent 38%),linear-gradient(180deg,{value['surface']},{value['background']} 68%)",
        "cosmic": f"radial-gradient(circle at 50% -12%,{value['accent_alt']}42,transparent 34%),radial-gradient(circle at 92% 30%,{value['accent']}24,transparent 25%),linear-gradient(180deg,{value['surface']} 0%,{value['background']} 72%)",
    }[value["background_style"]]
    return (
        ":root{"
        f"--workspace-bg:{value['background']};--workspace-surface:{value['surface']};"
        f"--workspace-surface-alt:{value['surface_alt']};--workspace-accent:{value['accent']};"
        f"--workspace-accent-alt:{value['accent_alt']};--workspace-text:{value['text']};"
        f"--workspace-muted:{value['muted']};--workspace-radius:{value['radius_px']}px;"
        f"--workspace-font-scale:{value['font_scale']};--workspace-density:{density_gap};"
        f"--workspace-motion:{motion};--workspace-aura-glow:{glow};"
        "}"
        "html{font-size:calc(16px * var(--workspace-font-scale));}"
        f"body{{font-family:{font}!important;background:{background}!important;color:var(--workspace-text)!important;}}"
        ".card,.panel,.tile,.form-card,.side,.inspector{border-radius:var(--workspace-radius)!important;}"
        ".card,.panel,.tile,.form-card{background-color:var(--workspace-surface)!important;}"
        "button,.btn,input,select,textarea{border-radius:calc(var(--workspace-radius) * .72)!important;}"
        "button,.btn,.card,.panel,.tile,input,select,textarea{transition-duration:var(--workspace-motion)!important;}"
        ".aura-stage,.aura-avatar,.orb{filter:drop-shadow(0 0 22px color-mix(in srgb,var(--workspace-accent-alt) calc(var(--workspace-aura-glow) * 100%),transparent));}"
        "[data-workspace-density]{gap:calc(1rem * var(--workspace-density));}"
    )


class ThemePreviewRequest(BaseModel):
    changes: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="Aura workspace customization", max_length=500)


class ThemePreviewDecision(BaseModel):
    preview_id: str = Field(min_length=8, max_length=80)


class WorkspaceThemeStore:
    def __init__(self, db_path: str | Path | None = None):
        configured = db_path or "data/live_sound_studio.sqlite3"
        self.db_path = Path(configured)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS workspace_theme_profiles (
                    subject_key TEXT PRIMARY KEY,
                    theme_json TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workspace_theme_previews (
                    id TEXT PRIMARY KEY,
                    subject_key TEXT NOT NULL,
                    proposed_json TEXT NOT NULL,
                    previous_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    decided_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_workspace_theme_previews_subject
                    ON workspace_theme_previews(subject_key, created_at DESC);
                CREATE TABLE IF NOT EXISTS workspace_theme_history (
                    id TEXT PRIMARY KEY,
                    subject_key TEXT NOT NULL,
                    action TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    actor_label TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_workspace_theme_history_subject
                    ON workspace_theme_history(subject_key, created_at DESC);
                """
            )

    def current(self, subject_key: str) -> dict[str, Any]:
        with self._connect() as con:
            row = con.execute(
                "SELECT theme_json,version,updated_at FROM workspace_theme_profiles WHERE subject_key=?",
                (subject_key,),
            ).fetchone()
        if not row:
            return {"theme": dict(DEFAULT_THEME), "version": 0, "updated_at": None}
        return {
            "theme": normalize_theme(_loads(row["theme_json"])),
            "version": int(row["version"]),
            "updated_at": row["updated_at"],
        }

    def create_preview(self, subject_key: str, changes: dict[str, Any], reason: str) -> dict[str, Any]:
        patch = validate_theme_patch(changes)
        current = self.current(subject_key)
        proposed = {**current["theme"], **patch}
        preview_id = secrets.token_urlsafe(18)
        created = _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO workspace_theme_previews
                   (id,subject_key,proposed_json,previous_json,reason,status,created_at,expires_at)
                   VALUES (?,?,?,?,?,'pending',?,?)""",
                (
                    preview_id,
                    subject_key,
                    json.dumps(proposed, sort_keys=True),
                    json.dumps(current["theme"], sort_keys=True),
                    (reason or "Aura workspace customization").strip()[:500],
                    _iso(created),
                    _iso(created + timedelta(minutes=PREVIEW_MINUTES)),
                ),
            )
        return {
            "preview_id": preview_id,
            "status": "pending",
            "theme": proposed,
            "css": theme_css(proposed),
            "expires_at": _iso(created + timedelta(minutes=PREVIEW_MINUTES)),
            "requires_confirmation": True,
        }

    def _preview(self, subject_key: str, preview_id: str) -> dict[str, Any]:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM workspace_theme_previews WHERE id=? AND subject_key=?",
                (preview_id, subject_key),
            ).fetchone()
        if not row:
            raise ValueError("Theme preview not found")
        return dict(row)

    def confirm(self, subject_key: str, preview_id: str, actor_label: str) -> dict[str, Any]:
        preview = self._preview(subject_key, preview_id)
        if preview["status"] != "pending":
            raise ValueError("Theme preview has already been decided")
        if preview["expires_at"] <= _iso():
            with self._connect() as con:
                con.execute(
                    "UPDATE workspace_theme_previews SET status='expired',decided_at=? WHERE id=?",
                    (_iso(), preview_id),
                )
            raise ValueError("Theme preview has expired")
        proposed = normalize_theme(_loads(preview["proposed_json"]))
        previous = normalize_theme(_loads(preview["previous_json"]))
        now = _iso()
        with self._connect() as con:
            existing = con.execute(
                "SELECT version FROM workspace_theme_profiles WHERE subject_key=?",
                (subject_key,),
            ).fetchone()
            version = int(existing["version"]) + 1 if existing else 1
            con.execute(
                """INSERT INTO workspace_theme_profiles(subject_key,theme_json,version,updated_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(subject_key) DO UPDATE SET
                     theme_json=excluded.theme_json,version=excluded.version,updated_at=excluded.updated_at""",
                (subject_key, json.dumps(proposed, sort_keys=True), version, now),
            )
            con.execute(
                "UPDATE workspace_theme_previews SET status='confirmed',decided_at=? WHERE id=?",
                (now, preview_id),
            )
            con.execute(
                """INSERT INTO workspace_theme_history
                   (id,subject_key,action,before_json,after_json,actor_label,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    uuid4().hex,
                    subject_key,
                    "confirm",
                    json.dumps(previous, sort_keys=True),
                    json.dumps(proposed, sort_keys=True),
                    actor_label[:160],
                    now,
                ),
            )
        return {"status": "confirmed", "theme": proposed, "version": version, "css": theme_css(proposed)}

    def discard(self, subject_key: str, preview_id: str) -> dict[str, Any]:
        preview = self._preview(subject_key, preview_id)
        if preview["status"] != "pending":
            raise ValueError("Theme preview has already been decided")
        now = _iso()
        with self._connect() as con:
            con.execute(
                "UPDATE workspace_theme_previews SET status='reverted',decided_at=? WHERE id=?",
                (now, preview_id),
            )
        current = self.current(subject_key)
        return {"status": "reverted", **current, "css": theme_css(current["theme"])}

    def revert_last(self, subject_key: str, actor_label: str) -> dict[str, Any]:
        with self._connect() as con:
            row = con.execute(
                """SELECT * FROM workspace_theme_history
                   WHERE subject_key=? AND action IN ('confirm','revert')
                   ORDER BY created_at DESC LIMIT 1""",
                (subject_key,),
            ).fetchone()
        if not row:
            raise ValueError("No saved theme change is available to revert")
        current = self.current(subject_key)
        target = normalize_theme(_loads(row["before_json"]))
        now = _iso()
        version = int(current["version"]) + 1
        with self._connect() as con:
            con.execute(
                """INSERT INTO workspace_theme_profiles(subject_key,theme_json,version,updated_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(subject_key) DO UPDATE SET
                     theme_json=excluded.theme_json,version=excluded.version,updated_at=excluded.updated_at""",
                (subject_key, json.dumps(target, sort_keys=True), version, now),
            )
            con.execute(
                """INSERT INTO workspace_theme_history
                   (id,subject_key,action,before_json,after_json,actor_label,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    uuid4().hex,
                    subject_key,
                    "revert",
                    json.dumps(current["theme"], sort_keys=True),
                    json.dumps(target, sort_keys=True),
                    actor_label[:160],
                    now,
                ),
            )
        return {"status": "reverted", "theme": target, "version": version, "css": theme_css(target)}

    def history(self, subject_key: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT action,actor_label,created_at,before_json,after_json
                   FROM workspace_theme_history WHERE subject_key=?
                   ORDER BY created_at DESC LIMIT ?""",
                (subject_key, max(1, min(int(limit), 50))),
            ).fetchall()
        return [
            {
                "action": row["action"],
                "actor_label": row["actor_label"],
                "created_at": row["created_at"],
                "before": normalize_theme(_loads(row["before_json"])),
                "after": normalize_theme(_loads(row["after_json"])),
            }
            for row in rows
        ]


_accounts = AccountStore()
themes = WorkspaceThemeStore(_accounts.db_path)


def _member_subject(request: Request) -> tuple[str, str]:
    member = getattr(request.state, "member", None)
    if member:
        return f"member:{member.user_id}", f"Member {member.user_id}"
    token = request.cookies.get(MEMBER_COOKIE)
    user = _accounts.resolve_session(token)
    if not user or user.get("status") != "active":
        raise HTTPException(401, "Active membership required")
    return f"member:{user['id']}", f"Member {user['id']}"


def _owner_subject(request: Request) -> tuple[str, str]:
    if not admin_authorized(request):
        raise HTTPException(401, "Owner authorization required")
    actor = actor_from_request(request)
    if not actor:
        raise HTTPException(409, "Select Kev or Mary before customizing the owner workspace")
    return f"owner:{actor['id']}", actor["audit_name"]


def subject_for_request(request: Request) -> tuple[str, str]:
    if request.url.path.startswith("/owner"):
        return _owner_subject(request)
    return _member_subject(request)


def _current_payload(subject_key: str) -> dict[str, Any]:
    current = themes.current(subject_key)
    return {**current, "css": theme_css(current["theme"])}


@router.get("/workspace/theme")
def get_workspace_theme(request: Request):
    subject_key, _actor = subject_for_request(request)
    return _current_payload(subject_key)


@router.post("/workspace/theme/preview")
def preview_workspace_theme(request: Request, payload: ThemePreviewRequest):
    subject_key, _actor = subject_for_request(request)
    try:
        return themes.create_preview(subject_key, payload.changes, payload.reason)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/workspace/theme/confirm")
def confirm_workspace_theme(request: Request, payload: ThemePreviewDecision):
    subject_key, actor = subject_for_request(request)
    try:
        return themes.confirm(subject_key, payload.preview_id, actor)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/workspace/theme/discard")
def discard_workspace_theme(request: Request, payload: ThemePreviewDecision):
    subject_key, _actor = subject_for_request(request)
    try:
        return themes.discard(subject_key, payload.preview_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/workspace/theme/revert")
def revert_workspace_theme(request: Request):
    subject_key, actor = subject_for_request(request)
    try:
        return themes.revert_last(subject_key, actor)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/workspace/theme/history")
def workspace_theme_history(request: Request, limit: int = 20):
    subject_key, _actor = subject_for_request(request)
    return {"history": themes.history(subject_key, limit)}


@router.get("/owner/workspace/theme")
def get_owner_workspace_theme(request: Request):
    subject_key, _actor = _owner_subject(request)
    return _current_payload(subject_key)


@router.post("/owner/workspace/theme/preview")
def preview_owner_workspace_theme(request: Request, payload: ThemePreviewRequest):
    subject_key, _actor = _owner_subject(request)
    try:
        return themes.create_preview(subject_key, payload.changes, payload.reason)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/owner/workspace/theme/confirm")
def confirm_owner_workspace_theme(request: Request, payload: ThemePreviewDecision):
    subject_key, actor = _owner_subject(request)
    try:
        return themes.confirm(subject_key, payload.preview_id, actor)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/owner/workspace/theme/discard")
def discard_owner_workspace_theme(request: Request, payload: ThemePreviewDecision):
    subject_key, _actor = _owner_subject(request)
    try:
        return themes.discard(subject_key, payload.preview_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/owner/workspace/theme/revert")
def revert_owner_workspace_theme(request: Request):
    subject_key, actor = _owner_subject(request)
    try:
        return themes.revert_last(subject_key, actor)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/owner/workspace/theme/history")
def owner_workspace_theme_history(request: Request, limit: int = 20):
    subject_key, _actor = _owner_subject(request)
    return {"history": themes.history(subject_key, limit)}


class WorkspaceThemeMiddleware(BaseHTTPMiddleware):
    """Apply only validated design tokens to authenticated member/owner HTML.

    The theme system never stores or executes arbitrary CSS/JavaScript. Aura can propose token
    changes, the browser can preview returned safe CSS, and the profile changes only after an
    explicit confirm call. Kev and Mary are separate owner subjects despite sharing owner data.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if response.status_code >= 400:
            return response
        if "text/html" not in (response.headers.get("content-type") or "").lower():
            return response
        try:
            if request.url.path.startswith("/owner"):
                subject_key, _actor = _owner_subject(request)
            else:
                subject_key, _actor = _member_subject(request)
        except HTTPException:
            return response

        current = themes.current(subject_key)
        css = theme_css(current["theme"])
        chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8"))
        raw = b"".join(chunks)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return response
        marker = "<style id='workspace-theme-tokens'>" + escape(css, quote=False) + "</style>"
        # CSS is generated only from validated tokens; keep it as CSS rather than HTML-escaped text.
        marker = f"<style id='workspace-theme-tokens'>{css}</style>"
        if "workspace-theme-tokens" not in text:
            if "</head>" in text:
                text = text.replace("</head>", marker + "</head>", 1)
            elif "</body>" in text:
                text = text.replace("</body>", marker + "</body>", 1)
            else:
                text = marker + text
        headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
        return HTMLResponse(text, status_code=response.status_code, headers=headers)
