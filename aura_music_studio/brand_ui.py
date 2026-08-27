from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

from .branding import ENDORSEMENT, PRODUCT_FULL_NAME, TAGLINE

router = APIRouter(tags=["Command Center Brand"])
STATIC_DIR = Path(__file__).resolve().parent / "static"
COMMAND_CENTER_MARK_PATH = STATIC_DIR / "elevate-souls-command-center-logo.svg"
COMMAND_CENTER_ART_PATH = STATIC_DIR / "elevate-souls-command-center-brand.webp"

# Internal compatibility export retained for older modules/tests. It points at the current
# Command Center artwork; the historical asset is no longer authoritative.
LOGO_PATH = COMMAND_CENTER_ART_PATH

COMMAND_CENTER_THEME_CSS = r"""
:root{
  --espcc-black:#030207;
  --espcc-ink:#08040f;
  --espcc-panel:#13091d;
  --espcc-panel-2:#20102d;
  --espcc-purple:#7030b8;
  --espcc-violet:#9d42ee;
  --espcc-magenta:#d637bd;
  --espcc-blue:#3f8dff;
  --espcc-gold:#e9bb58;
  --espcc-gold-hi:#ffe9a6;
  --espcc-silver:#eee9f3;
  --espcc-ruby:#d63148;
  --espcc-line:#50305e;
  --espcc-text:#fffaf2;
  --espcc-muted:#cfc2d7;
  /* compatibility variables used by older workspaces */
  --esp-black:var(--espcc-black);--esp-ink:var(--espcc-ink);--esp-panel:var(--espcc-panel);
  --esp-panel-2:var(--espcc-panel-2);--esp-purple:var(--espcc-purple);--esp-violet:var(--espcc-violet);
  --esp-magenta:var(--espcc-magenta);--esp-gold:var(--espcc-gold);--esp-gold-hi:var(--espcc-gold-hi);
  --esp-silver:var(--espcc-silver);--esp-ruby:var(--espcc-ruby);--esp-line:var(--espcc-line);
  --esp-text:var(--espcc-text);--esp-muted:var(--espcc-muted);
  --bg:var(--espcc-black);--panel:var(--espcc-panel);--panel2:var(--espcc-panel-2);
  --gold:var(--espcc-gold);--text:var(--espcc-text);--muted:var(--espcc-muted);--line:var(--espcc-line);
}
html{background:var(--espcc-black);color-scheme:dark}
body.esp-command-center-shell,body{
  color:var(--espcc-text)!important;
  background:
    radial-gradient(circle at 50% -10%,rgba(214,55,189,.22),transparent 34%),
    radial-gradient(circle at 8% 22%,rgba(112,48,184,.24),transparent 30%),
    radial-gradient(circle at 94% 34%,rgba(233,187,88,.12),transparent 24%),
    radial-gradient(circle at 48% 120%,rgba(63,141,255,.12),transparent 38%),
    linear-gradient(180deg,#100619 0%,#07030b 48%,#020105 100%)!important;
  background-attachment:fixed!important;
}
body.esp-command-center-shell:before{
  content:"";position:fixed;inset:0;pointer-events:none;z-index:-1;opacity:.50;
  background-image:
    radial-gradient(circle at 15% 16%,rgba(255,255,255,.72) 0 1px,transparent 1.4px),
    radial-gradient(circle at 73% 12%,rgba(233,187,88,.82) 0 1px,transparent 1.5px),
    radial-gradient(circle at 88% 55%,rgba(214,55,189,.78) 0 1px,transparent 1.5px),
    radial-gradient(circle at 35% 68%,rgba(78,148,255,.62) 0 1px,transparent 1.3px);
  background-size:173px 173px,229px 229px,307px 307px,251px 251px;
}
.brand{display:flex!important;align-items:center!important;gap:12px!important;color:#fff!important;text-shadow:0 0 20px rgba(214,55,189,.25)}
.brand:before{
  content:"";display:block;flex:0 0 auto;width:58px;height:58px;border-radius:50%;
  background:url('/brand/command-center-mark.svg') center/contain no-repeat;
  box-shadow:0 0 0 1px rgba(233,187,88,.68),0 0 24px rgba(214,55,189,.43),0 0 42px rgba(112,48,184,.18);
}
.brand small,.brand b,.eyebrow{color:var(--espcc-gold-hi)!important;text-shadow:0 0 14px rgba(233,187,88,.2)}
.card,.panel,.side,.hero-card,.tile,.form-card{
  background:linear-gradient(145deg,rgba(28,13,39,.96),rgba(8,4,13,.97))!important;
  border-color:rgba(233,187,88,.22)!important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.035),0 20px 65px rgba(0,0,0,.35)!important;
}
.hero-card{position:relative;overflow:hidden}
.hero-card:before{
  content:"";display:block;width:154px;height:154px;margin:0 auto 16px;
  background:url('/brand/command-center-mark.svg') center/contain no-repeat;
  filter:drop-shadow(0 0 22px rgba(214,55,189,.35));
}
.card:hover,.tile:hover{border-color:rgba(214,55,189,.42)!important}
.btn,button{
  border-color:rgba(233,187,88,.25)!important;
  background:linear-gradient(180deg,rgba(45,22,57,.94),rgba(24,11,31,.96))!important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.04);
}
.btn:hover,button:hover{border-color:rgba(233,187,88,.65)!important;transform:translateY(-1px)}
.btn.primary,button.primary,.primary{
  background:linear-gradient(135deg,#ffedb4 0%,#e9bb58 38%,#ad7020 100%)!important;
  color:#160b18!important;border-color:#f5d77d!important;
  box-shadow:0 8px 28px rgba(233,187,88,.20),inset 0 1px 0 rgba(255,255,255,.42)!important;
}
input,select,textarea{
  background:linear-gradient(180deg,rgba(9,5,12,.98),rgba(14,7,18,.98))!important;
  border-color:rgba(157,66,238,.35)!important;color:#fff!important;
}
input:focus,select:focus,textarea:focus{outline:none!important;border-color:var(--espcc-gold)!important;box-shadow:0 0 0 3px rgba(233,187,88,.11)!important}
h1,h2,h3{color:#fff!important}
h1 span,.gold,.tier,.price{background:linear-gradient(180deg,#fff1bd,#e6b44c 58%,#a86a1b);-webkit-background-clip:text;background-clip:text;color:transparent!important}
.badge,.pill{border-color:rgba(233,187,88,.38)!important;background:linear-gradient(135deg,rgba(91,28,104,.9),rgba(35,15,48,.94))!important;color:#ffe296!important}
.nav{border-bottom:1px solid rgba(233,187,88,.13)}
.footer{border-top-color:rgba(233,187,88,.14)!important}
.meter i,.progress i{background:linear-gradient(90deg,#7030b8,#d637bd,#e9bb58)!important}
.locked:after{background:linear-gradient(135deg,#6b285f,#351445)!important;color:#ffe9a4!important;border:1px solid rgba(233,187,88,.35)}
.alert.good{border-color:rgba(87,214,139,.42)!important}.alert{border-color:rgba(214,49,72,.33)!important}
.esp-hero-logo,.command-center-hero-mark{display:block;width:min(330px,72vw);aspect-ratio:1;margin:0 auto 18px;object-fit:contain;filter:drop-shadow(0 0 28px rgba(214,55,189,.34)) drop-shadow(0 0 55px rgba(233,187,88,.14))}
.esp-logo-small,.command-center-logo-small{width:88px;height:88px;object-fit:contain;filter:drop-shadow(0 0 20px rgba(214,55,189,.34))}
.command-center-master-art{display:block;width:min(620px,92vw);aspect-ratio:1;object-fit:cover;margin:0 auto;border-radius:28px;border:1px solid rgba(233,187,88,.36);box-shadow:0 28px 100px rgba(0,0,0,.55),0 0 60px rgba(112,48,184,.23)}
.esp-brand-line{height:1px;background:linear-gradient(90deg,transparent,#9b4eb9,#e9bb58,#9b4eb9,transparent);margin:14px 0 22px}
.esp-jewel{color:var(--espcc-ruby)!important;text-shadow:0 0 12px rgba(214,49,72,.4)}
.esp-history-fab{
  position:fixed;right:18px;bottom:18px;z-index:9998;text-decoration:none;color:#fff!important;font-weight:900;
  border:1px solid rgba(233,187,88,.5);border-radius:999px;padding:10px 14px 10px 42px;
  background:linear-gradient(135deg,rgba(58,20,70,.97),rgba(20,8,26,.97));box-shadow:0 10px 35px rgba(0,0,0,.4),0 0 20px rgba(214,55,189,.16);
}
.esp-history-fab:before{content:"";position:absolute;left:8px;top:50%;transform:translateY(-50%);width:26px;height:26px;background:url('/brand/command-center-mark.svg') center/contain no-repeat}
@media(max-width:700px){.brand:before{width:46px;height:46px}.esp-hero-logo,.command-center-hero-mark{width:min(230px,62vw)}.hero-card:before{width:118px;height:118px}.esp-history-fab{right:10px;bottom:10px;font-size:.82rem}.command-center-master-art{border-radius:18px}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{scroll-behavior:auto!important;transition-duration:.01ms!important;animation-duration:.01ms!important;animation-iteration-count:1!important}.btn:hover,button:hover{transform:none!important}}
"""

