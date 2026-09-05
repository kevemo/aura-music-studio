from __future__ import annotations

import hashlib
import json
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Literal
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .brand_ui import COMMAND_CENTER_ART_PATH
from .game_forge_assets import (
    asset_publication_blockers,
    private_runtime_asset_path,
    runtime_asset_manifest,
)
from .game_forge_integrity import game_integrity_hash
from .game_forge_models import ENGINE_REGISTRY, GameDNA
from .game_forge_package_integrity import (
    AURA_WEB_PACKAGE_SCHEMA_VERSION,
    verify_aura_web_export,
)
from .game_forge_state_machine_runtime import private_play_html
from .game_forge_store import game_dir, load_game
from .plans import GAME_CREATE

router = APIRouter(tags=["Aura Game Export"])

AURA_WEB_EXPORT_VERSION = AURA_WEB_PACKAGE_SCHEMA_VERSION
ExportTarget = Literal["aura_web", "phaser4", "playcanvas", "babylon", "godot"]

_EXPORT_CAPABILITIES: dict[str, dict] = {
    "aura_web": {
        "label": "Aura Web PWA Package",
        "production_ready": True,
        "executable_export": True,
        "format": "deterministic_pwa_zip_v3_verified",
        "runtime": "existing reviewed Aura playtest runtime in a sandboxed installable shell",
        "installable_pwa": True,
        "offline_core": True,
        "verified_media_cache": "same_origin_on_demand",
        "package_integrity": "sha256_all_payload_members",
        "download_reverification": True,
        "publisher_authenticity": "external_signing_gate",
    },
    "phaser4": {
        "label": "Phaser 4 adapter",
        "production_ready": False,
        "executable_export": False,
        "format": "planned",
        "reason": "The Phaser adapter is still planned and is not presented as generated executable code.",
    },
    "playcanvas": {
        "label": "PlayCanvas adapter",
        "production_ready": False,
        "executable_export": False,
        "format": "planned",
        "reason": "The PlayCanvas adapter is still planned and is not presented as generated executable code.",
    },
    "babylon": {
        "label": "Babylon.js adapter",
        "production_ready": False,
        "executable_export": False,
        "format": "planned",
        "reason": "The Babylon.js adapter is still planned and is not presented as generated executable code.",
    },
    "godot": {
        "label": "Godot adapter",
        "production_ready": False,
        "executable_export": False,
        "format": "planned",
        "reason": "The Godot adapter is still planned and is not presented as generated executable code.",
    },
}


class CreateGameExportRequest(BaseModel):
    target: ExportTarget = "aura_web"


