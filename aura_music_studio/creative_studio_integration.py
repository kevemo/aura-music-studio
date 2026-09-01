from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

from .assets import AssetLibrary
from .commercial_entitlement_routes import render_with_commercial_entitlements
from .creative_project import CreativeProjectStore, CreativeReference
from .creative_project_api import QueueRendererRequest, cancel_creative_render
from .creative_renderers import ComfyUIRenderer
from .plans import DEEP_REVISION_HISTORY, REVISION_HISTORY
from .render_attempts import RenderAttemptStore
from .revisions import create_revision
from .tenant_storage import project_path
from .upload_security import UploadTooLargeError, asset_upload_limit, safe_upload_filename, save_bounded_upload

router = APIRouter(tags=["creative-studio-integration"])

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
_AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}
_TEXT_EXTENSIONS = {".txt", ".md", ".json", ".yaml", ".yml", ".srt", ".lrc"}
_REFERENCE_KINDS = {"music", "audio", "video", "image", "voice", "text", "reference"}
_STUDIO_PATHS = {"/creative-house", "/image-designer", "/video-studio"}
_MAX_RENDERER_IMAGE_REFERENCES = 8


@dataclass(frozen=True)
class LocalRendererImageInput:
    """Server-only renderer input marker that cannot originate from member JSON."""

    source: Path
    reference_id: str
    asset_id: str


def _resolve_local_renderer_inputs(renderer, variables: dict) -> dict:
    """Stage validated local image markers immediately before provider submission.

    The integrated endpoint creates these marker objects only after resolving a reference against
    the current tenant project. Member JSON can contain only ordinary JSON values, not this Python
    type, so a caller cannot use the marker to request an arbitrary server path.
    """

    resolved = dict(variables or {})
    for key, value in list(resolved.items()):
        if not isinstance(value, LocalRendererImageInput):
            continue
        renderer_input = renderer.upload_image_input(value.source)
        resolved[key] = renderer_input.workflow_value
    return resolved


def install_creative_reference_staging() -> None:
    """Compose project reference staging into ComfyUI submit without bypassing admission.

    Commercial entitlement logic calls ``renderer.submit`` only after the durable render attempt
    and any required Creation Coin debit have been admitted. Keeping staging inside submit means a
    denied render never uploads a member reference to the renderer. A staging failure propagates
    through the existing commercial refund/fail-closed path before any prompt is submitted.
    """

    original = ComfyUIRenderer.submit
    if getattr(original, "__creative_reference_staging__", False):
        return

    def staged_submit(self, variables):
        resolved = _resolve_local_renderer_inputs(self, variables if isinstance(variables, dict) else {})
        return original(self, resolved)

    staged_submit.__creative_reference_staging__ = True  # type: ignore[attr-defined]
    staged_submit.__wrapped__ = original  # type: ignore[attr-defined]
    ComfyUIRenderer.submit = staged_submit  # type: ignore[assignment]


# Install idempotently when this production integration module is imported. In the main app this
# wrapper composes with provider-cost governance: commercial admission happens first, reference
# staging occurs inside submit, and operational metering records only an accepted provider prompt.
install_creative_reference_staging()


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


def _store(project_name: str) -> CreativeProjectStore:
    store = CreativeProjectStore(_project(project_name))
    if not store.exists():
        raise HTTPException(404, "Creative manifest not initialized for this project")
    return store


def _snapshot(member, store: CreativeProjectStore, *, label: str, reason: str) -> dict | None:
    if not member.plan.has(REVISION_HISTORY):
        return None
    keep = 200 if member.plan.has(DEEP_REVISION_HISTORY) else 20
    try:
        return create_revision(
            store.project_dir,
            label=label,
            reason=reason,
            actor="Aura Creative House",
            keep=keep,
        )
    except Exception:
        return None


def _allowed_extensions(kind: str) -> set[str]:
    if kind == "image":
        return _IMAGE_EXTENSIONS
    if kind == "video":
        return _VIDEO_EXTENSIONS
    if kind in {"audio", "music", "voice"}:
        return _AUDIO_EXTENSIONS
    if kind == "text":
        return _TEXT_EXTENSIONS
    return _IMAGE_EXTENSIONS | _VIDEO_EXTENSIONS | _AUDIO_EXTENSIONS | _TEXT_EXTENSIONS


