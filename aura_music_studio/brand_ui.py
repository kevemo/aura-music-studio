from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

from .branding import BRAND_ASSET_FILENAME, PRODUCT_FULL_NAME

router = APIRouter(tags=["ESP Brand"])
STATIC_DIR = Path(__file__).resolve().parent / "static"
LOGO_PATH = STATIC_DIR / BRAND_ASSET_FILENAME

ESP_THEME_CSS = r"""
:root{
  --esp-black:#050308;
  --esp-ink:#0b0611;
  --esp-panel:#160d1d;
  --esp-panel-2:#21102c;
  --esp-purple:#6f2aa8;
  --esp-violet:#a53add;
  --esp-magenta:#d12a9f;
  --esp-gold:#e7b953;
  --esp-gold-hi:#ffe29a;
  --esp-silver:#d9d7e0;
  --esp-ruby:#d42b35;
  --esp-line:#523263;
  --esp-text:#fffaf0;
  --esp-muted:#cdbfd4;
  --bg:var(--esp-black);
  --panel:var(--esp-panel);
  --panel2:var(--esp-panel-2);
  --gold:var(--esp-gold);
  --text:var(--esp-text);
  --muted:var(--esp-muted);
  --line:var(--esp-line);
}
html{background:var(--esp-black)}
body{
  color:var(--esp-text)!important;
  background:
    radial-gradient(circle at 50% -10%,rgba(209,42,159,.24),transparent 34%),
    radial-gradient(circle at 9% 20%,rgba(111,42,168,.24),transparent 31%),
    radial-gradient(circle at 92% 33%,rgba(231,185,83,.12),transparent 24%),
    radial-gradient(circle at 50% 120%,rgba(111,42,168,.20),transparent 38%),
    linear-gradient(180deg,#110719 0%,#07040a 46%,#030205 100%)!important;
  background-attachment:fixed!important;
}
body:before{
  content:"";position:fixed;inset:0;pointer-events:none;z-index:-1;opacity:.52;
  background-image:
    radial-gradient(circle at 15% 16%,rgba(255,255,255,.7) 0 1px,transparent 1.4px),
    radial-gradient(circle at 73% 12%,rgba(231,185,83,.8) 0 1px,transparent 1.5px),
    radial-gradient(circle at 88% 55%,rgba(209,42,159,.8) 0 1px,transparent 1.5px),
    radial-gradient(circle at 35% 68%,rgba(255,255,255,.55) 0 1px,transparent 1.3px);
  background-size:173px 173px,229px 229px,307px 307px,251px 251px;
}
.brand{display:flex!important;align-items:center!important;gap:12px!important;color:#fff!important;text-shadow:0 0 20px rgba(209,42,159,.25)}
.brand:before{
  content:"";display:block;flex:0 0 auto;width:58px;height:58px;border-radius:50%;
  background:url('/brand/esp-logo.webp') center/cover no-repeat;
  box-shadow:0 0 0 1px rgba(231,185,83,.7),0 0 22px rgba(209,42,159,.55),0 0 34px rgba(231,185,83,.18);
}
.brand small,.brand b,.eyebrow{color:var(--esp-gold-hi)!important;text-shadow:0 0 14px rgba(231,185,83,.2)}
.card,.panel,.side,.hero-card,.tile,.form-card{
  background:linear-gradient(145deg,rgba(28,13,37,.96),rgba(10,5,14,.97))!important;
  border-color:rgba(231,185,83,.22)!important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.035),0 20px 65px rgba(0,0,0,.35)!important;
}
.hero-card{position:relative;overflow:hidden}
.hero-card:before{
  content:"";display:block;width:154px;height:154px;margin:0 auto 16px;border-radius:50%;
  background:url('/brand/esp-logo.webp') center/cover no-repeat;
  box-shadow:0 0 0 1px rgba(231,185,83,.58),0 0 34px rgba(209,42,159,.33);
}
.card:hover,.tile:hover{border-color:rgba(209,42,159,.42)!important}
.btn,button{
  border-color:rgba(231,185,83,.25)!important;
  background:linear-gradient(180deg,rgba(45,22,57,.94),rgba(24,11,31,.96))!important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.04);
}
.btn:hover,button:hover{border-color:rgba(231,185,83,.65)!important;transform:translateY(-1px)}
.btn.primary,button.primary,.primary{
  background:linear-gradient(135deg,#ffe7a6 0%,#e8ba59 38%,#b67a23 100%)!important;
  color:#160b18!important;border-color:#f4d47d!important;
  box-shadow:0 8px 28px rgba(231,185,83,.20),inset 0 1px 0 rgba(255,255,255,.42)!important;
}
input,select,textarea{
  background:linear-gradient(180deg,rgba(9,5,12,.98),rgba(14,7,18,.98))!important;
  border-color:rgba(159,91,184,.35)!important;color:#fff!important;
}
input:focus,select:focus,textarea:focus{outline:none!important;border-color:var(--esp-gold)!important;box-shadow:0 0 0 3px rgba(231,185,83,.11)!important}
h1,h2,h3{color:#fff!important}
h1 span,.gold,.tier,.price{background:linear-gradient(180deg,#fff1bd,#e6b44c 58%,#a86a1b);-webkit-background-clip:text;background-clip:text;color:transparent!important}
.badge,.pill{border-color:rgba(231,185,83,.38)!important;background:linear-gradient(135deg,rgba(91,28,104,.9),rgba(35,15,48,.94))!important;color:#ffe296!important}
.nav{border-bottom:1px solid rgba(231,185,83,.13)}
.footer{border-top-color:rgba(231,185,83,.14)!important}
.meter i,.progress i{background:linear-gradient(90deg,#8c2fc1,#d12a9f,#e8b953)!important}
.locked:after{background:linear-gradient(135deg,#6b285f,#351445)!important;color:#ffe9a4!important;border:1px solid rgba(231,185,83,.35)}
.alert.good{border-color:rgba(87,214,139,.42)!important}.alert{border-color:rgba(212,43,53,.33)!important}
.esp-hero-logo{display:block;width:min(330px,72vw);aspect-ratio:1;margin:0 auto 18px;border-radius:50%;object-fit:cover;box-shadow:0 0 0 1px rgba(231,185,83,.65),0 0 45px rgba(209,42,159,.34),0 0 90px rgba(111,42,168,.22)}
.esp-logo-small{width:88px;height:88px;object-fit:cover;border-radius:50%;box-shadow:0 0 28px rgba(209,42,159,.38)}
.esp-brand-line{height:1px;background:linear-gradient(90deg,transparent,#9b4eb9,#e7b953,#9b4eb9,transparent);margin:14px 0 22px}
.esp-jewel{color:var(--esp-ruby)!important;text-shadow:0 0 12px rgba(212,43,53,.4)}
.esp-history-fab{
  position:fixed;right:18px;bottom:18px;z-index:9998;text-decoration:none;color:#fff!important;font-weight:900;
  border:1px solid rgba(231,185,83,.5);border-radius:999px;padding:10px 14px 10px 42px;
  background:linear-gradient(135deg,rgba(58,20,70,.97),rgba(20,8,26,.97));box-shadow:0 10px 35px rgba(0,0,0,.4),0 0 20px rgba(209,42,159,.16);
}
.esp-history-fab:before{content:"";position:absolute;left:8px;top:50%;transform:translateY(-50%);width:26px;height:26px;border-radius:50%;background:url('/brand/esp-logo.webp') center/cover no-repeat}
@media(max-width:700px){.brand:before{width:46px;height:46px}.esp-hero-logo{width:min(230px,62vw)}.hero-card:before{width:118px;height:118px}.esp-history-fab{right:10px;bottom:10px;font-size:.82rem}}
"""


def brand_head() -> str:
    return "<link rel='stylesheet' href='/brand/theme.css'><link rel='icon' type='image/webp' href='/brand/esp-logo.webp'>"


def hero_logo() -> str:
    return f"<img class='esp-hero-logo' src='/brand/esp-logo.webp' alt='{PRODUCT_FULL_NAME}'>"


@router.get('/brand/theme.css', include_in_schema=False)
def theme_css() -> Response:
    return Response(ESP_THEME_CSS, media_type='text/css; charset=utf-8', headers={'Cache-Control':'public, max-age=3600'})


@router.get('/brand/esp-logo.webp', include_in_schema=False)
def brand_logo() -> FileResponse:
    return FileResponse(LOGO_PATH, media_type='image/webp', filename='ESP-Content-Creation-Command-Center.webp', headers={'Cache-Control':'public, max-age=86400'})


@router.get('/favicon.webp', include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(LOGO_PATH, media_type='image/webp', headers={'Cache-Control':'public, max-age=86400'})
