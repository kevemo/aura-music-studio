from __future__ import annotations

import os
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .accounts import AccountStore
from .billing import payment_instructions
from .branding import PRODUCT_FULL_NAME, PRODUCT_NAME, TAGLINE
from .localization import (
    DEFAULT_LOCALE,
    LOCALE_COOKIE,
    LocalePreferenceStore,
    LocalizationError,
    language_options,
    locale_direction,
    normalize_locale,
)
from .mailer import notify_membership_request
from .membership import MembershipService
from .plans import PLANS, OWNERSHIP_NOTICE

router = APIRouter()
store = AccountStore()
memberships = MembershipService(store)
locale_store = LocalePreferenceStore(store.db_path)
COOKIE_NAME = "lss_session"

CSS = """
:root{--bg:#0b0712;--panel:#171020;--panel2:#21132d;--gold:#e8bd62;--text:#fff;--muted:#cbbfd5;--line:#3b294b;--good:#86e0a8;--bad:#ff9aa9}
*{box-sizing:border-box}body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:radial-gradient(circle at top,#26113b 0,#0b0712 45%);color:var(--text);min-height:100vh}
a{color:inherit}.wrap{max-width:1180px;margin:auto;padding:22px}.nav{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:16px 0}.brand{font-weight:900;letter-spacing:-.02em}.brand small{display:block;color:var(--gold);font-weight:700;font-size:.75rem;letter-spacing:.08em;text-transform:uppercase}.navlinks{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.btn,button{display:inline-block;border:1px solid var(--line);background:#21152d;color:#fff;padding:11px 16px;border-radius:12px;text-decoration:none;font-weight:800;cursor:pointer}.btn.primary,button.primary{background:linear-gradient(135deg,#f1cf7a,#c99b3f);color:#160e1e;border:0}.btn.ghost{background:transparent}.language-select{max-width:230px;background:#120b1d;color:#fff;border:1px solid var(--line);border-radius:12px;padding:10px 12px;font:inherit}.language-box{margin:16px 0;padding:14px;border:1px solid var(--line);border-radius:14px;background:#110b18}.language-box label{display:block;color:var(--gold);font-weight:900;margin-bottom:8px}.hero{padding:80px 0 54px;display:grid;grid-template-columns:1.15fr .85fr;gap:35px;align-items:center}.hero h1{font-size:clamp(2.7rem,7vw,5.7rem);line-height:.94;margin:0 0 22px;letter-spacing:-.055em}.hero h1 span{color:var(--gold)}.hero p{font-size:1.2rem;line-height:1.65;color:var(--muted);max-width:760px}.hero-card,.card{background:linear-gradient(145deg,#1d1228,#110b18);border:1px solid var(--line);border-radius:24px;padding:26px;box-shadow:0 25px 80px #0006}.meter{height:9px;background:#30213d;border-radius:99px;overflow:hidden;margin:14px 0}.meter i{display:block;height:100%;width:88%;background:linear-gradient(90deg,#9d53d6,#e8bd62)}.eyebrow{color:var(--gold);text-transform:uppercase;letter-spacing:.16em;font-weight:900;font-size:.77rem}.section{padding:44px 0}.section h2{font-size:2.3rem;margin:0 0 12px}.section>p{color:var(--muted);max-width:780px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:28px}.price-card{position:relative}.price-card.pro{border-color:#bd8fe2;box-shadow:0 25px 75px #662a9b35}.badge{position:absolute;right:16px;top:16px;background:var(--gold);color:#1b1024;border-radius:99px;padding:6px 10px;font-size:.72rem;font-weight:900}.price{font-size:2.7rem;font-weight:950;margin:14px 0}.price small{font-size:.9rem;color:var(--muted)}ul.features{padding:0;list-style:none;line-height:1.55}.features li{padding:7px 0;border-bottom:1px solid #ffffff0e}.features li:before{content:'✓';color:var(--gold);font-weight:900;margin-right:8px}.form-card{max-width:590px;margin:52px auto}.form-card h1{margin-top:0}.field{margin:15px 0}.field label{display:block;font-weight:800;margin-bottom:7px}.field input,.field select,.field textarea{width:100%;background:#0e0914;border:1px solid var(--line);color:#fff;border-radius:12px;padding:13px;font:inherit}.help,.muted{color:var(--muted);font-size:.92rem}.alert{padding:13px 15px;border-radius:12px;margin:14px 0;background:#321c29;border:1px solid #6a3146}.alert.good{background:#143122;border-color:#2c704a}.dashboard{display:grid;grid-template-columns:280px 1fr;gap:20px;padding:32px 0}.sidebar h3{margin-top:0}.tier{font-size:1.45rem;color:var(--gold);font-weight:950}.tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.tile{background:#15101d;border:1px solid var(--line);padding:18px;border-radius:17px}.tile strong{display:block;font-size:1.1rem;margin-bottom:5px}.locked{opacity:.43}.footer{border-top:1px solid #ffffff10;color:var(--muted);padding:30px 0 50px;margin-top:35px;font-size:.88rem}.translation-status{font-size:.78rem;color:var(--muted);min-height:1.1em}
@media(max-width:820px){.hero,.grid3,.dashboard,.tiles{grid-template-columns:1fr}.hero{padding-top:45px}.nav{align-items:flex-start;flex-direction:column}.hero h1{font-size:3rem}.language-select{max-width:100%;width:100%}}
"""


