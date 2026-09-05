from __future__ import annotations

import json
from html import escape
from urllib.parse import urljoin

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from .branding import AI_PRODUCER_NAME, PRODUCT_FULL_NAME, PRODUCT_NAME, PRODUCT_SHORT_NAME, TAGLINE
from .mailer import _public_url

router = APIRouter(tags=["Public Discovery"])

PUBLIC_PAGES = {
    "/": (PRODUCT_NAME, f"AI music creation, recording and professional production with {AI_PRODUCER_NAME} inside the Elevate Souls Productions Content Creation Command Center."),
    "/pricing": (f"{PRODUCT_NAME} Pricing", "Free, Basic and Unlimited Pro membership options for the Elevate Souls Productions Content Creation Command Center."),
    "/ai-music-studio": ("AI Music Studio", f"Create, record, arrange, mix and master music with {AI_PRODUCER_NAME} inside the Content Creation Command Center."),
    "/ai-song-generator": ("AI Song Generator", f"Turn lyrics, prompts or recorded ideas into real-audio-first song projects with {AI_PRODUCER_NAME}."),
    "/backing-track-maker": ("AI Backing Track Maker", "Build a complete backing production around an authorised vocal or instrument recording."),
    "/stem-splitter": ("Music Stem Splitter", "Separate vocals and instrument groups for remixing, practice and production workflows."),
    "/ai-mastering": ("AI-Assisted Music Mastering", "Master music with presets, loudness control, translation checks and advanced Unlimited Pro options."),
    "/ai-vocal-studio": ("AI Vocal Studio", f"Record vocals, apply {AI_PRODUCER_NAME} Tune and effects, create harmonies and use consent-approved voice workflows."),
}


def _base() -> str:
    value = _public_url().rstrip("/") + "/"
    return value


def _page(path: str, headline: str, intro: str, bullets: list[str]) -> HTMLResponse:
    title, description = PUBLIC_PAGES[path]
    canonical = urljoin(_base(), path.lstrip("/"))
    items = "".join(f"<li>{escape(item)}</li>" for item in bullets)
    body = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{escape(title)} — {escape(PRODUCT_NAME)}</title>