def _asset_kind(kind: str, suffix: str) -> str:
    if suffix in _IMAGE_EXTENSIONS:
        return "image"
    if suffix in _VIDEO_EXTENSIONS:
        return "video"
    if suffix in _AUDIO_EXTENSIONS:
        return "audio"
    if suffix in _TEXT_EXTENSIONS:
        return "text"
    return kind


def _public_asset(record) -> dict:
    return {
        "id": record.id,
        "name": record.name,
        "kind": record.kind,
        "sha256": record.sha256,
        "mime_type": record.mime_type,
        "created_at": record.created_at,
        "rights_record_id": record.rights_record_id,
        "analysis": record.analysis,
        "tags": record.tags,
    }


@router.post("/creative/projects/{project_name}/references/upload")
async def upload_creative_reference(
    project_name: str,
    request: Request,
    file: UploadFile = File(...),
    kind: str = Form("reference"),
    label: str = Form(""),
    usage: str = Form("creative reference"),
    rights_confirmed: bool = Form(False),
):
    member = _member(request)
    if not rights_confirmed:
        raise HTTPException(400, "Confirm that you have the right or authorization to use this reference")
    kind = str(kind or "reference").strip().lower()
    if kind not in _REFERENCE_KINDS:
        raise HTTPException(400, "Unsupported creative reference kind")
    try:
        safe_name = safe_upload_filename(file.filename, default="reference.bin")
    except ValueError as exc:
        raise HTTPException(400, "Upload filename is invalid") from exc
    suffix = Path(safe_name).suffix.lower()
    if suffix not in _allowed_extensions(kind):
        raise HTTPException(400, f"Unsupported {kind} reference file type")

    store = _store(project_name)
    project = store.project_dir.resolve()
    incoming = project / "input" / "uploads"
    temporary = incoming / f"{uuid4().hex}_{safe_name}"
    reused_asset = False
    try:
        try:
            limit = int(asset_upload_limit())
        except ValueError as exc:
            raise HTTPException(503, "Upload security policy is unavailable") from exc
        try:
            await save_bounded_upload(file, temporary, max_bytes=limit)
        except UploadTooLargeError as exc:
            raise HTTPException(413, "Creative reference exceeds the configured size limit") from exc
        except ValueError as exc:
            if str(exc) == "Upload is empty":
                raise HTTPException(400, "Creative reference upload is empty") from exc
            raise

        library = AssetLibrary(project)
        digest = library.ledger.sha256(temporary)
        record = next((item for item in library.list() if item.sha256 == digest), None)
        if record is not None:
            reused_asset = True
        else:
            attestation = "I confirm I have the right or authorization to use this reference in this creative project."
            record = library.ingest(
                temporary,
                kind=_asset_kind(kind, suffix),
                rights_basis="user_owned_or_licensed",
                attestation=attestation,
                tags=["creative-reference", kind],
                notes=str(usage or "creative reference")[:500],
            )
    finally:
        temporary.unlink(missing_ok=True)

    clean_label = str(label or "").strip() or Path(safe_name).stem or "Creative reference"
    clean_usage = str(usage or "creative reference").strip() or "creative reference"
    reference = CreativeReference(
        kind=kind,
        label=clean_label[:200],
        source_ref=f"asset:{record.id}",
        usage=clean_usage[:500],
        rights_confirmed=True,
        metadata={
            "asset_id": record.id,
            "asset_name": record.name,
            "asset_kind": record.kind,
            "asset_sha256": record.sha256,
            "asset_mime_type": record.mime_type,
            "rights_record_id": record.rights_record_id,
            "uploaded_via": "unified_creative_reference_bridge",
            "reused_project_asset": reused_asset,
        },
    )
    revision = _snapshot(member, store, label=f"Before attaching {reference.label}", reason="creative_reference_upload")
    try:
        manifest = store.add_reference(reference)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "reference": reference.model_dump(mode="json"),
        "asset": _public_asset(record),
        "manifest": manifest.model_dump(mode="json"),
        "revision_snapshot": revision,
        "shared_project_asset": True,
        "reused_project_asset": reused_asset,
        "note": (
            "Existing project asset reused and attached as another Creative DNA reference."
            if reused_asset
            else "Reference uploaded once into this project and attached to Creative DNA."
        ),
    }