def _member_and_locale(request: Request | None) -> tuple[dict | None, str]:
    member = None
    if request:
        member = store.resolve_session(request.cookies.get(COOKIE_NAME))
        if member:
            saved = locale_store.get_user_locale(member["id"])
            if saved:
                return member, saved
        cookie_locale = request.cookies.get(LOCALE_COOKIE)
        if cookie_locale:
            try:
                return member, normalize_locale(cookie_locale)
            except LocalizationError:
                pass
    return member, DEFAULT_LOCALE


def _language_selector(current: str, *, element_id: str) -> str:
    options = []
    for item in language_options():
        label = item["native_name"]
        if item["english_name"].casefold() != item["native_name"].casefold():
            label += f" — {item['english_name']}"
        selected = " selected" if item["locale"] == current else ""
        options.append(
            f"<option value='{escape(item['locale'], quote=True)}' data-dir='{item['direction']}'{selected}>{escape(label)}</option>"
        )
    return (
        f"<select id='{escape(element_id)}' class='language-select lss-language-select' "
        "aria-label='Language' data-no-i18n='true'>"
        + "".join(options)
        + "</select>"
    )


def _i18n_script(current: str) -> str:
    safe_locale = escape(current, quote=True)
    return f"""
<script>
(() => {{
  const initialLocale = {safe_locale!r};
  const skipTags = new Set(['SCRIPT','STYLE','CODE','PRE','NOSCRIPT']);
  function collectTextNodes() {{
    const nodes=[];
    const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT,{{acceptNode(node){{
      const p=node.parentElement;
      if(!p || skipTags.has(p.tagName) || p.closest('[data-no-i18n="true"]')) return NodeFilter.FILTER_REJECT;
      const v=(node.nodeValue||'').trim();
      if(!v || /^[\d\s.$£€¥%+\-/:]+$/.test(v)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    }}}});
    while(walker.nextNode()) nodes.push(walker.currentNode);
    return nodes;
  }}
  async function translatePage(locale) {{
    document.documentElement.lang=locale;
    const selected=[...document.querySelectorAll('.lss-language-select option:checked')][0];
    document.documentElement.dir=selected?.dataset.dir || 'ltr';
    if(locale==='en') return;
    const nodes=collectTextNodes();
    const texts=[...new Set(nodes.map(n=>(n.nodeValue||'').trim()))].slice(0,200);
    if(!texts.length) return;
    document.querySelectorAll('.translation-status').forEach(x=>x.textContent='Aura is translating the interface…');
    try {{
      const res=await fetch('/localization/translate',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{locale,texts}})}});
      const data=await res.json();
      if(!res.ok || !data.translated) throw new Error(data.detail || data.warning || 'Translation unavailable');
      const map=new Map(texts.map((text,i)=>[text,data.translations[i]]));
      for(const node of nodes) {{
        const raw=node.nodeValue||''; const trimmed=raw.trim(); const translated=map.get(trimmed);
        if(translated) node.nodeValue=raw.replace(trimmed,translated);
      }}
      document.querySelectorAll('.translation-status').forEach(x=>x.textContent='');
    }} catch(err) {{
      document.querySelectorAll('.translation-status').forEach(x=>x.textContent='Aura could not translate this screen yet. English remains available.');
    }}
  }}
  async function saveLocale(locale) {{
    const res=await fetch('/localization/preference',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{locale}})}});
    if(!res.ok) throw new Error('Could not save language');
    document.querySelectorAll('.lss-language-select').forEach(el=>{{if(el.value!==locale) el.value=locale;}});
    if(locale==='en') {{ location.reload(); return; }}
    location.reload();
  }}
  document.querySelectorAll('.lss-language-select').forEach(el=>el.addEventListener('change',()=>saveLocale(el.value)));
  if(initialLocale!=='en') translatePage(initialLocale);
}})();
</script>
"""


