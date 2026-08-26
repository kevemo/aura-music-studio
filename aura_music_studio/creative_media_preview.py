from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from .creative_project import CreativeProjectStore
from .tenant_storage import project_path

router = APIRouter(prefix="/creative", tags=["creative-media"])

_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
}
_MEDIA_KINDS = {"image", "video", "audio", "music"}


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    return member


def _resolve_source(project: Path, source_ref: str) -> tuple[Path, str]:
    clean = str(source_ref or "").strip()
    if not clean:
        raise FileNotFoundError("Creative element has no local media source")
    relative = Path(clean)
    if relative.is_absolute():
        raise ValueError("Creative media source must be project-relative")
    root = project.resolve()
    target = (root / relative).resolve()
    if root not in target.parents:
        raise ValueError("Creative media source escapes the project")
    media_type = _MEDIA_TYPES.get(target.suffix.lower())
    if not media_type:
        raise ValueError("Creative element is not a supported preview media type")
    if not target.is_file():
        raise FileNotFoundError(clean)
    return target, media_type


def resolve_element_media(project_name: str, element_id: str) -> tuple[Path, str, dict]:
    project = project_path(project_name, must_exist=True)
    manifest = CreativeProjectStore(project).load()
    element = next((item for item in manifest.elements if item.id == element_id), None)
    if element is None:
        raise KeyError(element_id)
    if element.kind not in _MEDIA_KINDS:
        raise ValueError("This Creative Element is not previewable media")
    target, media_type = _resolve_source(project, str(element.source_ref or ""))
    return target, media_type, element.model_dump(mode="json")


@router.get("/projects/{project_name}/elements/{element_id}/media")
def creative_element_media(
    project_name: str,
    element_id: str,
    request: Request,
    download: bool = False,
):
    _member(request)
    try:
        path, media_type, element = resolve_element_media(project_name, element_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Creative media file not found") from exc
    except KeyError as exc:
        raise HTTPException(404, "Creative Element not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    filename = Path(path).name
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        content_disposition_type="attachment" if download else "inline",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Pulsar-Creative-Element": str(element.get("id") or element_id),
        },
    )


