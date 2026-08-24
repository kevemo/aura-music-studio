from __future__ import annotations

from html import escape

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .branding import PRODUCT_FULL_NAME
from .esp_command_center import command_center as base_command_center, esp
from .esp_niche import EspNicheStore, NICHE_CATALOG, niche_definition, social_access_reason

router = APIRouter()
niches = EspNicheStore()


def _member(request: Request):
    return getattr(request.state, "member", None)


def _active_esp(request: Request) -> tuple[object | None, dict | None]:
    member = _member(request)
    if member is None:
        return None, None
    membership = esp.membership(member.user_id)
    if not membership or membership.get("status") not in {"active", "owner"}:
        return member, None
    return member, membership


def _options(selected: str | None) -> str:
    rows = []
    for key, value in NICHE_CATALOG.items():
        flag = " selected" if key == selected else ""
        rows.append(
            f"<option value='{escape(key)}'{flag}>{escape(value['icon'])} {escape(value['title'])}</option>"
        )
    return "".join(rows)


def _selected(value: str | None, expected: str) -> str:
    return " selected" if value == expected else ""


@router.get("/command-center/niche", response_class=HTMLResponse, include_in_schema=False)
def niche_select(request: Request):
    member, membership = _active_esp(request)
    if member is None:
        return RedirectResponse("/signin?next=/command-center/niche", status_code=303)
    if membership is None:
        return RedirectResponse("/command-center", status_code=303)

    profile = niches.get(member.user_id)
    current = (profile or {}).get("niche")
    definition = niche_definition(current)
    network_status = (profile or {}).get("network_status", "unsure")
    goals = "\n".join((profile or {}).get("goals", []))
    sub_niche = escape((profile or {}).get("sub_niche", ""))
    audience = escape((profile or {}).get("audience", ""))
    accent = definition["theme"]["accent"]
    secondary = definition["theme"]["secondary"]

    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>
