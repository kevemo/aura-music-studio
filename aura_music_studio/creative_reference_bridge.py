from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .assets import AssetLibrary, AssetRecord
from .creative_media_preview import CreativeMediaPreviewMiddleware, router
from .creative_project import CreativeKind, CreativeProjectStore, CreativeReference
from .creative_project_api import _snapshot
from .tenant_storage import project_path

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif"}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}
_TEXT_SUFFIXES = {".txt", ".md", ".lrc", ".srt", ".json", ".yaml", ".yml"}
_TARGET_PAGES = {"/creative-house", "/video-studio", "/image-designer"}


class AttachAssetReferenceRequest(BaseModel):
    kind: CreativeKind | None = None
    label: str = Field(default="", max_length=200)
    usage: str = Field(default="creative reference", max_length=500)
    rights_confirmed: bool = False


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    return member


def _project(project_name: str) -> Path:
    try:
        return project_path(project_name, must_exist=True)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc


def _asset_source(project: Path, asset: AssetRecord) -> tuple[Path, str]:
    relative = Path(str(asset.path or ""))
    if relative.is_absolute():
        raise ValueError("Project asset source must be project-relative")
    root = project.resolve()
    target = (root / relative).resolve()
    if root not in target.parents:
        raise ValueError("Project asset source escapes the project")
    if not target.is_file():
        raise FileNotFoundError(asset.path)
    return target, target.relative_to(root).as_posix()


def _detected_creative_kind(path: Path) -> CreativeKind:
    suffix = path.suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    if suffix in _VIDEO_SUFFIXES:
        return "video"
    if suffix in _AUDIO_SUFFIXES:
        return "audio"
    if suffix in _TEXT_SUFFIXES:
        return "text"
    return "reference"


def _creative_kind_for_asset(path: Path, requested: CreativeKind | None) -> CreativeKind:
    detected = _detected_creative_kind(path)
    if requested is None:
        return detected
    if requested == "reference":
        return requested
    if requested in {"music", "voice"} and detected == "audio":
        return requested
    if requested != detected:
        raise ValueError(f"Uploaded {detected} asset cannot be attached as {requested}")
    return requested


@router.post("/projects/{project_name}/references/from-asset/{asset_id}")
def attach_project_asset_as_creative_reference(
    project_name: str,
    asset_id: str,
    body: AttachAssetReferenceRequest,
    request: Request,
):
    member = _member(request)
    if not body.rights_confirmed:
        raise HTTPException(400, "Confirm that you have the right or authorization to use this reference")

    project = _project(project_name)
    store = CreativeProjectStore(project)
    try:
        manifest = store.load()
    except FileNotFoundError as exc:
        raise HTTPException(404, "Creative manifest not initialized for this project") from exc

    try:
        asset = AssetLibrary(project).get(asset_id)
    except KeyError as exc:
        raise HTTPException(404, "Project asset not found") from exc

    try:
        source, source_ref = _asset_source(project, asset)
        kind = _creative_kind_for_asset(source, body.kind)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project asset file not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    existing = next(
        (
            item
            for item in manifest.references
            if str(item.metadata.get("asset_id") or "") == asset.id
            and item.kind == kind
            and item.source_ref == source_ref
        ),
        None,
    )
    if existing is not None:
        return {
            "reference": existing.model_dump(mode="json"),
            "manifest": manifest.model_dump(mode="json"),
            "asset": {
                "id": asset.id,
                "name": asset.name,
                "kind": asset.kind,
                "source_ref": source_ref,
            },
            "already_attached": True,
            "single_project_source_of_truth": True,
        }

    label = body.label.strip() or asset.name
    usage = body.usage.strip() or "creative reference"
    reference = CreativeReference(
        kind=kind,
        label=label,
        source_ref=source_ref,
        usage=usage,
        rights_confirmed=True,
        metadata={
            "asset_id": asset.id,
            "asset_kind": asset.kind,
            "asset_sha256": asset.sha256,
            "rights_record_id": asset.rights_record_id,
            "source": "project_asset_library",
        },
    )
    revision = _snapshot(
        member,
        store,
        label=f"Before attaching uploaded reference {label}",
        reason="creative_asset_reference_add",
    )
    try:
        manifest = store.add_reference(reference)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    return {
        "reference": reference.model_dump(mode="json"),
        "manifest": manifest.model_dump(mode="json"),
        "asset": {
            "id": asset.id,
            "name": asset.name,
            "kind": asset.kind,
            "source_ref": source_ref,
        },
        "revision_snapshot": revision,
        "already_attached": False,
        "single_project_source_of_truth": True,
    }


