from __future__ import annotations

import os
from html import escape
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .aura_live_guardian_review import AuraLiveGuardianReviewItem, AuraLiveGuardianReviewStore
from .branding import PRODUCT_FULL_NAME

router = APIRouter(tags=["Aura LIVE Guardian Human Review"])


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _store() -> AuraLiveGuardianReviewStore:
    path = Path(os.getenv("AURA_LIVE_MODERATOR_DB", "data/aura_live_moderator.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return AuraLiveGuardianReviewStore(path)


def _actor(member) -> str:
    return f"member:{member.user_id}"


def _label(item: AuraLiveGuardianReviewItem) -> str:
    if item.review_kind == "safety_escalation":
        return "Critical safety escalation"
    return "Human action confirmation"


def _actions(item: AuraLiveGuardianReviewItem) -> str:
    review_id = escape(item.review_id, quote=True)
    if item.status != "pending":
        return ""
    if item.review_kind == "safety_escalation":
        return (
            f"<form method='post' action='/live-guardian/review/{review_id}/acknowledge'>"
            "<button class='primary' type='submit'>Acknowledge escalation</button></form>"
        )
    expired = item.is_expired()
    confirm = "" if expired else (
        f"<form method='post' action='/live-guardian/review/{review_id}/confirm'>"
        "<button class='primary' type='submit'>Confirm recommendation</button></form>"
    )
    dismiss = (
        f"<form method='post' action='/live-guardian/review/{review_id}/dismiss'>"
        "<button type='submit'>Dismiss</button></form>"
    )
    return f"<div class='actions'>{confirm}{dismiss}</div>"


def _card(item: AuraLiveGuardianReviewItem) -> str:
    expired = item.is_expired()
    status = "expired" if item.status == "pending" and expired else item.status
    provider_snapshot = "Yes" if item.provider_write_permitted_at_decision else "No"
    expiry = item.expires_at.isoformat() if item.expires_at else "Human acknowledgement required"
    return (
        "<article class='card'>"
        f"<div class='eyebrow'>{escape(_label(item))}</div>"
        f"<h2>{escape(item.recommended_action.replace('_', ' ').title())}</h2>"
        f"<p><b>Status:</b> {escape(status.title())}</p>"
        f"<p><b>Category:</b> {escape(item.signal_category.replace('_', ' ').title())} &nbsp; "
        f"<b>Severity:</b> {item.signal_severity}/4 &nbsp; <b>Confidence:</b> {escape(item.confidence_bucket.replace('_', ' ').title())}</p>"
        f"<p><b>Provider write was permitted at decision time:</b> {provider_snapshot}</p>"
        f"<p><b>Created:</b> {escape(item.created_at.isoformat())}<br><b>Expires / acknowledgement:</b> {escape(expiry)}</p>"
        "<p class='muted'>Confirming this item records your human decision only. It does not execute a TikTok action. "
        "Any provider write must independently pass the current approved connector, consent, assignment and capability gates.</p>"
        f"{_actions(item)}</article>"
    )


@router.get("/live-guardian/review", response_class=HTMLResponse, include_in_schema=False)
def guardian_review_page(request: Request):
    member = _member(request)
    store = _store()
    pending = store.pending(member.user_id, limit=100)
    recent = store.recent(member.user_id, limit=30)
    pending_ids = {item.review_id for item in pending}
    history = [item for item in recent if item.review_id not in pending_ids]
    pending_html = "".join(_card(item) for item in pending) or "<div class='card muted'>No Guardian items currently require your review.</div>"
    history_html = "".join(_card(item) for item in history) or "<div class='card muted'>No resolved Guardian reviews yet.</div>"
    title = escape(PRODUCT_FULL_NAME)
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='referrer' content='no-referrer'><meta name='robots' content='noindex,nofollow'><title>Aura LIVE Human Review — {title}</title><style>
body{{margin:0;background:#070812;color:#fff;font-family:Inter,system-ui,sans-serif}}.wrap{{width:min(1080px,calc(100% - 28px));margin:auto;padding:36px 0 64px}}h1{{font-size:clamp(2.5rem,6vw,4.8rem);margin:.12em 0}}.card{{background:#111526;border:1px solid #ffffff20;border-radius:18px;padding:18px;margin:14px 0}}.muted{{color:#bdc6d8;line-height:1.55}}.eyebrow{{color:#efc96b;font-weight:900;text-transform:uppercase;font-size:.78rem;letter-spacing:.06em}}button,.btn{{font:inherit;border:1px solid #ffffff25;border-radius:10px;background:#ffffff0d;color:#fff;padding:10px 12px;cursor:pointer;font-weight:850;text-decoration:none;display:inline-block}}.primary{{background:linear-gradient(120deg,#efc96b,#9b72ff);color:#160b22;border:0}}.actions{{display:flex;gap:10px;flex-wrap:wrap}}.actions form{{margin:0}}section{{margin-top:30px}}
</style></head><body><main class='wrap'><div class='eyebrow'>Aura LIVE Guardian · Human-in-the-loop safety</div><h1>Human Review Queue</h1><p class='muted'>Review Assisted-mode recommendations and acknowledge critical safety escalations. Raw LIVE messages are not stored in this queue. Confirmation never bypasses the approved TikTok/partner connector boundary.</p><a class='btn' href='/live-guardian'>Back to LIVE Guardian</a><section><h2>Needs your review</h2>{pending_html}</section><section><h2>Recent review history</h2>{history_html}</section></main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"})


def _resolve(request: Request, review_id: str, action: str):
    member = _member(request)
    store = _store()
    try:
        if action == "confirm":
            store.confirm_action(user_id=member.user_id, review_id=review_id, actor=_actor(member))
        elif action == "dismiss":
            store.dismiss_action(user_id=member.user_id, review_id=review_id, actor=_actor(member))
        elif action == "acknowledge":
            store.acknowledge_escalation(user_id=member.user_id, review_id=review_id, actor=_actor(member))
        else:
            raise HTTPException(400, "Invalid Guardian review action")
    except KeyError as exc:
        raise HTTPException(404, "Guardian review item not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse("/live-guardian/review", status_code=303)


@router.post("/live-guardian/review/{review_id}/confirm", include_in_schema=False)
def confirm_guardian_review(request: Request, review_id: str):
    return _resolve(request, review_id, "confirm")


@router.post("/live-guardian/review/{review_id}/dismiss", include_in_schema=False)
def dismiss_guardian_review(request: Request, review_id: str):
    return _resolve(request, review_id, "dismiss")


@router.post("/live-guardian/review/{review_id}/acknowledge", include_in_schema=False)
def acknowledge_guardian_escalation(request: Request, review_id: str):
    return _resolve(request, review_id, "acknowledge")


__all__ = [
    "router",
    "guardian_review_page",
    "confirm_guardian_review",
    "dismiss_guardian_review",
    "acknowledge_guardian_escalation",
]