<meta name='robots' content='noindex,nofollow'><title>ESP Niche Select — {escape(PRODUCT_FULL_NAME)}</title>
<style>
:root{{--accent:{accent};--secondary:{secondary};--bg:#07050d;--panel:#15101d;--line:#ffffff20;--text:#fff;--muted:#c9bfd2}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 85% 0,var(--secondary),transparent 29%),radial-gradient(circle at 10% 0,var(--accent),transparent 24%),#07050d;color:var(--text);font-family:Inter,ui-sans-serif,system-ui,sans-serif;min-height:100vh}}.wrap{{width:min(1000px,calc(100% - 28px));margin:auto;padding:36px 0 60px}}.top{{display:flex;justify-content:space-between;gap:14px;align-items:center;flex-wrap:wrap}}.eyebrow{{color:var(--accent);font-weight:900;letter-spacing:.15em;text-transform:uppercase;font-size:.72rem}}h1{{font-size:clamp(2.5rem,7vw,5.4rem);line-height:.94;letter-spacing:-.05em;margin:.18em 0}}p{{color:var(--muted);line-height:1.6}}.card{{border:1px solid var(--line);border-radius:22px;padding:20px;background:#100c18e8;backdrop-filter:blur(14px);margin-top:18px}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}label{{display:block;font-weight:800;margin:10px 0 6px}}input,select,textarea{{width:100%;border:1px solid var(--line);background:#08070d;color:#fff;border-radius:12px;padding:12px;outline:none}}textarea{{min-height:110px;resize:vertical}}input:focus,select:focus,textarea:focus{{border-color:var(--accent)}}.network{{border:1px solid #f4c87355;background:#f4c8730d;border-radius:17px;padding:15px;margin-top:14px}}.choice{{display:flex;gap:10px;align-items:flex-start;margin:10px 0;color:var(--muted)}}.choice input{{width:auto;margin-top:4px}}.btn{{display:inline-block;border:0;border-radius:12px;padding:11px 15px;font-weight:900;background:linear-gradient(110deg,var(--accent),var(--secondary));color:#120b18;text-decoration:none;cursor:pointer}}.secondary{{background:#ffffff0b;color:#fff;border:1px solid var(--line)}}.actions{{display:flex;gap:9px;flex-wrap:wrap;margin-top:18px}}.notice{{border-left:4px solid var(--accent);padding:12px 14px;background:#ffffff08;border-radius:10px;color:var(--muted)}}@media(max-width:700px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Elevate Souls Productions · Private Creator & Agent Hub</div></div><a class='btn secondary' href='/command-center'>Back to ESP Hub</a></div>
<h1>{escape(definition['icon'])} Choose your creator niche.</h1><p>This selection customises your ESP dashboard theme, Aura guidance, training priorities and private social-management workflow. It does not change the separate Pulsar-Frequency House creative studio.</p>
<div class='notice'><strong>No-poaching boundary:</strong> ESP social-management tools are for approved ESP creators, agents and owners. They are not offered to accounts represented by another Creator Network.</div>
<form class='card' method='post' action='/command-center/niche'><div class='grid'><div><label>Primary niche</label><select name='niche' required>{_options(current)}</select></div><div><label>Specific sub-niche</label><input name='sub_niche' value='{sub_niche}' placeholder='Example: singer-songwriter, cosy gaming, beauty tutorials'></div></div>
<label>Who is your audience?</label><textarea name='audience' placeholder='Describe the people you want to serve and entertain.'>{audience}</textarea>
<label>Your current goals — one per line</label><textarea name='goals' placeholder='Improve LIVE retention\nBuild a repeat audience\nCreate a consistent short-form strategy'>{escape(goals)}</textarea>
<div class='network'><strong>Creator Network affiliation for this account</strong><p>This protects creators and other networks. Choose the statement that is currently accurate.</p>
<label class='choice'><input type='radio' name='network_status' value='esp_only'{_selected(network_status, 'esp_only')} required><span><strong>ESP-approved / no other Creator Network.</strong><br>This account is approved inside Elevate Souls Productions and is not represented by another Creator Network.</span></label>
<label class='choice'><input type='radio' name='network_status' value='other_network'{_selected(network_status, 'other_network')}><span><strong>Currently represented by another Creator Network.</strong><br>ESP social-management tools will remain locked. We do not use this system to recruit or manage another network's creator.</span></label>
<label class='choice'><input type='radio' name='network_status' value='unsure'{_selected(network_status, 'unsure')}><span><strong>Unsure / needs owner review.</strong><br>Social-management tools remain locked until the affiliation is confirmed.</span></label></div>
<div class='actions'><button class='btn' type='submit'>Save niche & personalise ESP Hub</button><a class='btn secondary' href='/command-center'>Cancel</a></div></form></main></body></html>"""
    return HTMLResponse(html)


@router.post("/command-center/niche", include_in_schema=False)
def save_niche(
    request: Request,
    niche: str = Form(...),
    sub_niche: str = Form(""),
    audience: str = Form(""),
    goals: str = Form(""),
    network_status: str = Form(...),
):
    member, membership = _active_esp(request)
    if member is None:
        return RedirectResponse("/signin?next=/command-center/niche", status_code=303)
    if membership is None:
        return RedirectResponse("/command-center", status_code=303)
    goal_rows = [line.strip() for line in goals.splitlines() if line.strip()]
    niches.set(
        member.user_id,
        niche=niche,
        sub_niche=sub_niche,
        audience=audience,
        goals=goal_rows,
        network_status=network_status,  # validated by EspNicheStore
    )
    return RedirectResponse("/command-center", status_code=303)


def _niche_banner(profile: dict, membership: dict) -> str:
    definition = profile["catalog"]
    accent = definition["theme"]["accent"]
    secondary = definition["theme"]["secondary"]
    role = membership.get("roles") or ("owner" if membership.get("status") == "owner" else "member")
    allowed, reason = social_access_reason(membership, profile)
    training = "".join(
        f"<li style='margin:7px 0'>{escape(item)}</li>" for item in definition.get("training", [])
    )
    goals = "".join(
        f"<span style='display:inline-block;border:1px solid #ffffff25;border-radius:999px;padding:5px 9px;margin:3px'>{escape(goal)}</span>"
        for goal in profile.get("goals", [])
    ) or "<span style='color:#c8bdd2'>Add goals from Niche Select so Aura can customise your coaching.</span>"
    if allowed:
        social = (
            "<a class='btn' href='/command-center/social' style='margin-top:10px'>"
            "Open ESP Social Management</a>"
        )
    else:
        social = (
            "<div style='border:1px solid #f2bd5a45;background:#f2bd5a0d;border-radius:12px;padding:11px;margin-top:10px;'>"
            f"<strong>Social management locked.</strong><br><small>{escape(reason)}</small></div>"
        )
    return f"""
<style>:root{{--gold:{accent};--purple:{secondary}}}</style>
<section class='card' style='border-color:{accent}66;background:linear-gradient(135deg,{accent}14,{secondary}16,#120b19);'>
<div class='top'><div><div class='brand'>{escape(definition['icon'])} {escape(definition['title'])} ESP Hub</div><h2 style='margin:.35em 0'>Your niche-specific creator system</h2><p class='muted'>Role: {escape(str(role).title())} · Sub-niche: {escape(profile.get('sub_niche') or 'Not specified')}</p></div><div><a class='btn secondary' href='/command-center/niche'>Change Niche</a> <a class='btn secondary' href='/command-center/progress'>LIVE & Video Progress</a></div></div>
<div class='grid2'><div><h3>Aura training priorities</h3><ul class='muted' style='padding-left:20px'>{training}</ul><a class='btn' href='/command-center/progress' style='margin-top:8px'>Upload Analytics & Get Aura Guidance</a></div><div><h3>Your current goals</h3><div>{goals}</div><h3 style='margin-top:18px'>ESP Social Growth Tools</h3><p class='muted'>Private planning, content management, approvals and social training for this ESP niche. This is separate from the public creative studio.</p>{social}</div></div></section>
"""


@router.get("/command-center", response_class=HTMLResponse, include_in_schema=False)
def niche_command_center(request: Request):
    """Intercept active ESP members so niche selection becomes the hub entry experience.

    Non-ESP members still fall through to the original Command Center page so the existing
    access-request process remains available. Active ESP members select a niche once, then
    the original mature Command Center is rendered with a niche-specific theme and training
    header. This avoids duplicating or weakening the existing ESP role/approval system.
    """
    member, membership = _active_esp(request)
    if member is None or membership is None:
        return base_command_center(request)

    profile = niches.get(member.user_id)
    if profile is None:
        return RedirectResponse("/command-center/niche", status_code=303)

    response = base_command_center(request)
    if not isinstance(response, Response) or not getattr(response, "body", None):
        return response
    try:
        html = response.body.decode("utf-8")
    except Exception:
        return response

    banner = _niche_banner(profile, membership)
    marker = "<div class='wrap'>"
    if marker in html:
        html = html.replace(marker, marker + banner, 1)
    else:
        html = html.replace("<body>", "<body>" + banner, 1)
    return HTMLResponse(html, status_code=response.status_code)