def _page(title: str, body: str, request: Request | None = None) -> HTMLResponse:
    member, locale = _member_and_locale(request)
    auth_nav = (
        "<a class='btn ghost' href='/dashboard'>Dashboard</a>"
        if member else
        "<a class='btn ghost' href='/signin'>Sign in</a><a class='btn primary' href='/signup'>Join Studio</a>"
    )
    language = _language_selector(locale, element_id="language-select-nav")
    direction = locale_direction(locale)
    html = f"""<!doctype html><html lang='{escape(locale, quote=True)}' dir='{direction}'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{escape(title)} — {escape(PRODUCT_NAME)}</title><style>{CSS}</style></head><body>
<div class='wrap'><nav class='nav'><a class='brand' data-no-i18n='true' href='/' style='text-decoration:none'>{escape(PRODUCT_NAME)}<small>Elevate Souls Productions</small></a><div class='navlinks'>{language}<a class='btn ghost' href='/pricing'>Pricing</a>{auth_nav}</div></nav><div class='translation-status'></div>{body}
<footer class='footer'>{escape(PRODUCT_FULL_NAME)} · {escape(TAGLINE)}<br><br>{escape(OWNERSHIP_NOTICE)}</footer></div>{_i18n_script(locale)}</body></html>"""
    return HTMLResponse(html)


def _feature_names(plan_id: str) -> list[str]:
    if plan_id == "free":
        return ["Song ideas and basic creation", "Basic AI lyrics", "Aura Producer and multilingual companion", "Basic real-audio previews"]
    if plan_id == "base":
        return [
            "1 confirmed full track every day",
            "Unlimited regeneration until that track is confirmed",
            "MP3 + WAV finished downloads",
            "Standard AI video generation",
            "AI image and poster generation",
            "Core visual editing and effects",
            "Backing tracks, harmonies, cleanup and mastering",
        ]
    return [
        "Unlimited confirmed full tracks and regeneration",
        "Advanced music generation, multitrack DAW and mastering",
        "Consent-approved multilingual singing voice conversion",
        "Advanced video generation and Aura Video Director",
        "Advanced image/poster generation and editing",
        "Layered Visual FX Studio with masks, keyframes and compositing",
        "Advanced captions, color, motion, exports and priority processing",
        "Aura multilingual companion and live translator",
    ]


def _pricing_cards(selected: str | None = None) -> str:
    chunks = []
    for pid in ("free", "base", "pro"):
        plan = PLANS[pid]
        price = "Free" if pid == "free" else f"${plan.monthly_price_usd}"
        cls = "card price-card pro" if pid == "pro" else "card price-card"
        badge = "<span class='badge'>FULL STUDIO</span>" if pid == "pro" else ""
        features = "".join(f"<li>{escape(x)}</li>" for x in _feature_names(pid))
        cta = "Choose this plan" if selected != pid else "Selected"
        chunks.append(f"<div class='{cls}'>{badge}<div class='eyebrow'>{escape(plan.name)}</div><div class='price'>{price}<small>{'' if pid=='free' else ' / month'}</small></div><p class='muted'>{escape(plan.description)}</p><ul class='features'>{features}</ul><a class='btn primary' href='/signup?plan={pid}'>{cta}</a></div>")
    return "<div class='grid3'>" + "".join(chunks) + "</div>"


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    body = f"""
<section class='hero'><div><div class='eyebrow'>AI music · video · image · visual FX · multilingual Aura</div><h1>Create with <span>Aura.</span></h1><p>Create original music, professional backing tracks, images, posters, videos and release-ready visual content inside one intelligent Studio. Aura can guide the system by text or voice and can translate across languages.</p><div class='navlinks'><a class='btn primary' href='/signup'>Start creating</a><a class='btn' href='/pricing'>Compare plans</a></div></div>
<div class='hero-card'><div class='eyebrow'>Aura Producer & Companion</div><h2>One studio. One creative intelligence.</h2><p class='muted'>Lyrics → arrangement → vocals → mix → master → artwork → video → visual FX → export.</p><div class='meter'><i></i></div><p><b>Real creative outputs</b><br><span class='muted'>No symbolic music guide or placeholder render is presented as a finished master.</span></p></div></section>
<section class='section'><div class='eyebrow'>Memberships</div><h2>Start basic. Unlock the professional system.</h2><p>All memberships require approval by Elevate Souls Productions before access activates. Paid plans move to payment only after approval.</p>{_pricing_cards()}</section>"""
    return _page("Home", body, request)


