from __future__ import annotations

import os
from html import escape
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .aura_live_guardian_policy import AuraLiveGuardianPolicyStore
from .branding import PRODUCT_FULL_NAME

router = APIRouter(tags=["Aura LIVE Guardian Policy"])

_CATEGORY_LABELS = {
    "harassment": "Harassment / personal attacks",
    "hate": "Hate / identity attacks",
    "sexual": "Sexual comments",
    "threat": "Threats",
    "doxxing": "Personal-information exposure / doxxing",
    "scam": "Scams / deceptive solicitation",
    "spam": "Spam / repeated promotion",
    "impersonation": "Impersonation",
    "self_harm_concern": "Self-harm concern",
    "grooming_concern": "Grooming concern",
}
_LOCKED_CATEGORIES = {"threat", "doxxing", "grooming_concern"}


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _store() -> AuraLiveGuardianPolicyStore:
    path = Path(os.getenv("AURA_LIVE_MODERATOR_DB", "data/aura_live_moderator.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return AuraLiveGuardianPolicyStore(path)


def _checked(value: bool) -> str:
    return " checked" if value else ""


@router.get("/live-guardian/policy", response_class=HTMLResponse, include_in_schema=False)
def guardian_policy_page(request: Request):
    member = _member(request)
    policy = _store().get(member.user_id)
    phrases = "\n".join(policy.blocked_phrases) if policy else ""
    language = policy.language_tolerance if policy else "balanced"
    spam = policy.spam_sensitivity if policy else "medium"
    enabled = policy.enabled_categories if policy else frozenset(_CATEGORY_LABELS)
    category_controls = "".join(
        (f"<label class='category locked'><input type='checkbox' checked disabled> {escape(label)} <span>Always protected</span></label>"
         if key in _LOCKED_CATEGORIES else
         f"<label class='category'><input type='checkbox' name='category' value='{key}'{_checked(key in enabled)}> {escape(label)}</label>")
        for key, label in _CATEGORY_LABELS.items()
    )
    language_options = "".join(f"<option value='{v}'{' selected' if language == v else ''}>{v.title()}</option>" for v in ("strict", "balanced", "relaxed"))
    spam_options = "".join(f"<option value='{v}'{' selected' if spam == v else ''}>{v.title()}</option>" for v in ("low", "medium", "high"))
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='referrer' content='no-referrer'><meta name='robots' content='noindex,nofollow'><title>Aura LIVE Guardian Policy — {escape(PRODUCT_FULL_NAME)}</title><style>
body{{margin:0;background:#070812;color:#fff;font-family:Inter,system-ui,sans-serif}}.wrap{{width:min(980px,calc(100% - 28px));margin:auto;padding:36px 0 60px}}h1{{font-size:clamp(2.4rem,6vw,4.7rem);margin:.1em 0}}.card{{background:#111526;border:1px solid #ffffff20;border-radius:18px;padding:18px;margin:16px 0}}.muted{{color:#bdc6d8;line-height:1.55}}label{{font-weight:800}}textarea,select,button,.btn{{font:inherit;border:1px solid #ffffff25;border-radius:10px;background:#ffffff0d;color:#fff;padding:10px 12px}}textarea{{width:100%;min-height:180px;resize:vertical}}select{{min-width:190px}}button,.btn{{cursor:pointer;font-weight:850;text-decoration:none;display:inline-block}}.primary{{background:linear-gradient(120deg,#efc96b,#9b72ff);color:#160b22;border:0}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.category{{display:block;padding:11px 0;border-bottom:1px solid #ffffff12}}.category span{{color:#efc96b;font-size:.8rem;margin-left:6px}}@media(max-width:700px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><main class='wrap'><div style='color:#efc96b;font-weight:900'>Aura LIVE Guardian · Creator-specific moderation</div><h1>Your LIVE moderation policy</h1><p class='muted'>Tune Aura to your show style without weakening critical safety protections. These preferences shape recommendations and any future approved bounded connector actions; they do not create TikTok API authority.</p><form method='post' action='/live-guardian/policy'><div class='card'><h2>Blocked phrases</h2><p class='muted'>One phrase per line, up to 100 phrases.</p><textarea name='blocked_phrases' maxlength='12100' placeholder='one phrase per line'>{escape(phrases)}</textarea></div><div class='card'><div class='grid'><div><h2>Language tolerance</h2><select name='language_tolerance'>{language_options}</select><p class='muted'>Relaxed can tolerate ordinary strong language; it does not relax hate, threats, doxxing or grooming protections.</p></div><div><h2>Spam sensitivity</h2><select name='spam_sensitivity'>{spam_options}</select><p class='muted'>Higher sensitivity catches repetitive promotions and message flooding earlier.</p></div></div></div><div class='card'><h2>Moderation categories</h2>{category_controls}<p class='muted'><b>Threats, doxxing and grooming concern are locked on.</b></p></div><button class='primary' type='submit'>Save moderation policy</button> <a class='btn' href='/live-guardian'>Back to LIVE Guardian</a></form></main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"})


@router.post("/live-guardian/policy", include_in_schema=False)
def save_guardian_policy(request: Request, blocked_phrases: str = Form(""), language_tolerance: str = Form("balanced"), spam_sensitivity: str = Form("medium"), category: list[str] = Form(default=[])):
    member = _member(request)
    _store().save(user_id=member.user_id, blocked_phrases=[line for line in blocked_phrases.splitlines() if line.strip()], language_tolerance=language_tolerance, spam_sensitivity=spam_sensitivity, enabled_categories=set(category), actor=f"member:{member.user_id}")  # type: ignore[arg-type]
    return RedirectResponse("/live-guardian/policy", status_code=303)


__all__ = ["router", "guardian_policy_page", "save_guardian_policy"]
