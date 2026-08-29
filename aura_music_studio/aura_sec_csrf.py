from __future__ import annotations

import hashlib
import hmac


def approval_csrf_token(session_token: str, action_id: str, *, phase: str, challenge_token: str = "") -> str:
    """Derive a CSRF proof bound to the exact HttpOnly session and approval step.

    The high-entropy member session token acts as the HMAC key and is never exposed in
    page markup. The resulting proof is scoped to one action and one phase; confirmation
    can additionally bind to the one-time approval token. No global secret is required and
    a token from another login session/action cannot be reused.
    """
    session = (session_token or "").encode("utf-8")
    action = (action_id or "").strip()
    phase_value = (phase or "").strip().lower()
    if len(session) < 16:
        raise ValueError("Valid Aura Sec member session required for CSRF proof")
    if not action or phase_value not in {"start", "confirm"}:
        raise ValueError("Invalid Aura Sec approval CSRF scope")
    message = f"aura-sec-approval:{phase_value}:{action}:{challenge_token}".encode("utf-8")
    return hmac.new(session, message, hashlib.sha256).hexdigest()


def verify_approval_csrf(
    supplied: str,
    session_token: str,
    action_id: str,
    *,
    phase: str,
    challenge_token: str = "",
) -> bool:
    try:
        expected = approval_csrf_token(
            session_token,
            action_id,
            phase=phase,
            challenge_token=challenge_token,
        )
    except ValueError:
        return False
    return bool(supplied) and hmac.compare_digest(str(supplied).strip(), expected)


__all__ = ["approval_csrf_token", "verify_approval_csrf"]
