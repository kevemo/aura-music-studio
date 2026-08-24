from __future__ import annotations

import contextvars
import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Literal

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

OwnerPersona = Literal["mary", "kev"]
OWNER_PERSONA_COOKIE = "pfh_owner_persona"
OWNER_ADMIN_COOKIE = "lss_admin_session"


@dataclass(frozen=True)
class OwnerTheme:
    key: OwnerPersona
    display_name: str
    accent: str
    secondary: str
    glow: str
    aura_context: str


OWNER_THEMES: dict[OwnerPersona, OwnerTheme] = {
    "mary": OwnerTheme(
        key="mary",
        display_name="Mary",
        accent="#f2b8d5",
        secondary="#9e78ff",
        glow="#f2b8d555",
        aura_context="Owner view: Mary. Prioritise concise creator oversight, people, approvals, progress and operational clarity.",
    ),
    "kev": OwnerTheme(
        key="kev",
        display_name="Kev",
        accent="#ff9b4a",
        secondary="#8f70ff",
        glow="#ff9b4a55",
        aura_context="Owner view: Kev. Prioritise platform architecture, creator growth, production systems, analytics and strategic operational detail.",
    ),
}

_owner_context: contextvars.ContextVar[OwnerPersona | None] = contextvars.ContextVar(
    "pfh_owner_persona", default=None
)


def _admin_key() -> str:
    return (os.getenv("LSS_ADMIN_KEY") or "").strip()


def owner_session_authorized(request: Request) -> bool:
    """Validate the existing owner bootstrap session without exposing the secret.

    The current owner login stores the deployment admin key in an HttpOnly cookie for
    backwards compatibility. New owner-specific state is signed separately and never
    accepts an unsigned client-provided persona value.
    """
    configured = _admin_key()
    supplied = request.cookies.get(OWNER_ADMIN_COOKIE) or ""
    return bool(configured and supplied and hmac.compare_digest(configured, supplied))


def _signature(persona: OwnerPersona) -> str:
    key = _admin_key().encode("utf-8")
    return hmac.new(key, f"owner-persona:{persona}".encode("utf-8"), hashlib.sha256).hexdigest()


def encode_persona_cookie(persona: OwnerPersona) -> str:
    if persona not in OWNER_THEMES:
        raise ValueError("Unknown owner persona")
    if not _admin_key():
        raise RuntimeError("Owner admin key is not configured")
    return f"{persona}.{_signature(persona)}"


def decode_persona_cookie(value: str | None) -> OwnerPersona | None:
    if not value or "." not in value or not _admin_key():
        return None
    persona, supplied = value.split(".", 1)
    if persona not in OWNER_THEMES:
        return None
    expected = _signature(persona)  # type: ignore[arg-type]
    if not hmac.compare_digest(expected, supplied):
        return None
    return persona  # type: ignore[return-value]


def request_owner_persona(request: Request) -> OwnerPersona | None:
    return decode_persona_cookie(request.cookies.get(OWNER_PERSONA_COOKIE))


def current_owner_persona() -> OwnerPersona | None:
    return _owner_context.get()


def owner_theme(persona: OwnerPersona | None = None) -> OwnerTheme:
    selected = persona or current_owner_persona() or "kev"
    return OWNER_THEMES[selected]


def owner_actor(fallback: str = "ESP Owner") -> str:
    persona = current_owner_persona()
    if persona:
        return f"{OWNER_THEMES[persona].display_name} · ESP Owner"
    return fallback


def set_persona_cookie(response: Response, persona: OwnerPersona) -> None:
    response.set_cookie(
        OWNER_PERSONA_COOKIE,
        encode_persona_cookie(persona),
        max_age=12 * 60 * 60,
        httponly=True,
        secure=(os.getenv("LSS_COOKIE_SECURE", "true").lower() == "true"),
        samesite="strict",
    )


def clear_persona_cookie(response: Response) -> None:
    response.delete_cookie(OWNER_PERSONA_COOKIE)


class OwnerIdentityMiddleware(BaseHTTPMiddleware):
    """Bind the verified Mary/Kev owner identity to the current request context."""

    async def dispatch(self, request: Request, call_next):
        persona = request_owner_persona(request) if request.url.path.startswith("/owner") else None
        token = _owner_context.set(persona)
        try:
            request.state.owner_persona = persona
            request.state.owner_theme = owner_theme(persona) if persona else None
            return await call_next(request)
        finally:
            _owner_context.reset(token)