REFERENCE_UPLOAD_SCRIPT = r"""
(()=>{
  if(window.__auraUnifiedReferenceUploadInstalled)return;
  window.__auraUnifiedReferenceUploadInstalled=true;
  const byId=id=>document.getElementById(id);
  const projectId=()=>((byId('projectName')||byId('project'))?.value||'').trim();
  const labelField=()=>byId('referenceLabel')||byId('refLabel');
  const usageField=()=>byId('referenceUsage')||byId('refUsage');
  const rightsField=()=>byId('referenceRights')||byId('refRights');
  const sourceField=()=>byId('referenceSource')||byId('refSource');
  const desiredKind=()=>{
    const select=byId('referenceKind');
    if(select)return select.value;
    if(location.pathname==='/video-studio')return 'video';
    if(location.pathname==='/image-designer')return 'image';
    return 'reference';
  };
  const tell=(message,error=false)=>{
    try{if(typeof notice==='function'){notice(message,error);return}}catch(_){}
    const box=byId('creativeReferenceUploadStatus');
    if(box){box.textContent=message;box.style.color=error?'#ffb8c2':'#9ff0bd'}
  };
  const setManifest=value=>{
    try{if(typeof manifest!=='undefined')manifest=value}catch(_){}
    try{if(typeof render==='function')render()}catch(_){}
  };
  const addReferenceId=id=>{
    const field=byId('referenceIds');if(!field||!id)return;
    const values=field.value.split(',').map(v=>v.trim()).filter(Boolean);
    if(!values.includes(id))values.push(id);
    field.value=values.join(', ');
  };
  async function responseJson(response){
    let body={};try{body=await response.json()}catch(_){}
    if(!response.ok)throw new Error(body.detail||`Request failed (${response.status})`);
    return body;
  }
  async function uploadAndAttach(){
    const input=byId('creativeReferenceFile'),button=byId('creativeReferenceUploadButton');
    const file=input?.files?.[0],project=projectId(),rights=rightsField();
    if(!project)return tell('Load or initialize a project before uploading a reference.',true);
    if(!file)return tell('Choose a reference file first.',true);
    if(!rights?.checked)return tell('Confirm you have the right or authorization to use this reference.',true);
    button.disabled=true;button.textContent='Uploading…';
    try{
      const form=new FormData();
      form.append('file',file,file.name);
      form.append('kind','auto');
      form.append('rights_basis','user_owned_or_licensed');
      form.append('attestation','I confirm I have the right to use this material in this project.');
      form.append('tags','creative-reference');
      const asset=await responseJson(await fetch(`/projects/${encodeURIComponent(project)}/assets`,{method:'POST',credentials:'same-origin',body:form}));
      const payload={
        kind:desiredKind(),
        label:(labelField()?.value||'').trim()||file.name,
        usage:(usageField()?.value||'').trim()||'creative reference',
        rights_confirmed:true,
      };
      const attached=await responseJson(await fetch(`/creative/projects/${encodeURIComponent(project)}/references/from-asset/${encodeURIComponent(asset.id)}`,{
        method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),
      }));
      if(sourceField())sourceField().value=attached.asset?.source_ref||'';
      if(labelField()&&!labelField().value.trim())labelField().value=attached.reference?.label||file.name;
      addReferenceId(attached.reference?.id);
      if(attached.manifest)setManifest(attached.manifest);
      input.value='';
      tell(attached.already_attached?'This project asset was already attached. Its existing Creative DNA reference is selected.':'Reference uploaded once, rights-recorded, and attached to this same Creative DNA project.');
    }catch(error){tell(error.message||String(error),true)}finally{button.disabled=false;button.textContent='Upload & attach reference'}
  }
  function install(){
    const source=sourceField();if(!source||byId('creativeReferenceUploadBox'))return;
    const host=source.parentElement;if(!host)return;
    const box=document.createElement('div');box.id='creativeReferenceUploadBox';box.style.cssText='border:1px solid #ffffff24;border-radius:12px;padding:10px;background:#ffffff05;display:grid;gap:7px';
    const inputClass=source.className||'';
    box.innerHTML=`<div style="font-size:.72rem;font-weight:900;color:#f3c76d">UPLOAD PROJECT REFERENCE</div><div style="font-size:.7rem;line-height:1.4;color:#bfc3d3">Upload the real file once. It stays in this project’s existing protected asset library and is attached to the same Creative DNA used by Creative House, Image Designer and Video Studio.</div><input id="creativeReferenceFile" class="${inputClass}" type="file" accept="image/*,video/*,audio/*,.txt,.md,.lrc,.srt,.json,.yaml,.yml,.pdf"><button id="creativeReferenceUploadButton" type="button">Upload & attach reference</button><div id="creativeReferenceUploadStatus" role="status" aria-live="polite" style="font-size:.7rem;color:#bfc3d3"></div>`;
    host.insertBefore(box,source);
    byId('creativeReferenceUploadButton').addEventListener('click',uploadAndAttach);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
"""


@router.get("/reference-upload-ui.js", include_in_schema=False)
def reference_upload_ui():
    return Response(
        REFERENCE_UPLOAD_SCRIPT,
        media_type="application/javascript",
        headers={"Cache-Control": "private, no-store"},
    )


def _install_reference_upload_middleware_patch() -> None:
    if getattr(CreativeMediaPreviewMiddleware, "_unified_reference_upload_installed", False):
        return
    original_dispatch = CreativeMediaPreviewMiddleware.dispatch

    async def dispatch(self, request: Request, call_next):
        response = await original_dispatch(self, request, call_next)
        if request.method.upper() != "GET" or request.url.path not in _TARGET_PAGES:
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
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                background=response.background,
            )
        marker = "<script src='/creative/reference-upload-ui.js'></script>"
        if marker not in text:
            text = text.replace("</body>", marker + "</body>")
        encoded = text.encode("utf-8")
        migrated = Response(content=encoded, status_code=response.status_code, background=response.background)
        raw_headers = [
            (key, value)
            for key, value in response.raw_headers
            if key.lower() != b"content-length"
        ]
        raw_headers.append((b"content-length", str(len(encoded)).encode("ascii")))
        migrated.raw_headers = raw_headers
        return migrated

    CreativeMediaPreviewMiddleware.dispatch = dispatch
    CreativeMediaPreviewMiddleware._unified_reference_upload_installed = True


_install_reference_upload_middleware_patch()


__all__ = [
    "AttachAssetReferenceRequest",
    "REFERENCE_UPLOAD_SCRIPT",
    "attach_project_asset_as_creative_reference",
]
