from __future__ import annotations

from html import escape
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .api import app as _base_app
from .commercial_entitlements import can_download_media
from .creative_project import CreativeProjectStore
from .tenant_storage import list_project_dirs
from .universal_creative_catalogue_api import router as _runtime_universal_router
from .universal_creative_library import router as _legacy_universal_router

router = APIRouter(prefix="/creative", tags=["creative-library"])

_MEDIA_KINDS = {"image", "video", "audio", "music"}
_MEDIA_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif",
    ".mp4", ".webm", ".mov", ".m4v", ".mkv", ".avi",
    ".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg",
}


def _mount_universal_creative_routes() -> None:
    """Mount the universal catalogue on the canonical base app before app.py composes overlays.

    Specific runtime/menu routes must precede the legacy ``/{item_id:path}`` catch-all.
    Importing this module is already part of the production app composition path, so this keeps
    the catalogue reachable without creating a second FastAPI application or bypassing the
    membership/security middleware installed by ``aura_music_studio.api``.
    """
    paths = {getattr(route, "path", "") for route in _base_app.router.routes}
    if "/command-center/api/universal-library/menus" not in paths:
        _base_app.include_router(_runtime_universal_router)
        paths = {getattr(route, "path", "") for route in _base_app.router.routes}
    if "/command-center/api/universal-library" not in paths:
        _base_app.include_router(_legacy_universal_router)


_mount_universal_creative_routes()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    return member


def _valid_local_media(project: Path, source_ref: str) -> bool:
    clean = str(source_ref or "").strip()
    if not clean:
        return False
    relative = Path(clean)
    if relative.is_absolute() or relative.suffix.lower() not in _MEDIA_SUFFIXES:
        return False
    root = project.resolve()
    target = (root / relative).resolve()
    return root in target.parents and target.is_file()


def scan_creative_library(member, project_dirs: list[Path] | None = None) -> list[dict]:
    """Return playable media metadata for the current member's Creative DNA projects.

    The response intentionally contains element-based application URLs rather than local
    filesystem paths. The media endpoint re-authorizes every request and applies download
    entitlements independently of this catalog.
    """
    items: list[dict] = []
    for project in project_dirs if project_dirs is not None else list_project_dirs():
        try:
            manifest = CreativeProjectStore(project).load()
        except Exception:
            continue
        active_ids = set(getattr(manifest, "active_element_ids", []) or [])
        project_title = str(getattr(manifest, "title", "") or project.name)
        for element in getattr(manifest, "elements", []) or []:
            kind = str(getattr(element, "kind", "") or "")
            source_ref = str(getattr(element, "source_ref", "") or "")
            if kind not in _MEDIA_KINDS or not _valid_local_media(project, source_ref):
                continue
            element_id = str(getattr(element, "id", ""))
            if not element_id:
                continue
            media_url = f"/creative/projects/{project.name}/elements/{element_id}/media"
            item = {
                "id": f"{project.name}:{element_id}",
                "element_id": element_id,
                "project": project.name,
                "project_title": project_title,
                "title": str(getattr(element, "label", "") or kind.title()),
                "kind": kind,
                "role": str(getattr(element, "role", "") or ""),
                "status": str(getattr(element, "status", "") or ""),
                "current": element_id in active_ids,
                "media_url": media_url,
                "download_url": media_url + "?download=true" if can_download_media(member, kind) else None,
                "download_allowed": can_download_media(member, kind),
                "download_reason": (
                    "Included in this membership tier"
                    if can_download_media(member, kind)
                    else "Music/video downloads require Basic (£4.99) or Pro (£9.99)"
                ),
            }
            items.append(item)
    items.sort(key=lambda row: (row["project_title"].casefold(), 0 if row["current"] else 1, row["title"].casefold()))
    return items


@router.get("/api/library")
def creative_library_api(request: Request, kind: str = "", project: str = "", q: str = ""):
    member = _member(request)
    rows = scan_creative_library(member)
    kind = kind.strip().lower()
    project = project.strip()
    query = q.strip().casefold()
    if kind:
        if kind not in _MEDIA_KINDS:
            raise HTTPException(400, "kind must be image, video, audio or music")
        rows = [row for row in rows if row["kind"] == kind]
    if project:
        rows = [row for row in rows if row["project"] == project]
    if query:
        rows = [row for row in rows if query in " ".join((row["title"], row["project_title"], row["role"], row["kind"])).casefold()]
    return {
        "items": rows,
        "count": len(rows),
        "plan": member.plan.id,
        "private_member_library": True,
        "filesystem_paths_exposed": False,
    }