def _creator(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(GAME_CREATE):
        raise HTTPException(403, "Game export unlocks on the Basic £4.99 tier")
    return member


def _game(game_id: str) -> GameDNA:
    try:
        return load_game(game_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Game not found") from exc


def _exports_root(game_id: str) -> Path:
    parent = game_dir(game_id).resolve()
    target = (parent / "exports").resolve()
    if parent not in target.parents:
        raise ValueError("Game export storage escaped the game directory")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _safe_export_id(export_id: str) -> str:
    value = str(export_id or "").strip()
    if not value.startswith("export_") or not value[7:].isalnum() or len(value) > 80:
        raise ValueError("Invalid game export id")
    return value


def _export_path(game_id: str, export_id: str) -> Path:
    root = _exports_root(game_id).resolve()
    target = (root / f"{_safe_export_id(export_id)}.zip").resolve()
    if root not in target.parents:
        raise ValueError("Game export path escaped storage")
    return target


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip_info(name: str) -> ZipInfo:
    # Fixed archive metadata plus deterministic manifest data makes identical builds reproducible.
    info = ZipInfo(filename=name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def _write_zip_entry(zf: ZipFile, name: str, data: bytes) -> None:
    clean = str(name or "").replace("\\", "/").lstrip("/")
    if not clean or ".." in Path(clean).parts:
        raise ValueError("Unsafe export archive path")
    zf.writestr(_zip_info(clean), data)


def _validate_exportable(game: GameDNA, target: ExportTarget) -> str:
    capability = _EXPORT_CAPABILITIES[target]
    if not capability["production_ready"]:
        raise ValueError(str(capability.get("reason") or "Requested export adapter is not production ready"))
    if target != "aura_web":
        raise ValueError("Only the Aura Web export is production ready")
    if not game.rights_confirmed or not str(game.rights_attestation or "").strip():
        raise ValueError("Confirm the project's creation/usage rights before export")
    current_hash = game_integrity_hash(game)
    if not game.latest_build or game.latest_build.content_hash != current_hash:
        raise ValueError("Game build is missing or stale. Rebuild the current game before export")
    blockers = asset_publication_blockers(game.id)
    if blockers:
        raise ValueError("; ".join(blockers))
    return current_hash


def _brand_art_bytes() -> bytes:
    try:
        data = COMMAND_CENTER_ART_PATH.read_bytes()
    except OSError as exc:
        raise ValueError("Command Center brand artwork is unavailable for the installable export") from exc
    if len(data) < 16 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("Command Center brand artwork failed WebP integrity validation")
    return data


def _webp_size(data: bytes) -> str | None:
    """Return an exact WebP width/height without requiring Pillow at runtime."""
    if len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    offset = 12
    while offset + 8 <= len(data):
        kind = data[offset : offset + 4]
        size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        start = offset + 8
        end = start + size
        if end > len(data):
            return None
        chunk = data[start:end]
        if kind == b"VP8X" and len(chunk) >= 10:
            width = 1 + int.from_bytes(chunk[4:7], "little")
            height = 1 + int.from_bytes(chunk[7:10], "little")
            return f"{width}x{height}"
        if kind == b"VP8L" and len(chunk) >= 5 and chunk[0] == 0x2F:
            bits = int.from_bytes(chunk[1:5], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
            return f"{width}x{height}"
        if kind == b"VP8 " and len(chunk) >= 10 and chunk[3:6] == b"\x9d\x01\x2a":
            width = int.from_bytes(chunk[6:8], "little") & 0x3FFF
            height = int.from_bytes(chunk[8:10], "little") & 0x3FFF
            if width and height:
                return f"{width}x{height}"
        offset = end + (size & 1)
    return None


def _pwa_manifest(game: GameDNA, brand_art: bytes) -> bytes:
    icon: dict[str, str] = {
        "src": "./brand-icon.webp",
        "type": "image/webp",
        "purpose": "any maskable",
    }
    size = _webp_size(brand_art)
    if size:
        icon["sizes"] = size
    payload = {
        "id": "./",
        "name": game.title[:120],
        "short_name": game.title[:30],
        "description": "Aura Game Forge installable export — Powered by Aura AI",
        "start_url": "./index.html",
        "scope": "./",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#030207",
        "theme_color": "#7030b8",
        "icons": [icon],
    }
    return (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _pwa_index(game: GameDNA) -> bytes:
    title = escape(game.title[:160])
    html = f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>
<meta name='theme-color' content='#7030b8'><meta name='color-scheme' content='dark'>
<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self'; frame-src 'self'; worker-src 'self'; manifest-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'\">
<link rel='manifest' href='./manifest.webmanifest'><link rel='icon' href='./brand-icon.webp' type='image/webp'><title>{title}</title><style>
*{{box-sizing:border-box}}html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#030207;color:#fff;font-family:system-ui,sans-serif}}#shell{{height:100%;display:grid;grid-template-rows:auto 1fr}}header{{min-height:56px;display:flex;align-items:center;gap:10px;padding:8px max(10px,env(safe-area-inset-right)) 8px max(10px,env(safe-area-inset-left));background:#08040ff2;border-bottom:1px solid #e9bb5840}}header img{{width:40px;height:40px;border-radius:50%;object-fit:cover}}header div{{min-width:0}}header b{{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}header small{{color:#ffe9a6}}iframe{{width:100%;height:100%;border:0;background:#050611}}#offline{{position:fixed;right:10px;top:10px;z-index:3;border:1px solid #e9bb5848;background:#08040fe8;border-radius:999px;padding:5px 8px;font-size:11px;color:#ffe9a6}}
</style></head><body><div id='shell'><header><img src='./brand-icon.webp' alt='Elevate Souls Productions'><div><b>{title}</b><small>Content Creation Command Center · Powered by Aura AI</small></div></header><iframe title='Aura Game' src='./play.html' sandbox='allow-scripts allow-pointer-lock' referrerpolicy='no-referrer' allow='gamepad'></iframe></div><div id='offline' role='status' aria-live='polite'>Installable Aura Web package</div><script>
'use strict';const badge=document.getElementById('offline');function status(){{badge.textContent=navigator.onLine?'Installable Aura Web package':'Offline · cached package'}}addEventListener('online',status);addEventListener('offline',status);status();if('serviceWorker' in navigator){{addEventListener('load',()=>navigator.serviceWorker.register('./service-worker.js',{{scope:'./'}}).catch(()=>{{badge.textContent='Package ready · offline cache unavailable'}}))}}
</script></body></html>"""
    return html.encode("utf-8")


def _service_worker(content_hash: str, media_paths: list[str]) -> bytes:
    cache_name = f"aura-game-v{AURA_WEB_EXPORT_VERSION}-{content_hash[:20]}"
    core = ["", "index.html", "play.html", "manifest.webmanifest", "brand-icon.webp"]
    allowed = sorted(set(core + [path.lstrip("./") for path in media_paths]))
    script = f"""'use strict';
const CACHE={json.dumps(cache_name)};
const CORE={json.dumps(["./", "./index.html", "./play.html", "./manifest.webmanifest", "./brand-icon.webp"])};
const ALLOWED=new Set({json.dumps(allowed)});
function relativePath(request){{const url=new URL(request.url),scope=new URL(self.registration.scope);if(url.origin!==scope.origin||!url.pathname.startsWith(scope.pathname))return null;return decodeURIComponent(url.pathname.slice(scope.pathname.length));}}
self.addEventListener('install',event=>{{event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(CORE)).then(()=>self.skipWaiting()))}});
self.addEventListener('activate',event=>{{event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key.startsWith('aura-game-v')&&key!==CACHE).map(key=>caches.delete(key)))).then(()=>self.clients.claim()))}});
self.addEventListener('fetch',event=>{{if(event.request.method!=='GET')return;const path=relativePath(event.request);if(path===null||!ALLOWED.has(path))return;event.respondWith(caches.open(CACHE).then(async cache=>{{const hit=await cache.match(event.request,{{ignoreSearch:true}});if(hit)return hit;try{{const response=await fetch(event.request);if(response&&response.ok)await cache.put(event.request,response.clone());return response}}catch(error){{const fallback=await cache.match('./index.html');if(event.request.mode==='navigate'&&fallback)return fallback;throw error}}}}))}});
"""
    return script.encode("utf-8")


def _package_integrity(entries: dict[str, bytes]) -> dict:
    return {
        "algorithm": "sha256",
        "coverage": "all_archive_members_except_manifest.json",
        "files": [
            {
                "path": name,
                "sha256": _sha256_bytes(data),
                "byte_size": len(data),
            }
            for name, data in sorted(entries.items())
        ],
        "publisher_authenticity_verified": False,
        "publisher_authenticity_gate": "independently trusted release signing",
    }


def create_aura_web_export(game: GameDNA) -> dict:
    content_hash = _validate_exportable(game, "aura_web")
    html = private_play_html(game).encode("utf-8")
    assets = runtime_asset_manifest(game.id)
    media_entries: list[dict] = []
    media_bytes: list[tuple[str, bytes]] = []
    for row in assets:
        media_url = str(row.get("media_url") or "")
        if not media_url.startswith("media/"):
            raise ValueError("Runtime media URL escaped the same-origin export contract")
        filename = media_url.split("/", 1)[1]
        path, record = private_runtime_asset_path(game.id, filename)
        data = path.read_bytes()
        if len(data) != int(row.get("byte_size") or -1) or _sha256_bytes(data) != str(row.get("sha256") or ""):
            raise ValueError(f"Game asset '{record.label}' failed export integrity verification")
        media_entries.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "label": row["label"],
                "role": row["role"],
                "media_url": media_url,
                "mime_type": row["mime_type"],
                "sha256": row["sha256"],
                "byte_size": row["byte_size"],
            }
        )
        media_bytes.append((media_url, data))

    brand_art = _brand_art_bytes()
    export_seed = f"aura_web:v{AURA_WEB_EXPORT_VERSION}:{game.id}:{content_hash}".encode("utf-8")
    export_id = f"export_{hashlib.sha256(export_seed).hexdigest()[:32]}"

    package_entries: dict[str, bytes] = {
        "index.html": _pwa_index(game),
        "play.html": html,
        "manifest.webmanifest": _pwa_manifest(game, brand_art),
        "service-worker.js": _service_worker(content_hash, [name for name, _ in media_bytes]),
        "brand-icon.webp": brand_art,
    }
    for name, data in media_bytes:
        if name in package_entries:
            raise ValueError("Game export media path collides with a core package file")
        package_entries[name] = data

    manifest = {
        "schema_version": AURA_WEB_EXPORT_VERSION,
        "export_id": export_id,
        "target": "aura_web",
        "product": "Elevate Souls Productions Content Creation Command Center",
        "powered_by": "Aura AI",
        "game": {
            "id": game.id,
            "title": game.title,
            "genre": game.genre,
            "dimension": game.dimension,
            "requested_engine": game.engine_target,
            "build_id": game.latest_build.build_id,
            "runtime": game.latest_build.runtime,
            "content_hash": content_hash,
        },
        "assets": media_entries,
        "package_integrity": _package_integrity(package_entries),
        "pwa": {
            "installable_shell": True,
            "offline_core_cache": True,
            "verified_media_cache": "same_origin_on_demand",
            "service_worker_external_origins_allowed": False,
            "runtime_frame_sandboxed": True,
            "brand_artwork_included": True,
        },
        "provenance": {
            "build_created_at": game.latest_build.created_at,
            "game_rights_confirmed": True,
            "asset_rights_verified_before_export": True,
            "content_integrity_bound": True,
            "package_payload_integrity_verified": True,
            "publisher_authenticity_verified": False,
            "same_origin_media_only": True,
            "creator_private_paths_included": False,
            "server_secrets_included": False,
            "session_data_included": False,
            "external_network_dependency_added": False,
            "llm_generated_executable_code_included": False,
        },
        "launch": {
            "entrypoint": "index.html",
            "runtime_entrypoint": "play.html",
            "serve_over_http": True,
            "https_required_for_installability_outside_localhost": True,
        },
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED, compresslevel=9) as zf:
        for name in ("index.html", "play.html", "manifest.webmanifest", "service-worker.js", "brand-icon.webp"):
            _write_zip_entry(zf, name, package_entries[name])
        _write_zip_entry(zf, "manifest.json", manifest_bytes)
        for name in sorted(entry for entry in package_entries if entry.startswith("media/")):
            _write_zip_entry(zf, name, package_entries[name])

    payload = buffer.getvalue()
    target = _export_path(game.id, export_id)
    tmp = target.with_suffix(".zip.tmp")
    tmp.write_bytes(payload)
    try:
        verification = verify_aura_web_export(
            tmp,
            expected_export_id=export_id,
            expected_game_id=game.id,
            expected_content_hash=content_hash,
        )
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(target)
    return {
        "export_id": export_id,
        "target": "aura_web",
        "filename": target.name,
        "byte_size": len(payload),
        "sha256": _sha256_bytes(payload),
        "content_hash": content_hash,
        "runtime": game.latest_build.runtime,
        "asset_count": len(media_entries),
        "download_url": f"/api/game-forge/games/{game.id}/exports/{export_id}/download",
        "production_ready": True,
        "pwa_installable": True,
        "offline_core_cache": True,
        "verified_media_cache": "same_origin_on_demand",
        "deterministic_for_current_build": True,
        "package_integrity_verified": bool(verification.get("valid")),
        "package_verified_file_count": int(verification.get("verified_file_count") or 0),
        "download_reverification": True,
        "publisher_authenticity_verified": False,
        "creator_private_paths_included": False,
        "server_secrets_included": False,
        "llm_generated_executable_code_included": False,
    }


def export_capabilities() -> dict:
    engines = {
        key: {
            "label": value.get("label"),
            "adapter_stage": value.get("adapter_stage"),
            "native": bool(value.get("native")),
        }
        for key, value in ENGINE_REGISTRY.items()
    }
    return {
        "targets": _EXPORT_CAPABILITIES,
        "engine_registry": engines,
        "production_ready_targets": ["aura_web"],
        "external_adapters_claimed_ready": False,
    }


@router.get("/api/game-forge/games/{game_id}/exports/capabilities")
def game_export_capabilities(game_id: str, request: Request):
    _creator(request)
    _game(game_id)
    return {"game_id": game_id, **export_capabilities()}


@router.post("/api/game-forge/games/{game_id}/exports")
def create_game_export(game_id: str, body: CreateGameExportRequest, request: Request):
    _creator(request)
    game = _game(game_id)
    try:
        if body.target != "aura_web":
            _validate_exportable(game, body.target)
        return create_aura_web_export(game)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get(
    "/api/game-forge/games/{game_id}/exports/{export_id}/download",
    response_class=FileResponse,
    include_in_schema=False,
)
def download_game_export(game_id: str, export_id: str, request: Request):
    _creator(request)
    _game(game_id)
    try:
        path = _export_path(game_id, export_id)
    except ValueError as exc:
        raise HTTPException(404, "Game export not found") from exc
    if not path.is_file():
        raise HTTPException(404, "Game export not found")
    try:
        verify_aura_web_export(
            path,
            expected_export_id=export_id,
            expected_game_id=game_id,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(409, "Game export failed package integrity verification. Rebuild the export before download.") from exc
    return FileResponse(
        path,
        media_type="application/zip",
        filename=path.name,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


__all__ = [
    "router",
    "CreateGameExportRequest",
    "export_capabilities",
    "create_aura_web_export",
]