# Historical Python export retained only for compatibility. The browser route serves
# COMMAND_CENTER_THEME_CSS directly, so this marker never reintroduces the retired logo URL
# into current page styling.
ESP_THEME_CSS = COMMAND_CENTER_THEME_CSS + "\n/* legacy route compatibility: /brand/esp-logo.webp */\n"


def brand_head() -> str:
    return (
        "<link rel='stylesheet' href='/brand/theme.css'>"
        "<link rel='icon' type='image/webp' href='/favicon.webp'>"
        "<meta name='theme-color' content='#08040f'>"
        f"<meta name='application-name' content='{PRODUCT_FULL_NAME}'>"
    )


def hero_logo() -> str:
    return (
        "<img class='command-center-hero-mark esp-hero-logo' "
        "src='/brand/command-center-mark.svg' "
        f"alt='{PRODUCT_FULL_NAME} — {ENDORSEMENT}'>"
    )


def master_brand_art() -> str:
    return (
        "<img class='command-center-master-art' src='/brand/command-center-art.webp' "
        f"alt='{PRODUCT_FULL_NAME} — {ENDORSEMENT}. {TAGLINE}'>"
    )


@router.get('/brand/theme.css', include_in_schema=False)
def theme_css() -> Response:
    return Response(COMMAND_CENTER_THEME_CSS, media_type='text/css; charset=utf-8', headers={'Cache-Control':'public, max-age=3600'})


@router.get('/brand/command-center-mark.svg', include_in_schema=False)
def command_center_mark() -> FileResponse:
    return FileResponse(COMMAND_CENTER_MARK_PATH, media_type='image/svg+xml', headers={'Cache-Control':'public, max-age=86400'})


@router.get('/brand/command-center-art.webp', include_in_schema=False)
def command_center_art() -> FileResponse:
    return FileResponse(COMMAND_CENTER_ART_PATH, media_type='image/webp', headers={'Cache-Control':'public, max-age=86400'})


# Compatibility route: older templates can keep their historical URL while receiving the
# current Command Center artwork. New UI must use the command-center paths above.
@router.get('/brand/esp-logo.webp', include_in_schema=False)
def legacy_brand_logo() -> FileResponse:
    return FileResponse(COMMAND_CENTER_ART_PATH, media_type='image/webp', headers={'Cache-Control':'public, max-age=86400'})


@router.get('/favicon.webp', include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(COMMAND_CENTER_ART_PATH, media_type='image/webp', headers={'Cache-Control':'public, max-age=86400'})