@router.get("/library", response_class=HTMLResponse, include_in_schema=False)
def creative_library_page(request: Request):
    member = _member(request)
    rows = scan_creative_library(member)
    plan = str(member.plan.id).title()
    cards: list[str] = []
    for item in rows:
        media = ""
        if item["kind"] == "image":
            media = f"<img loading='lazy' src='{escape(item['media_url'], quote=True)}' alt='{escape(item['title'], quote=True)}'>"
        elif item["kind"] == "video":
            media = "<div class='mediaIcon'>🎬</div>"
        else:
            media = "<div class='mediaIcon'>🎵</div>"
        play = ""
        if item["kind"] != "image":
            play = (
                f"<button class='btn primary' data-pulsar-play='{escape(item['media_url'], quote=True)}' "
                f"data-pulsar-id='{escape(item['id'], quote=True)}' data-pulsar-kind='{escape(item['kind'], quote=True)}' "
                f"data-pulsar-title='{escape(item['title'], quote=True)}' data-pulsar-project='{escape(item['project_title'], quote=True)}' "
                f"data-pulsar-element='{escape(item['element_id'], quote=True)}'>▶ Play</button>"
            )
        download = (
            f"<a class='btn' href='{escape(str(item['download_url']), quote=True)}'>Download</a>"
            if item["download_url"]
            else f"<span class='locked'>{escape(item['download_reason'])}</span>"
        )
        cards.append(
            "<article class='card' "
            f"data-kind='{escape(item['kind'], quote=True)}' data-search='{escape((item['title']+' '+item['project_title']+' '+item['role']).casefold(), quote=True)}'>"
            f"<div class='visual'>{media}</div><div class='top'><span class='pill'>{escape(item['kind'].upper())}</span>"
            f"<span class='pill {'good' if item['current'] else ''}'>{'CURRENT' if item['current'] else 'HISTORY'}</span></div>"
            f"<h3>{escape(item['title'])}</h3><p>{escape(item['project_title'])}</p><small>{escape(item['role'] or 'Creative output')}</small>"
            f"<div class='actions'>{play}<a class='btn' href='{escape(item['media_url'], quote=True)}' target='_blank' rel='noopener'>Preview</a>{download}</div></article>"
        )
    listing = "".join(cards) if cards else "<div class='empty'>No playable Creative DNA outputs yet. Create or import media in a project and it will appear here automatically.</div>"
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Creative Library — Pulsar-Frequency House</title><style>
    :root{{--line:#ffffff1e;--gold:#f2c86f;--violet:#9f70ff;--cyan:#5ce8ff;--muted:#c0bacb;--good:#79dfa6}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 8% 0,#43165e,transparent 31%),radial-gradient(circle at 92% 0,#123f59,transparent 28%),#05040a;color:#fff;font-family:Inter,system-ui,sans-serif;padding-bottom:110px}}a{{color:inherit;text-decoration:none}}button,input,select{{font:inherit}}.wrap{{width:min(1440px,calc(100% - 28px));margin:auto;padding:34px 0 70px}}.top,.actions,.filters{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}.top{{justify-content:space-between}}.eyebrow{{color:var(--gold);font-size:.7rem;text-transform:uppercase;letter-spacing:.15em;font-weight:950}}h1{{font-size:clamp(3rem,7vw,5.8rem);letter-spacing:-.06em;line-height:.93;margin:.12em 0}}p,.muted,small{{color:var(--muted);line-height:1.5}}.btn,input,select{{border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#ffffff08;color:#fff}}.btn{{font-weight:850;cursor:pointer}}.btn.primary{{border:0;background:linear-gradient(110deg,#f4dfa0,var(--gold),var(--violet));color:#160b1c}}.filters{{margin:18px 0}}.filters input{{flex:1;min-width:220px}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.card{{border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,#15101fea,#090711ee);padding:12px;min-width:0}}.visual{{height:150px;border-radius:13px;overflow:hidden;background:radial-gradient(circle,#3b2057,#090a12);display:grid;place-items:center;margin-bottom:10px}}.visual img{{width:100%;height:100%;object-fit:cover}}.mediaIcon{{font-size:3.1rem}}.card h3{{margin:9px 0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.card p{{margin:0 0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.pill{{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-size:.62rem;margin-right:4px}}.pill.good{{color:var(--good);border-color:#79dfa64c}}.actions{{margin-top:11px}}.locked{{font-size:.67rem;color:#d9b678;line-height:1.25}}.empty{{border:1px dashed #ffffff29;border-radius:18px;padding:28px;text-align:center;color:var(--muted)}}@media(max-width:1100px){{.grid{{grid-template-columns:repeat(3,1fr)}}}}@media(max-width:800px){{.grid{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:520px){{.grid{{grid-template-columns:1fr}}}}
    </style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Unified Creative Library · {escape(plan)} plan</div><h1>Your creations.</h1><p>Play music and videos in the persistent Pulsar Player, preview images, return to your Creative House, and download only where your current membership allows.</p></div><div class='actions'><a class='btn' href='/creative-house'>Creative House</a><a class='btn' href='/dashboard'>Dashboard</a></div></div><section class='filters'><input id='librarySearch' placeholder='Search title, project or role'><select id='libraryKind'><option value=''>All media</option><option value='music'>Music</option><option value='audio'>Audio</option><option value='video'>Video</option><option value='image'>Images</option></select><button class='btn' id='libraryClear'>Clear</button></section><section class='grid' id='libraryGrid'>{listing}</section></main><script>
    (()=>{{const q=document.getElementById('librarySearch'),kind=document.getElementById('libraryKind'),cards=[...document.querySelectorAll('#libraryGrid .card')];function filter(){{const text=q.value.trim().toLowerCase(),k=kind.value;for(const card of cards){{card.style.display=(!k||card.dataset.kind===k)&&(!text||(card.dataset.search||'').includes(text))?'block':'none'}}}}q.addEventListener('input',filter);kind.addEventListener('change',filter);document.getElementById('libraryClear').onclick=()=>{{q.value='';kind.value='';filter()}}}})();
    </script></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "private, no-store"})


__all__ = ["router", "scan_creative_library"]