def _asset_path(project: Path, asset_id: str) -> tuple[Path, object]:
    try:
        record = AssetLibrary(project).get(asset_id)
    except KeyError as exc:
        raise HTTPException(409, "A Creative DNA reference points to an unavailable project asset") from exc
    root = project.resolve()
    target = (root / record.path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(409, "A Creative DNA reference asset is unavailable")
    return target, record


def _reference_asset_id(reference) -> str | None:
    metadata_id = str(reference.metadata.get("asset_id") or "").strip()
    source = str(reference.source_ref or "").strip()
    source_id = source[6:].strip() if source.startswith("asset:") else ""
    if metadata_id and source_id and metadata_id != source_id:
        raise HTTPException(409, "Creative reference asset identity is inconsistent")
    return metadata_id or source_id or None


@router.post("/creative/projects/{project_name}/directives/{directive_id}/render-integrated")
def queue_integrated_creative_render(
    project_name: str,
    directive_id: str,
    body: QueueRendererRequest,
    request: Request,
):
    _member(request)
    store = _store(project_name)
    project = store.project_dir.resolve()
    manifest = store.load()
    directive = next((item for item in manifest.directives if item.id == directive_id), None)
    if directive is None:
        raise HTTPException(404, "Aura directive not found")
    if directive.target_kind not in {"image", "video"}:
        raise HTTPException(400, "Integrated renderer bridge accepts image or video Aura directives")

    by_reference = {item.id: item for item in manifest.references}
    staged: list[dict] = []
    variables = dict(body.variables)

    candidate_images: list[tuple[object, str, Path]] = []
    for reference_id in directive.reference_ids:
        reference = by_reference.get(reference_id)
        if reference is None:
            raise HTTPException(409, "Aura directive references missing Creative DNA context")
        asset_id = _reference_asset_id(reference)
        if not asset_id:
            continue
        target, _record = _asset_path(project, asset_id)
        if target.suffix.lower() in _IMAGE_EXTENSIONS:
            candidate_images.append((reference, asset_id, target))

    if len(candidate_images) > _MAX_RENDERER_IMAGE_REFERENCES:
        raise HTTPException(400, f"At most {_MAX_RENDERER_IMAGE_REFERENCES} image references can be staged per render")

    for index, (reference, asset_id, target) in enumerate(candidate_images, start=1):
        variable = "reference_image" if index == 1 else f"reference_image_{index}"
        variables[variable] = LocalRendererImageInput(
            source=target,
            reference_id=reference.id,
            asset_id=asset_id,
        )
        staged.append({
            "reference_id": reference.id,
            "asset_id": asset_id,
            "workflow_variable": variable,
        })

    variables["reference_image_count"] = len(staged)
    integrated_body = body.model_copy(update={"variables": variables})
    # Commercial admission happens before renderer.submit. LocalRendererImageInput objects remain
    # server-only until submit is invoked, where the installed staging wrapper converts them into
    # opaque ComfyUI input tokens. A denied render therefore creates no renderer-side input upload.
    result = render_with_commercial_entitlements(project_name, directive_id, integrated_body, request)

    latest = store.load()
    latest_directive = next(item for item in latest.directives if item.id == directive_id)
    render_meta = dict(latest_directive.metadata.get("creative_renderer") or {})
    render_meta.update({
        "creative_reference_ids": list(directive.reference_ids),
        "staged_image_reference_count": len(staged),
        "staged_image_reference_ids": [item["reference_id"] for item in staged],
    })
    latest = store.update_directive(directive_id, metadata={"creative_renderer": render_meta})
    latest_directive = next(item for item in latest.directives if item.id == directive_id)
    result["directive"] = latest_directive.model_dump(mode="json")
    result["reference_inputs"] = staged
    result["reference_input_count"] = len(staged)
    result["note"] = (
        f"{result.get('note', 'Renderer accepted the Aura directive.')} {len(staged)} image reference(s) were staged from this same project."
        if staged
        else result.get("note", "Renderer accepted the Aura directive.")
    )
    return result


def _reconcile_cancelled_attempt(member, project_name: str, directive_id: str, result: dict) -> dict | None:
    """Release only the durable admission proven to belong to the cancelled provider prompt."""

    user_id = str(getattr(member, "user_id", "") or "").strip()
    if not user_id:
        return None
    attempts = RenderAttemptStore()
    active = attempts.active(user_id, project_name, directive_id)
    if active is None:
        return None

    directive = result.get("directive") if isinstance(result, dict) else None
    if not isinstance(directive, dict):
        return None
    metadata = directive.get("metadata")
    render_meta = metadata.get("creative_renderer") if isinstance(metadata, dict) else None
    prompt_id = str(render_meta.get("prompt_id") or "").strip() if isinstance(render_meta, dict) else ""
    if not prompt_id or not active.provider_prompt_id or prompt_id != active.provider_prompt_id:
        # Fail closed for ambiguous/legacy admissions. Never release an attempt merely because
        # the same member/project/directive names match when provider identity does not.
        return None

    status = str(directive.get("status") or "")
    cancellation = result.get("cancellation") if isinstance(result, dict) else None
    cancellation_state = str(cancellation.get("state") or "") if isinstance(cancellation, dict) else ""
    try:
        if status == "completed":
            updated = attempts.mark_completed(active.attempt_id)
        elif status == "failed":
            updated = attempts.mark_failed(active.attempt_id)
        elif status == "ready_for_renderer" and cancellation_state in {"cancelled_running", "cancelled_pending"}:
            # The attempt ledger predates creator-facing cancellation and has no cancelled state.
            # Marking this provider attempt failed releases replay admission without fabricating a
            # refund. Any already-consumed allowance/Creation Coin remains governed by its policy.
            updated = attempts.mark_failed(active.attempt_id)
        else:
            return None
    except (KeyError, ValueError):
        return None
    return {
        "attempt_id": updated.attempt_id,
        "state": updated.state,
        "provider_status": updated.provider_status,
        "provider_prompt_matched": True,
        "refund_issued": False,
    }


@router.post("/creative/projects/{project_name}/directives/{directive_id}/cancel-integrated")
def cancel_integrated_creative_render(project_name: str, directive_id: str, request: Request):
    member = _member(request)
    result = cancel_creative_render(project_name, directive_id, request)
    reconciliation = _reconcile_cancelled_attempt(member, project_name, directive_id, result)
    result["render_attempt_reconciliation"] = reconciliation
    return result


STUDIO_INTEGRATION_SCRIPT = r"""
(()=>{
  const path=location.pathname;
  const isHouse=path==='/creative-house';
  const isMedia=path==='/image-designer'||path==='/video-studio';
  if(!isHouse&&!isMedia)return;
  const $=id=>document.getElementById(id);
  const busy=new Set(),terminal=new Set();
  let timer=null,polling=false;
  const projectId=()=>{try{return typeof project==='function'?project():($('projectName')?.value||$('project')?.value||'').trim()}catch(_){return ($('projectName')?.value||$('project')?.value||'').trim()}};
  const show=(message,error=false)=>{try{if(typeof notice==='function')notice(message,error)}catch(_){}};
  const json=async(url,opt={})=>{const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});let b={};try{b=await r.json()}catch(_){}if(!r.ok)throw new Error(b.detail||`Request failed (${r.status})`);return b};
  const currentManifest=()=>{try{return manifest}catch(_){return null}};
  const setManifest=value=>{try{manifest=value}catch(_){}};
  const redraw=()=>{try{if(typeof render==='function')render()}catch(_){}};
  function idsForReference(){return isHouse?{label:'referenceLabel',source:'referenceSource',usage:'referenceUsage',rights:'referenceRights',kind:'referenceKind'}:{label:'refLabel',source:'refSource',usage:'refUsage',rights:'refRights',kind:null}}
  function selectedReferenceKind(){const ids=idsForReference();if(ids.kind&&$(ids.kind))return $(ids.kind).value;try{return typeof KIND!=='undefined'?KIND:'reference'}catch(_){return 'reference'}}
  function inferredUploadKind(file){if(isHouse)return selectedReferenceKind();const type=String(file?.type||'').toLowerCase(),name=String(file?.name||'').toLowerCase();if(type.startsWith('image/')||/\.(png|jpe?g|webp|avif|gif)$/.test(name))return 'image';if(type.startsWith('video/')||/\.(mp4|mov|webm|mkv|avi|m4v)$/.test(name))return 'video';if(type.startsWith('audio/')||/\.(wav|mp3|flac|m4a|aac|ogg)$/.test(name))return 'audio';return selectedReferenceKind()}
  function ensureUploadUI(){const ids=idsForReference(),source=$(ids.source);if(!source||$('creativeReferenceFile'))return;const file=document.createElement('input');file.id='creativeReferenceFile';file.type='file';file.className=source.className||'field';file.accept=isHouse?'image/*,video/*,audio/*,.txt,.md,.json,.srt,.lrc':path==='/video-studio'?'image/*,video/*':'image/*';const button=document.createElement('button');button.id='creativeReferenceUpload';button.type='button';button.className=isHouse?'btn small':'btn';button.textContent='Upload & attach reference';button.onclick=uploadReference;const hint=document.createElement('div');hint.className='muted';hint.style.fontSize='.7rem';hint.textContent='Uploads are stored once in this same private project, rights-attested, then attached to Creative DNA. Manual asset/URI references remain available below.';source.parentNode.insertBefore(file,source);source.parentNode.insertBefore(button,source);source.parentNode.insertBefore(hint,source)}
  async function uploadReference(){const ids=idsForReference(),file=$('creativeReferenceFile')?.files?.[0],rights=$(ids.rights),pid=projectId();if(!pid)return show('Load or initialize this project first.',true);if(!file)return show('Choose a reference file to upload.',true);if(!rights?.checked)return show('Rights/authorization confirmation is required.',true);const form=new FormData();form.append('file',file);form.append('kind',inferredUploadKind(file));form.append('label',($(ids.label)?.value||file.name).trim());form.append('usage',($(ids.usage)?.value||'creative reference').trim());form.append('rights_confirmed','true');try{const r=await fetch(`/creative/projects/${encodeURIComponent(pid)}/references/upload`,{method:'POST',credentials:'same-origin',body:form});let data={};try{data=await r.json()}catch(_){}if(!r.ok)throw new Error(data.detail||`Upload failed (${r.status})`);setManifest(data.manifest);if($(ids.source))$(ids.source).value=data.reference?.source_ref||'';if($(ids.label))$(ids.label).value='';if($(ids.usage))$(ids.usage).value='';rights.checked=false;$('creativeReferenceFile').value='';redraw();show(data.note||'Reference uploaded and attached to Creative DNA.')}catch(error){show(error.message,true)}}
  window.uploadCreativeReference=uploadReference;
  function activeHouse(){if(!isHouse)return[];const m=currentManifest();return (m?.directives||[]).filter(d=>['image','video'].includes(d.target_kind)&&['queued','running'].includes(d.status))}
  function stop(){if(timer){clearTimeout(timer);timer=null}}
  function schedule(){if(!isHouse)return;stop();if(document.hidden||!activeHouse().length)return;timer=setTimeout(()=>void pollHouse(),3000)}
  function upsert(d){const m=currentManifest();if(!m||!d)return;const rows=m.directives||[];const i=rows.findIndex(x=>x.id===d.id);if(i>=0)rows[i]=d;else rows.push(d);m.directives=rows}
  async function pollHouse(force=false){if(!isHouse||polling||(!force&&document.hidden))return;const active=activeHouse();if(!active.length){stop();return}polling=true;try{for(const d of active){if(busy.has(d.id))continue;try{const s=await json(`/creative/projects/${encodeURIComponent(projectId())}/directives/${encodeURIComponent(d.id)}/render-status`);upsert(s.directive);if(['completed','failed'].includes(s.renderer_status)&&!terminal.has(d.id)){terminal.add(d.id);show(s.renderer_status==='completed'?`${d.target_kind==='video'?'Video':'Image'} render complete — ${s.outputs.length} output(s) ready to import.`:'Creative render failed.',s.renderer_status==='failed')}}catch(error){show(`Live render status: ${error.message}`,true)}}redraw()}finally{polling=false;schedule()}}
  async function cancelIntegrated(id){if(busy.has(id))return;busy.add(id);redraw();try{const data=await json(`/creative/projects/${encodeURIComponent(projectId())}/directives/${encodeURIComponent(id)}/cancel-integrated`,{method:'POST',body:'{}'});upsert(data.directive);terminal.delete(id);redraw();show(data.note||'Render cancelled safely.')}catch(error){show(error.message,true)}finally{busy.delete(id);redraw();if(isHouse)schedule()}}
  async function refreshHouse(id){if(!isHouse||busy.has(id))return;busy.add(id);redraw();try{const data=await json(`/creative/projects/${encodeURIComponent(projectId())}/directives/${encodeURIComponent(id)}/render-status`);upsert(data.directive);redraw();show(`Renderer status: ${data.renderer_status}. ${data.outputs.length} output(s).`)}catch(error){show(error.message,true)}finally{busy.delete(id);redraw();schedule()}}
  window.cancelIntegratedCreativeRender=cancelIntegrated;window.refreshIntegratedCreativeRender=refreshHouse;
  if(isHouse&&typeof rendererActions==='function'){
    rendererActions=function(d){if(!['image','video'].includes(d.target_kind))return '';const r=renderers[d.target_kind]||{},meta=d.metadata?.creative_renderer||{},disabled=busy.has(d.id)?' disabled':'';if(d.status==='completed'&&meta.synced)return `<span class="chip good">${meta.output_count||0} output(s) imported</span>`;if(d.status==='completed')return `<button class="btn small primary" onclick="syncOutputs('${d.id}')"${disabled}>Import outputs</button>`;if(['queued','running'].includes(d.status))return `<button class="btn small" onclick="refreshIntegratedCreativeRender('${d.id}')"${disabled}>Refresh now</button><button class="btn small" onclick="cancelIntegratedCreativeRender('${d.id}')"${disabled}>${busy.has(d.id)?'Working…':'Cancel render'}</button><span class="chip wait">Live monitor active</span>`;if(r.configured)return `<button class="btn small primary" onclick="queueRender('${d.id}')"${disabled}>${busy.has(d.id)?'Starting…':`Render ${d.target_kind}`}</button>`;return `<button class="btn small" disabled>${d.target_kind} renderer not configured</button>`};
  }
  async function integratedQueue(id,kind,settings){if(busy.has(id))return;busy.add(id);redraw();try{const data=await json(`/creative/projects/${encodeURIComponent(projectId())}/directives/${encodeURIComponent(id)}/render-integrated`,{method:'POST',body:JSON.stringify(settings)});upsert(data.directive);terminal.delete(id);redraw();show(data.note||'Render queued.');if(isHouse)void pollHouse(true);else{try{if(typeof pollActiveRenders==='function')void pollActiveRenders(true)}catch(_){}}}catch(error){show(error.message,true)}finally{busy.delete(id);redraw();if(isHouse)schedule()}}
  if(isHouse&&typeof queueRender==='function')queueRender=async function(id){const d=currentManifest()?.directives?.find(x=>x.id===id);if(!d)return;const settings={negative_prompt:$('negativePrompt')?.value.trim()||'',width:Number($('renderWidth')?.value||1024),height:Number($('renderHeight')?.value||1024),frames:Number($('renderFrames')?.value||121),fps:Number($('renderFps')?.value||24)};return integratedQueue(id,d.target_kind,settings)};
  if(isMedia&&typeof queue==='function')queue=async function(id){const settings={negative_prompt:$('negative')?.value.trim()||'',width:Number($('width')?.value||1024),height:Number($('height')?.value||1024),frames:Number($('frames')?.value||1),fps:Number($('fps')?.value||1)};return integratedQueue(id,typeof KIND!=='undefined'?KIND:'image',settings)};
  if(isMedia&&typeof cancelRender==='function')cancelRender=cancelIntegrated;
  if(isHouse&&typeof loadProject==='function'){const baseLoad=loadProject;loadProject=async function(...args){const out=await baseLoad(...args);schedule();return out}}
  if(isHouse&&typeof initializeProject==='function'){const baseInit=initializeProject;initializeProject=async function(...args){const out=await baseInit(...args);schedule();return out}}
  document.addEventListener('visibilitychange',()=>{if(isHouse&&!document.hidden)void pollHouse(true)});window.addEventListener('beforeunload',stop);
  ensureUploadUI();if(isHouse){redraw();schedule()}
})();
"""


@router.get("/creative/studio-integration-ui.js", include_in_schema=False)
def creative_studio_integration_ui():
    return Response(
        content=STUDIO_INTEGRATION_SCRIPT,
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


class CreativeStudioIntegrationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.method.upper() != "GET" or request.url.path not in _STUDIO_PATHS:
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
        marker = "<script src='/creative/studio-integration-ui.js'></script>"
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
    "CreativeStudioIntegrationMiddleware",
    "LocalRendererImageInput",
    "STUDIO_INTEGRATION_SCRIPT",
    "install_creative_reference_staging",
    "upload_creative_reference",
    "queue_integrated_creative_render",
    "cancel_integrated_creative_render",
]
