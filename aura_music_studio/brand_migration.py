from __future__ import annotations

from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


# Compatibility bridge: historical product copy may still exist inside legacy modules
# and persisted templates. This middleware makes Pulsar-Frequency House authoritative
# at the HTTP boundary without renaming storage keys, cookies or package imports.
#
# A few presentation-only replacements below also keep the original compressed landing
# template current while larger workspaces evolve independently. They intentionally do
# not claim an external renderer/model is live merely because its software adapter exists.
_AURA_CORE_HOME_SECTION = """<section class='wrap section' data-pfh-aura-core='0.20'><div class='eyebrow'>Aura Core 0.20</div><h2>Your creative house now has an operating intelligence layer.</h2><p class='sectionintro'>Aura is more than a prompt box: the connected software layer provides private realtime conversations, project-aware tools, hands-free voice workflows, versioned Artifacts, durable Tasks, Notifications, Aura Today, verified multi-step tool chains and encrypted read-only workspace connectors when a member authorizes them.</p><div class='grid'><article class='card' style='--accent:#a66bff'><div class='icon'>🧠</div><h3>Aura Intelligence</h3><p>Persistent private conversations with Fast, Auto, Deep and Creative modes, custom Aura Profiles, project context, research and verified tool workflows.</p><span class='status'>Aura Core 0.20 connected</span><div class='featurelist'><span>Realtime</span><span>Profiles</span><span>Research</span><span>Project-aware</span></div></article><article class='card' style='--accent:#5de7ff'><div class='icon'>🎙️</div><h3>Voice & Embodied Host</h3><p>Single-turn speech, optional hands-free Voice Conversation and an embodied Aura state/runtime. The browser 3D renderer is implemented; the final production rig remains a deployment asset.</p><span class='status'>Host runtime connected · final rig pending</span><div class='featurelist'><span>Listening</span><span>Thinking</span><span>Speaking</span><span>3D-ready runtime</span></div></article><article class='card' style='--accent:#77e0a6'><div class='icon'>☀️</div><h3>Aura Today & Tasks</h3><p>At-a-glance Calendar/Gmail metadata, pinned-project context, durable reminders, scheduled read-only briefings and private notifications across sessions.</p><span class='status'>Workspace intelligence connected</span><div class='featurelist'><span>Today</span><span>Tasks</span><span>Briefings</span><span>Notifications</span></div></article><article class='card' style='--accent:#f4c873'><div class='icon'>▤</div><h3>Artifacts & Safe Tools</h3><p>Versioned documents, lyrics, prompts, data and code with restore history. Code execution remains disabled on the web host and requires a separately configured isolated sandbox.</p><span class='status'>Artifacts connected · sandbox optional</span><div class='featurelist'><span>Versions</span><span>Restore</span><span>Data tools</span><span>Isolation</span></div></article></div><div class='heroactions'><a class='btn primary' href='/aura-intelligence'>Open Aura Intelligence</a><a class='btn' href='/creative-house'>Open Creative House</a></div><p class='tiny'>External AI models, speech services, renderers, OAuth services and the final 3D rig have separate runtime/configuration states. Pulsar-Frequency House reports those states instead of presenting an unconfigured backend as complete.</p></section>"""
_LANDING_MEMBERSHIP_MARKER = "<section class='wrap section'><div class='eyebrow'>Memberships</div>"