<meta name='description' content='{escape(description, quote=True)}'>
<meta name='robots' content='index,follow,max-image-preview:large'>
<link rel='canonical' href='{escape(canonical, quote=True)}'>
<meta property='og:type' content='website'><meta property='og:title' content='{escape(title, quote=True)}'><meta property='og:description' content='{escape(description, quote=True)}'><meta property='og:url' content='{escape(canonical, quote=True)}'><meta property='og:image' content='{escape(urljoin(_base(), "brand/esp-logo.webp"), quote=True)}'>
<meta name='twitter:card' content='summary_large_image'><meta name='theme-color' content='#120818'><link rel='manifest' href='/manifest.webmanifest'>
<style>*{{box-sizing:border-box}}body{{margin:0;font-family:Inter,system-ui,sans-serif;color:#fff}}.wrap{{max-width:1120px;margin:auto;padding:24px}}.nav{{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap}}.brand{{font-size:1.1rem;font-weight:950}}a{{color:inherit}}.btn{{display:inline-block;text-decoration:none;padding:11px 15px;border-radius:11px;border:1px solid #563367;font-weight:850;margin:4px}}.primary{{background:linear-gradient(135deg,#ffe7a6,#e8ba59,#b67a23);color:#180b18}}.hero{{display:grid;grid-template-columns:1fr 330px;gap:35px;align-items:center;padding:75px 0 45px}}.hero h1{{font-size:clamp(2.5rem,6vw,5rem);line-height:.95;margin:.15em 0}}.hero p,.muted{{color:#cdbfd4;line-height:1.7}}.logo{{width:100%;max-width:300px;border-radius:50%;box-shadow:0 0 50px #b22ca955}}.card{{background:#150b1d;border:1px solid #50305e;border-radius:20px;padding:24px;margin:18px 0}}li{{padding:6px 0;line-height:1.5}}.eyebrow{{text-transform:uppercase;letter-spacing:.14em;color:#ffe29a;font-weight:900;font-size:.76rem}}@media(max-width:760px){{.hero{{grid-template-columns:1fr;padding-top:45px}}.hero img{{order:-1;max-width:220px;margin:auto}}}}</style></head><body><div class='wrap'><nav class='nav'><a class='brand' href='/' style='text-decoration:none'>{escape(PRODUCT_NAME)}<small style='display:block;color:#e7b953'>Elevate Souls Productions</small></a><div><a class='btn' href='/pricing'>Pricing</a><a class='btn' href='/signin'>Sign in</a><a class='btn primary' href='/signup'>Join Studio</a></div></nav>
<section class='hero'><div><div class='eyebrow'>{escape(PRODUCT_FULL_NAME)}</div><h1>{escape(headline)}</h1><p>{escape(intro)}</p><a class='btn primary' href='/signup'>Start with Free</a><a class='btn' href='/pricing'>Compare memberships</a></div><img class='logo' src='/brand/esp-logo.webp' alt='Elevate Souls Productions logo'></section>
<section class='card'><h2>What {escape(AI_PRODUCER_NAME)} can do</h2><ul>{items}</ul></section>
<section class='card'><h2>Real-audio-first production</h2><p class='muted'>MIDI, notation and MusicXML may guide timing, harmony or performance control, but the Content Creation Command Center does not accept General MIDI or SoundFont audio as a finished master. Final audible music must come from recorded, neural or approved hybrid waveform audio.</p></section>
<footer class='muted' style='padding:30px 0 55px'>{escape(PRODUCT_FULL_NAME)} · {escape(TAGLINE)}</footer></div></body></html>"""
    return HTMLResponse(body)


@router.get("/ai-music-studio", response_class=HTMLResponse)
def ai_music_studio():
    return _page(
        "/ai-music-studio",
        "An AI music studio built around the whole production workflow.",
        "Start from lyrics, a prompt, a vocal, an instrument or a browser recording and continue into arrangement, effects, tuning, mixing, mastering and export.",
        ["Original song creation", "Browser recording", "Build Around Upload", "Generative multitrack production", f"{AI_PRODUCER_NAME} Tune and FX", "Mixing/mastering", "Take lanes and project history"],
    )


@router.get("/ai-song-generator", response_class=HTMLResponse)
def ai_song_generator():
    return _page(
        "/ai-song-generator",
        "Turn an idea into a complete song project.",
        f"{AI_PRODUCER_NAME} can structure an original song from a concept or lyrics, apply genre/instrument choices and route the project into real-audio generation rather than stopping at a MIDI sketch.",
        ["Prompt and lyric input", "Verse/chorus/bridge structure", "Genre, BPM, key and instrument controls", "Regeneration workflow", "Basic daily confirmed track", "Unlimited Pro full-song production"],
    )


@router.get("/backing-track-maker", response_class=HTMLResponse)
def backing_track_maker():
    return _page(
        "/backing-track-maker",
        "Build the production around your performance.",
        f"Upload or record an authorised lead vocal, guitar, piano, bass, drums or other musical anchor and let {AI_PRODUCER_NAME} create complementary parts around it.",
        ["Preserve the uploaded anchor", "Select guitar/drum/bass/keyboard and other performance types", "Optional original lead/backing vocals", "Basic complete-mix workflow", "Unlimited Pro editable multitrack generation", "AutoMix, effects and mastering"],
    )


@router.get("/stem-splitter", response_class=HTMLResponse)
def stem_splitter():
    return _page(
        "/stem-splitter",
        "Split a mix into usable production stems.",
        "The Studio routes separation through configured local/open models and exposes progressively deeper split modes by membership tier.",
        ["Reduced two/four-stem workflows", "Unlimited Pro six-stem and detailed routes", "Vocal/instrument separation", "Private per-member outputs", "Background processing queue", "DAW-ready follow-on editing"],
    )


@router.get("/ai-mastering", response_class=HTMLResponse)
def ai_mastering():
    return _page(
        "/ai-mastering",
        "Master for character, loudness and translation.",
        f"{AI_PRODUCER_NAME} Master combines one-click character choices with engineering controls and optional local reference-mastering tools.",
        ["Multiple mastering characters", "LUFS and true-peak control", "EQ and stereo-width adjustment", "Translation/mono checks", "Unlimited Pro reference mastering", "Album/EP consistency workflow"],
    )


@router.get("/ai-vocal-studio", response_class=HTMLResponse)
def ai_vocal_studio():
    return _page(
        "/ai-vocal-studio",
        f"Record, tune, process and arrange vocals with {AI_PRODUCER_NAME}.",
        f"Capture a dry vocal in the browser, build a track around it, tune it, add effects and create harmonies while keeping voice-conversion workflows consent-gated.",
        ["Browser vocal recording", f"{AI_PRODUCER_NAME} Tune", "Vocal FX presets", "Harmony Architect", "Contextual backing harmonies", "Consent-approved voice profiles", "Take lanes and phrase comping on Unlimited Pro"],
    )


@router.get("/robots.txt", include_in_schema=False)
def robots() -> Response:
    sitemap = urljoin(_base(), "sitemap.xml")
    text = "\n".join([
        "User-agent: *",
        "Allow: /$",
        "Allow: /pricing$",
        "Allow: /ai-music-studio$",
        "Allow: /ai-song-generator$",
        "Allow: /backing-track-maker$",
        "Allow: /stem-splitter$",
        "Allow: /ai-mastering$",
        "Allow: /ai-vocal-studio$",
        "Disallow: /owner",
        "Disallow: /dashboard",
        "Disallow: /studio",
        "Disallow: /production-suite",
        "Disallow: /recording-studio",
        "Disallow: /take-manager",
        "Disallow: /history",
        "Disallow: /projects/",
        "Disallow: /membership/",
        "Disallow: /auth/",
        "Disallow: /admin/",
        "Disallow: /privacy/",
        "Disallow: /system/",
        f"Sitemap: {sitemap}",
        "",
    ])
    return Response(text, media_type="text/plain; charset=utf-8")


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap() -> Response:
    base = _base()
    urls = []
    for path in PUBLIC_PAGES:
        loc = base if path == "/" else urljoin(base, path.lstrip("/"))
        priority = "1.0" if path == "/" else ("0.9" if path == "/pricing" else "0.8")
        urls.append(f"<url><loc>{escape(loc)}</loc><changefreq>weekly</changefreq><priority>{priority}</priority></url>")
    xml = "<?xml version='1.0' encoding='UTF-8'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>" + "".join(urls) + "</urlset>"
    return Response(xml, media_type="application/xml; charset=utf-8")


@router.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> Response:
    payload = {
        "name": PRODUCT_FULL_NAME,
        "short_name": "ESP Command Center",
        "description": f"AI music creation, recording and professional production with {AI_PRODUCER_NAME} inside {PRODUCT_SHORT_NAME}.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#050308",
        "theme_color": "#120818",
        "icons": [{"src": "/brand/esp-logo.webp", "sizes": "512x512", "type": "image/webp", "purpose": "any maskable"}],
    }
    return Response(json.dumps(payload), media_type="application/manifest+json")


@router.get("/service-worker.js", include_in_schema=False)
def service_worker() -> Response:
    script = r"""
const CACHE='esp-live-sound-public-v1';
const PUBLIC=new Set(['/', '/pricing', '/ai-music-studio', '/ai-song-generator', '/backing-track-maker', '/stem-splitter', '/ai-mastering', '/ai-vocal-studio', '/brand/theme.css', '/brand/esp-logo.webp']);
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(c=>c.addAll(['/brand/theme.css','/brand/esp-logo.webp']))));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('fetch',event=>{
  const u=new URL(event.request.url);
  if(event.request.method!=='GET'||u.origin!==self.location.origin||!PUBLIC.has(u.pathname)) return;
  event.respondWith(fetch(event.request).then(r=>{const copy=r.clone();caches.open(CACHE).then(c=>c.put(event.request,copy));return r;}).catch(()=>caches.match(event.request)));
});
"""
    return Response(script, media_type="application/javascript; charset=utf-8", headers={"Service-Worker-Allowed": "/"})