@router.post("/projects/{project_name}/elements/{element_id}/activate-version")
def activate_creative_element_version(project_name: str, element_id: str, request: Request):
    _member(request)
    try:
        project = project_path(project_name, must_exist=True)
        store = CreativeProjectStore(project)
        manifest = store.activate_element_version(element_id)
        family = store.version_family(element_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Creative project not found") from exc
    except KeyError as exc:
        raise HTTPException(404, "Creative Element not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    element = next(item for item in manifest.elements if item.id == element_id)
    return {
        "element": element.model_dump(mode="json"),
        "manifest": manifest.model_dump(mode="json"),
        "version_family": family,
        "non_destructive": True,
        "media_files_deleted": False,
    }


MEDIA_PREVIEW_SCRIPT = r"""
(()=>{
  const mediaKinds=new Set(['image','video','audio','music']);
  const $=id=>document.getElementById(id);
  function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
  function projectId(){const box=$('projectName');return box?box.value.trim():''}
  function rows(){try{return (manifest?.elements||[]).filter(e=>mediaKinds.has(e.kind)&&e.source_ref)}catch(_){return []}}
  function allElements(){try{return manifest?.elements||[]}catch(_){return []}}
  function activeIds(){try{return new Set(manifest?.active_element_ids||[])}catch(_){return new Set()}}
  function isCurrent(e){return activeIds().has(e.id)}
  function mediaURL(e,download=false){return `/creative/projects/${encodeURIComponent(projectId())}/elements/${encodeURIComponent(e.id)}/media${download?'?download=true':''}`}
  async function request(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});let body={};try{body=await r.json()}catch(_){}if(!r.ok)throw new Error(body.detail||`Request failed (${r.status})`);return body}
  function ensureDrawer(){
    let d=$('creativeMediaDrawer');if(d)return d;
    d=document.createElement('aside');d.id='creativeMediaDrawer';d.style.cssText='position:fixed;right:0;top:0;bottom:0;width:min(620px,100%);z-index:90;transform:translateX(105%);transition:.2s;background:#080b16fb;border-left:1px solid #ffffff20;padding:18px;overflow:auto;box-shadow:-24px 0 70px #000a';
    d.innerHTML=`<div style="display:flex;align-items:center;gap:8px"><div style="flex:1"><div class="eyebrow">Creative DNA outputs</div><h2 style="margin:4px 0">Media Gallery</h2><div class="muted" style="font-size:.72rem">Preview, play and switch non-destructive Creative DNA versions in this private project.</div></div><button class="btn small" id="creativeMediaClose">✕</button></div><div id="creativeMediaViewer" style="margin:14px 0"></div><div id="creativeMediaRows"></div>`;
    document.body.append(d);$('creativeMediaClose').onclick=()=>closeDrawer();
    d.addEventListener('click',event=>{const b=event.target.closest('[data-creative-preview]');if(b)openElement(b.dataset.creativePreview);const play=event.target.closest('[data-creative-play]');if(play)playInPulsar(play.dataset.creativePlay);const revise=event.target.closest('[data-creative-revise]');if(revise)prepareRevision(revise.dataset.creativeRevise);const activate=event.target.closest('[data-creative-activate]');if(activate)activateVersion(activate.dataset.creativeActivate)});
    return d;
  }
  function closeDrawer(){const d=$('creativeMediaDrawer');if(d)d.style.transform='translateX(105%)'}
  function versionChip(e){return isCurrent(e)?'<span class="chip good">CURRENT</span>':'<span class="chip wait">HISTORY</span>'}
  function playButton(e){return e.kind==='image'?'':`<button class="btn small" data-creative-play="${esc(e.id)}">▶ Pulsar Player</button>`}
  function renderRows(){
    const d=ensureDrawer(),items=rows(),target=$('creativeMediaRows');
    target.innerHTML=items.length?items.map(e=>`<div class="item" style="margin:8px 0"><div class="itemtop"><div><b>${esc(e.label||e.kind)}</b><div><span class="chip">${esc(e.kind)}</span><span class="chip ${e.status==='ready'?'good':'wait'}">${esc(e.status||'draft')}</span>${versionChip(e)}</div><div class="source">${esc(e.source_ref)}</div></div><div style="display:flex;gap:5px;flex-wrap:wrap;justify-content:flex-end"><button class="btn small" data-creative-preview="${esc(e.id)}">Preview</button>${playButton(e)}${!isCurrent(e)&&e.status!=='archived'?`<button class="btn small" data-creative-activate="${esc(e.id)}">Make current</button>`:''}</div></div></div>`).join(''):'<div class="empty">No local image, video or audio outputs are registered in this project yet. Generate/import media first, then it will appear here.</div>';
    d.style.transform='translateX(0)';
  }
  function openElement(id){
    const e=rows().find(row=>row.id===id);if(!e)return;
    const url=mediaURL(e),viewer=$('creativeMediaViewer');let body='';
    if(e.kind==='image')body=`<img src="${url}" alt="${esc(e.label||'Creative image')}" style="display:block;max-width:100%;max-height:65vh;margin:auto;border-radius:14px;border:1px solid #ffffff20">`;
    else if(e.kind==='video')body=`<video src="${url}" controls playsinline preload="metadata" style="display:block;width:100%;max-height:65vh;border-radius:14px;background:#000"></video>`;
    else body=`<audio src="${url}" controls preload="metadata" style="width:100%"></audio>`;
    viewer.innerHTML=`<div class="item"><div class="itemtop"><div><b>${esc(e.label||e.kind)}</b><div class="muted" style="font-size:.7rem">${esc(e.role||'')} · ${esc(e.kind)} · ${isCurrent(e)?'CURRENT VERSION':'HISTORICAL VERSION'}</div></div><div style="display:flex;gap:5px;flex-wrap:wrap">${playButton(e)}<button class="btn small" data-creative-revise="${esc(e.id)}">✦ Revise with Aura</button>${!isCurrent(e)&&e.status!=='archived'?`<button class="btn small" data-creative-activate="${esc(e.id)}">Make current</button>`:''}<a class="btn small" href="${mediaURL(e,true)}">Download</a></div></div><div style="margin-top:10px">${body}</div><div class="muted" style="font-size:.65rem;margin-top:8px">Served by Creative Element ID from this member's project; arbitrary server paths are not accepted. Version switching changes Creative DNA metadata only and does not delete retained media. Download eligibility is enforced server-side by membership tier.</div></div>`;
  }
  function playInPulsar(id){
    const e=rows().find(row=>row.id===id);if(!e||e.kind==='image')return;
    if(!window.PulsarPlayer){if(typeof notice==='function')notice('Pulsar Player is still loading. Try again in a moment.',true);return}
    window.PulsarPlayer.play({id:e.id,src:mediaURL(e),kind:e.kind,title:e.label||e.kind,project:projectId(),elementId:e.id,version:isCurrent(e)?'Current':'History'});
    if(typeof notice==='function')notice(`${e.label||e.kind} loaded into Pulsar Player.`);
  }
  async function activateVersion(id){
    if(!projectId())return;
    try{const data=await request(`/creative/projects/${encodeURIComponent(projectId())}/elements/${encodeURIComponent(id)}/activate-version`,{method:'POST',body:'{}'});manifest=data.manifest||manifest;if(typeof render==='function')render();renderRows();openElement(id);if(typeof notice==='function')notice('Current Creative DNA version updated. Previous media remains available in History.')}catch(error){if(typeof notice==='function')notice(error.message,true)}
  }
  function prepareRevision(id){
    const e=allElements().find(row=>row.id===id);if(!e)return;
    const target=$('targetIds'),preserve=$('preserveIds'),kind=$('targetKind'),operation=$('operation'),instruction=$('instruction');
    if(!target||!preserve||!kind||!operation||!instruction)return;
    target.value=e.id;
    preserve.value=allElements().filter(row=>row.id!==e.id).map(row=>row.id).join(', ');
    if(Array.from(kind.options).some(option=>option.value===e.kind))kind.value=e.kind;
    operation.value='revise';
    if(!instruction.value.trim())instruction.value=`Revise only “${e.label||e.kind}”. Keep every preserved Creative DNA element unchanged. `;
    closeDrawer();instruction.scrollIntoView({behavior:'smooth',block:'center'});instruction.focus();
    if(typeof notice==='function')notice(`Revision prepared for ${e.label||e.kind}. Review the instruction, then add the Aura directive when ready.`);
  }
  const bar=document.querySelector('.rendererbar');if(bar&&!$('creativeMediaButton')){const b=document.createElement('button');b.id='creativeMediaButton';b.className='btn small';b.textContent='▣ Media Gallery';b.onclick=()=>{if(!projectId())return typeof notice==='function'?notice('Load a Creative House project first.',true):null;renderRows()};bar.append(b)}
})();
"""


@router.get("/media-preview-ui.js", include_in_schema=False)
def media_preview_ui():
    return Response(content=MEDIA_PREVIEW_SCRIPT, media_type="application/javascript", headers={"Cache-Control": "no-store"})


class CreativeMediaPreviewMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.method.upper() != "GET" or request.url.path != "/creative-house":
            return response
        content_type = (response.headers.get("content-type") or "").lower()
        if not content_type.startswith("text/html"):
            return response
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return Response(content=body, status_code=response.status_code, headers=dict(response.headers), background=response.background)
        marker = "<script src='/creative/media-preview-ui.js'></script>"
        if marker not in text:
            text = text.replace("</body>", marker + "</body>")
        encoded = text.encode("utf-8")
        migrated = Response(content=encoded, status_code=response.status_code, background=response.background)
        raw_headers = [(key, value) for key, value in response.raw_headers if key.lower() != b"content-length"]
        raw_headers.append((b"content-length", str(len(encoded)).encode("ascii")))
        migrated.raw_headers = raw_headers
        return migrated


__all__ = [
    "router",
    "CreativeMediaPreviewMiddleware",
    "resolve_element_media",
    "MEDIA_PREVIEW_SCRIPT",
]
