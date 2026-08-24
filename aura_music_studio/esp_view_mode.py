from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from html import escape
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, Response

from .accounts import AccountStore
from .membership import MembershipService

router = APIRouter()
store = AccountStore()
memberships = MembershipService(store)
MEMBER_COOKIE = "lss_session"
VALID_VIEW_MODES = {"creator", "agent"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ViewModeRequest(BaseModel):
    view: str


class EspViewModeStore:
    def __init__(self, account_store: AccountStore | None = None):
        self.accounts = account_store or store
        self.db_path = self.accounts.db_path
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS esp_view_preferences (
                    user_id TEXT PRIMARY KEY,
                    view_mode TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )

    def membership(self, user_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT user_id,status,roles FROM esp_memberships WHERE user_id=?",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        if item.get("status") not in {"active", "owner"}:
            return None
        if item.get("roles") not in {"creator", "agent", "both", "owner"}:
            return None
        return item

    @staticmethod
    def allowed_views(role: str) -> list[str]:
        if role in {"agent", "both", "owner"}:
            return ["agent", "creator"]
        if role == "creator":
            return ["creator"]
        return []

    def require_membership(self, user_id: str) -> dict[str, Any]:
        membership = self.membership(user_id)
        if not membership:
            raise PermissionError("Active ESP membership is required")
        return membership

    def get(self, user_id: str) -> dict[str, Any]:
        membership = self.require_membership(user_id)
        role = membership["roles"]
        allowed = self.allowed_views(role)
        default_view = "agent" if "agent" in allowed else "creator"
        with self._connect() as con:
            row = con.execute("SELECT view_mode,updated_at FROM esp_view_preferences WHERE user_id=?", (user_id,)).fetchone()
        selected = row["view_mode"] if row and row["view_mode"] in allowed else default_view
        return {
            "view": selected,
            "allowed_views": allowed,
            "role": role,
            "updated_at": row["updated_at"] if row else None,
        }

    def set(self, user_id: str, view: str) -> dict[str, Any]:
        membership = self.require_membership(user_id)
        requested = (view or "").strip().lower()
        if requested not in VALID_VIEW_MODES:
            raise ValueError("View must be creator or agent")
        allowed = self.allowed_views(membership["roles"])
        if requested not in allowed:
            raise PermissionError("This ESP role cannot use that view")
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO esp_view_preferences(user_id,view_mode,updated_at) VALUES (?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET view_mode=excluded.view_mode,updated_at=excluded.updated_at
                """,
                (user_id, requested, _now()),
            )
        return self.get(user_id)

    def command_center_fragment(self, user_id: str) -> str:
        try:
            context = self.get(user_id)
        except PermissionError:
            return ""
        if len(context["allowed_views"]) < 2:
            return ""
        view = context["view"]
        return f"""
        <style data-no-i18n='true'>
        .esp-view-switch{{position:sticky;top:10px;z-index:200;display:flex;justify-content:center;margin:8px 0 18px;pointer-events:none}}
        .esp-view-switch-inner{{pointer-events:auto;display:flex;gap:6px;padding:6px;background:#100917eF;border:1px solid #473452;border-radius:999px;box-shadow:0 12px 38px #0008;backdrop-filter:blur(12px)}}
        .esp-view-button{{border:0;border-radius:999px;padding:10px 16px;background:transparent;color:#c8bdd2;font-weight:900;cursor:pointer}}
        .esp-view-button.active{{background:linear-gradient(135deg,#e7bd63,#b47cff);color:#160d1d}}
        .esp-view-status{{text-align:center;color:#c8bdd2;font-size:.8rem;margin-top:-10px;margin-bottom:12px}}
        </style>
        <div class='esp-view-switch' data-no-i18n='true'><div class='esp-view-switch-inner'>
          <button type='button' class='esp-view-button' data-view='agent'>Agent View</button>
          <button type='button' class='esp-view-button' data-view='creator'>Creator View</button>
        </div></div><div id='esp-view-status' class='esp-view-status' data-no-i18n='true'></div>
        <script data-no-i18n='true'>
        (()=>{{
          let current={escape(repr(view))};
          const status=document.getElementById('esp-view-status');
          const buttons=[...document.querySelectorAll('.esp-view-button')];
          function section(name){{
            const heading=[...document.querySelectorAll('h2')].find(h=>h.textContent.trim()===name+' Command Center');
            return heading?{{heading,body:heading.nextElementSibling}}:null;
          }}
          function apply(view){{
            current=view; document.documentElement.dataset.espView=view;
            for(const name of ['Agent','Creator']){{const s=section(name);if(!s)continue;const show=name.toLowerCase()===view;s.heading.style.display=show?'':'none';if(s.body)s.body.style.display=show?'':'none'}}
            buttons.forEach(b=>b.classList.toggle('active',b.dataset.view===view));
            if(status) status.textContent=view==='agent'?'Agent operations and training context':'Creator tools and training context';
            window.dispatchEvent(new CustomEvent('aura:esp-view',{{detail:{{view}}}}));
          }}
          buttons.forEach(button=>button.addEventListener('click',async()=>{{
            const requested=button.dataset.view;if(requested===current)return;
            status.textContent='Switching view…';
            try{{const res=await fetch('/esp/view-mode',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{view:requested}})}});const data=await res.json();if(!res.ok)throw new Error(data.detail||'Could not switch view');apply(data.view)}}catch(err){{status.textContent=err.message||String(err)}}
          }}));
          apply(current);
        }})();
        </script>"""


view_modes = EspViewModeStore(store)


def _session_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(MEMBER_COOKIE)


def _member_or_401(request: Request):
    try:
        return memberships.from_session(_session_token(request), require_active=True)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc


@router.get("/esp/view-mode")
def get_view_mode(request: Request):
    member = _member_or_401(request)
    try:
        return view_modes.get(member.user_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/esp/view-mode")
def set_view_mode(request: Request, payload: ViewModeRequest):
    member = _member_or_401(request)
    try:
        return view_modes.set(member.user_id, payload.view)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


class EspCommandCenterViewMiddleware(BaseHTTPMiddleware):
    """Injects the role-safe Agent/Creator context switch into the ESP Command Center."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        if request.url.path != "/command-center" or response.status_code != 200:
            return response
        if "text/html" not in (response.headers.get("content-type") or ""):
            return response
        user = store.resolve_session(request.cookies.get(MEMBER_COOKIE))
        if not user:
            return response
        fragment = view_modes.command_center_fragment(user["id"])
        if not fragment:
            return response
        chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8"))
        text = b"".join(chunks).decode("utf-8", errors="replace")
        marker = "<div class='grid'>"
        if marker in text:
            text = text.replace(marker, fragment + marker, 1)
        elif "</body>" in text:
            text = text.replace("</body>", fragment + "</body>", 1)
        headers = dict(response.headers)
        headers.pop("content-length", None)
        return HTMLResponse(text, status_code=response.status_code, headers=headers)