@router.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request):
    body = f"<section class='section'><div class='eyebrow'>Plans</div><h2>Choose your studio level</h2><p>Base unlocks the core Music, Video and Image/Poster creation system. Pro adds the advanced professional controls across music, video, images and Visual FX.</p>{_pricing_cards()}</section>"
    return _page("Pricing", body, request)


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, plan: str = "free", error: str | None = None):
    plan = plan if plan in PLANS else "free"
    error_html = f"<div class='alert'>{escape(error)}</div>" if error else ""
    options = "".join(f"<option value='{pid}' {'selected' if pid==plan else ''}>{escape(PLANS[pid].name)} — {'Free' if pid=='free' else '$'+str(PLANS[pid].monthly_price_usd)+'/month'}</option>" for pid in ("free","base","pro"))
    body = f"""<div class='card form-card'><div class='eyebrow'>Membership request</div><h1>Create your account</h1><p class='muted'>Your request is sent to Elevate Souls Productions for approval before access is activated.</p>{error_html}
<form method='post' action='/signup'><div class='field'><label>Name</label><input name='display_name' required minlength='2' autocomplete='name'></div><div class='field'><label>Email</label><input type='email' name='email' required autocomplete='email'></div><div class='field'><label>Password</label><input type='password' name='password' required minlength='10' autocomplete='new-password'><div class='help'>Minimum 10 characters.</div></div><div class='field'><label>Membership</label><select name='plan_id'>{options}</select></div><button class='primary' type='submit'>Send membership request</button></form><p class='help'>Already have an account? <a href='/signin'>Sign in</a>.</p></div>"""
    return _page("Sign up", body, request)


@router.post("/signup", response_class=HTMLResponse)
def signup_submit(request: Request, display_name: str = Form(...), email: str = Form(...), password: str = Form(...), plan_id: str = Form("free")):
    try:
        result = store.signup(email, display_name, password, plan_id)
        cookie_locale = request.cookies.get(LOCALE_COOKIE)
        if cookie_locale:
            try:
                locale_store.set_user_locale(result.user_id, normalize_locale(cookie_locale))
            except LocalizationError:
                pass
        notify_membership_request(approval_token=result.approval_token, applicant_email=result.email, display_name=result.display_name, plan_id=result.requested_plan)
    except Exception as exc:
        return signup_page(request, plan_id, str(exc))
    body = f"""<div class='card form-card'><div class='eyebrow'>Request received</div><h1>Membership pending approval</h1><div class='alert good'>Your request for the <b>{escape(result.requested_plan.upper())}</b> tier has been sent to Elevate Souls Productions.</div><p>You will be able to sign in while pending, but studio access stays locked until the request is approved. Paid plans then require payment verification before activation.</p><a class='btn primary' href='/signin'>Continue to sign in</a></div>"""
    return _page("Membership pending", body, request)


@router.get("/signin", response_class=HTMLResponse)
def signin_page(request: Request, error: str | None = None):
    error_html = f"<div class='alert'>{escape(error)}</div>" if error else ""
    body = f"""<div class='card form-card'><div class='eyebrow'>Member access</div><h1>Sign in</h1>{error_html}<form method='post' action='/signin'><div class='field'><label>Email</label><input type='email' name='email' required autocomplete='email'></div><div class='field'><label>Password</label><input type='password' name='password' required autocomplete='current-password'></div><button class='primary' type='submit'>Sign in</button></form><p class='help'>New here? <a href='/signup'>Request membership</a>.</p></div>"""
    return _page("Sign in", body, request)