_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (
        "Powered by Elevate Souls Productions and Aura AI Systems",
        "Powered by Elevate Souls Productions & Aura AI Systems",
    ),
    (
        "Elevate Souls Productions Presents: The Live Sound Studio",
        "Pulsar-Frequency House",
    ),
    (
        "Elevate Souls Productions Presents: Live Sound Studio",
        "Pulsar-Frequency House",
    ),
    (
        "Elevate Souls Productions Presents",
        "Powered by Elevate Souls Productions & Aura AI Systems",
    ),
    ("4Infinity Creative Studios", "Pulsar-Frequency House"),
    ("Cosmic Creative Studios", "Pulsar-Frequency House"),
    ("Cosmic Creation Studios", "Pulsar-Frequency House"),
    ("The Live Sound Studio", "Pulsar-Frequency House"),
    ("Live Sound Studio", "Pulsar-Frequency House"),
    ("4Infinity", "Pulsar-Frequency"),
    (
        "Music, Video, Image & Creator Intelligence",
        "For Professional Creation Beyond The Cosmos",
    ),
    ("Music Making for Professionals", "For Professional Creation Beyond The Cosmos"),
    # Current landing-page presentation upgrades. Keep these exact and narrow so legacy
    # API payloads/project data are not semantically rewritten.
    ("href='#suite'>Creative House", "href='/creative-house'>Creative House"),
    ("Workspace architecture staged", "Creative DNA + renderer bridge connected"),
    ("Aura routes connected", "Aura Core 0.20 connected"),
    ("Unified project layer in build", "Creative DNA project layer connected"),
    ("<h3>Base</h3>", "<h3>Basic</h3>"),
    ("href='/signup?plan=base'>Choose Base", "href='/signup?plan=base'>Choose Basic"),
    (
        "The target project model keeps music sections, stems, scenes, visual layers, voice assets and generation settings addressable",
        "The Creative DNA project model keeps music sections, stems, scenes, visual layers, voice assets and generation settings addressable",
    ),
    (
        "The platform is being built around continuity:",
        "The platform is built around Creative DNA continuity:",
    ),
    (
        "The current real-audio music engine, owner controls and ESP permission systems remain underneath the new master brand while the unified video, image and multimodal editing layers are expanded.",
        "The real-audio music engine, Creative DNA layer, Aura Core, owner controls and ESP permission systems are connected under the master brand. Image/video renderer bridges are present while their external generation backends remain deployment-configurable.",
    ),
)

_TEXTUAL_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/manifest+json",
    "application/javascript",
    "application/xml",
    "image/svg+xml",
)


def rebrand_text(value: str) -> str:
    for old, new in _REPLACEMENTS:
        value = value.replace(old, new)
    if "data-pfh-aura-core='0.20'" not in value and _LANDING_MEMBERSHIP_MARKER in value:
        value = value.replace(
            _LANDING_MEMBERSHIP_MARKER,
            _AURA_CORE_HOME_SECTION + _LANDING_MEMBERSHIP_MARKER,
            1,
        )
    return value


def inject_song_dna_lock_entry(value: str, path: str) -> str:
    clean_path = (path or "").split("?", 1)[0].strip()
    parts = [part for part in clean_path.strip("/").split("/") if part]
    if len(parts) != 2 or parts[0] != "song-editor" or not parts[1]:
        return value
    if "id='songDnaLocksEntry'" in value or "id=\"songDnaLocksEntry\"" in value:
        return value
    project = quote(parts[1], safe="")
    entry = (
        f"<a id='songDnaLocksEntry' href='/song-editor/{project}/locks' "
        "style='position:fixed;right:18px;bottom:18px;z-index:90;border:1px solid #edca7255;"
        "border-radius:999px;padding:10px 14px;background:#0a0d18f2;color:#edca72;"
        "font:800 .78rem Inter,system-ui,sans-serif;text-decoration:none;box-shadow:0 12px 36px #0008'>"
        "🔒 Preserve Locks</a>"
    )
    if "</body>" not in value:
        return value
    return value.replace("</body>", entry + "</body>", 1)


class BrandMigrationMiddleware(BaseHTTPMiddleware):
    """Rewrite legacy public-facing product copy to Pulsar-Frequency House.

    Binary audio/video/image responses are passed through untouched. Response headers,
    including repeated Set-Cookie headers, are preserved while Content-Length is
    recalculated after text replacement.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        content_type = (response.headers.get("content-type") or "").lower()
        if not any(content_type.startswith(prefix) for prefix in _TEXTUAL_CONTENT_TYPES):
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                background=response.background,
            )

        branded_text = rebrand_text(text)
        if request.method.upper() == "GET":
            branded_text = inject_song_dna_lock_entry(branded_text, request.url.path)
        branded = branded_text.encode("utf-8")
        migrated = Response(
            content=branded,
            status_code=response.status_code,
            background=response.background,
        )

        raw_headers = [
            (key, value)
            for key, value in response.raw_headers
            if key.lower() != b"content-length"
        ]
        raw_headers.append((b"content-length", str(len(branded)).encode("ascii")))
        migrated.raw_headers = raw_headers
        return migrated