@router.post("/signin")
def signin_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    user = store.authenticate(email, password)
    if not user:
        return signin_page(request, "Incorrect email or password")
    cookie_locale = request.cookies.get(LOCALE_COOKIE)
    if cookie_locale:
        try:
            locale_store.set_user_locale(user["id"], normalize_locale(cookie_locale))
        except LocalizationError:
            pass
    token = store.create_session(user["id"])
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(COOKIE_NAME, token, max_age=30*24*60*60, httponly=True, secure=(os.getenv("LSS_COOKIE_SECURE", "true").lower()=="true"), samesite="lax")
    return response


@router.post("/signout")
def signout(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    store.revoke_session(token)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    user = store.resolve_session(token)
    if not user:
        return RedirectResponse("/signin", status_code=303)
    status = user["status"]
    requested = user.get("requested_plan_id") or user.get("plan_id") or "free"
    active_plan = user.get("plan_id") or "free"
    if status == "pending_approval":
        state = "<div class='alert'>Your membership request is waiting for ESP approval. Studio generation is locked until approval.</div>"
    elif status == "approved_pending_payment":
        pay = payment_instructions(requested)
        state = f"<div class='alert'>Your membership was approved. Complete the ${escape(str(pay.get('amount_usd','')))} payment and wait for payment verification to activate your plan.</div><a class='btn primary' target='_blank' rel='noopener' href='{escape(pay.get('url') or '', quote=True)}'>Open PayPal payment</a>"
    elif status == "active":
        state = f"<div class='alert good'>Your {escape(PLANS[active_plan].name)} membership is active.</div>"
    elif status == "rejected":
        state = "<div class='alert'>This membership request was not approved.</div>"
    else:
        state = f"<div class='alert'>Account status: {escape(status)}</div>"
    plan_for_tiles = PLANS[active_plan if status == "active" else "free"]
    tile_defs = [
        ("Create Music","Song creation and Aura production","basic_create"),
        ("Full Track","Finished real-audio generation","full_track"),
        ("Backing Tracks","Create professional backing arrangements","backing_track"),
        ("Mastering","Master finished mixes","basic_mastering"),
        ("Video Studio","AI video generation","video_generation"),
        ("Image & Poster Studio","AI artwork, covers and posters","image_generation"),
        ("Visual FX Studio","Layered professional visual editor","visual_fx_studio"),
        ("Splitter / Stems","Separate vocals and instruments","stem_splitter"),
        ("Multitrack Studio","Tracks, takes, automation and editing","multitrack_daw"),
        ("Voice Studio","Consent-approved multilingual voice production","approved_voice_duplication"),
        ("Aura Live Translator","Two-way multilingual speech and captions","aura_speech"),
        ("Style DNA","Blend authorized style references","style_dna"),
    ]
    tiles = "".join(f"<div class='tile {'locked' if feature not in plan_for_tiles.features else ''}'><strong>{escape(name)}</strong><span class='muted'>{escape(desc)}</span><br><small>{'Available' if feature in plan_for_tiles.features and status=='active' else 'Locked / upgrade required'}</small></div>" for name,desc,feature in tile_defs)
    current_locale = locale_store.get_user_locale(user["id"]) or DEFAULT_LOCALE
    dashboard_language = _language_selector(current_locale, element_id="language-select-dashboard")
    body = f"""<div class='dashboard'><aside class='card sidebar'><div class='eyebrow'>Member</div><h3>{escape(user['display_name'])}</h3><p class='muted' data-no-i18n='true'>{escape(user['email'])}</p><div class='tier'>{escape((requested if status!='active' else active_plan).upper())}</div><p>Status: <b>{escape(status.replace('_',' '))}</b></p><div class='language-box'><label for='language-select-dashboard'>Language</label>{dashboard_language}<div class='translation-status'></div></div><form method='post' action='/signout'><button type='submit'>Sign out</button></form></aside><main><div class='eyebrow'>Studio dashboard</div><h1>Welcome to The Live Sound Studio</h1>{state}<section class='section'><h2>Your studio</h2><div class='tiles'>{tiles}</div></section></main></div>"""
    return _page("Dashboard", body, request)
