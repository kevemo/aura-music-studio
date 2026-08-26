from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .creative_project import CreativeProjectStore
from .esp_niche import require_esp_social_member
from .social_management import ActivityEvent, SocialHouseStore, utc_now
from .tenant_storage import project_path

router = APIRouter(tags=["ESP Social Media Library"])

MediaKind = Literal["image", "video", "audio", "document", "other"]
MediaSource = Literal["creative_element", "external_url", "artifact_ref", "client_submission"]
ApprovalState = Literal["quarantined", "approved", "rejected"]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _safe_external_ref(value: str) -> str:
    clean = (value or "").strip()
    if len(clean) > 2000:
        raise ValueError("Media source reference is too long")
    parsed = urlparse(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("External media source must use http or https")
    return clean


def _safe_artifact_ref(value: str) -> str:
    clean = (value or "").strip()[:240]
    if not clean:
        raise ValueError("Application artifact reference is required")
    if ".." in clean or "/" in clean or "\\" in clean:
        raise ValueError("Application artifact reference cannot be a filesystem path")
    return clean


def _library(house) -> dict:
    library = house.metadata.get("media_library")
    if not isinstance(library, dict):
        library = {"schema_version": 1, "folders": [], "assets": []}
        house.metadata["media_library"] = library
    library.setdefault("schema_version", 1)
    library.setdefault("folders", [])
    library.setdefault("assets", [])
    return library


def _usage_count(house, asset_id: str) -> int:
    ref = f"library:{asset_id}"
    count = 0
    for content in house.content:
        for variant in content.variants:
            count += sum(1 for value in variant.media_refs if value == ref)
            if variant.cover_ref == ref:
                count += 1
    return count


class CreateFolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class AddMediaRequest(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    kind: MediaKind
    source_type: MediaSource
    source_ref: str = Field(default="", max_length=2000)
    source_project: str = Field(default="", max_length=160)
    source_element_id: str = Field(default="", max_length=160)
    folder_id: str | None = Field(default=None, max_length=160)
    tags: list[str] = Field(default_factory=list, max_length=60)
    rights_confirmed: bool = False
    rights_note: str = Field(default="", max_length=2000)


class ApproveMediaRequest(BaseModel):
    rights_confirmed: bool
    rights_note: str = Field(default="", max_length=2000)


class AttachMediaRequest(BaseModel):
    content_id: str = Field(min_length=1, max_length=160)
    platform: str = Field(min_length=1, max_length=80)


class SocialMediaLibraryStore:
    """Rights/provenance-aware media metadata stored inside each tenant Social House.

    External/client references are quarantined until a human explicitly approves them.
    Attaching media never publishes anything and invalidates prior content approval when the
    approved content body changes.
    """

    def __init__(self, social_store: SocialHouseStore | None = None):
        self.social = social_store or SocialHouseStore()

    def state(self, space_id: str) -> dict:
        house = self.social.load(space_id)
        library = _library(house)
        assets = []
        for raw in library["assets"]:
            item = dict(raw)
            item["usage_count"] = _usage_count(house, item["id"])
            assets.append(item)
        return {
            "space_id": house.id,
            "space_name": house.name,
            "folders": list(library["folders"]),
            "assets": assets,
            "counts": {
                "assets": len(assets),
                "approved": sum(1 for item in assets if item.get("approval_state") == "approved"),
                "quarantined": sum(1 for item in assets if item.get("approval_state") == "quarantined"),
            },
            "external_publish_triggered": False,
        }

    def create_folder(self, space_id: str, name: str) -> dict:
        house = self.social.load(space_id)
        library = _library(house)
        clean = " ".join((name or "").split())[:160]
        if not clean:
            raise ValueError("Folder name is required")
        folder = {"id": _new_id("folder"), "name": clean, "created_at": utc_now()}
        library["folders"].append(folder)
        house.activity.append(ActivityEvent(action="media_folder_created", entity_type="media_folder", entity_id=folder["id"], detail=clean))
        self.social.save(house)
        return folder

    def _creative_provenance(self, project_name: str, element_id: str) -> dict:
        if not project_name or not element_id:
            raise ValueError("Creative Project and element id are required")
        try:
            root = project_path(project_name, must_exist=True)
        except (ValueError, FileNotFoundError) as exc:
            raise FileNotFoundError(project_name) from exc
        manifest = CreativeProjectStore(root).load()
        element = next((item for item in manifest.elements if item.id == element_id), None)
        if element is None:
            raise KeyError(element_id)
        if element.status == "archived":
            raise ValueError("Archived Creative Elements cannot be added to the active Social Media Library")
        return {
            "creative_project": project_name,
            "creative_element_id": element.id,
            "creative_kind": element.kind,
            "creative_label": element.label,
            "creative_status": element.status,
            "verified_in_creative_dna": True,
        }

    def add_asset(self, space_id: str, body: AddMediaRequest, *, actor: str) -> dict:
        house = self.social.load(space_id)
        library = _library(house)
        folders = {item["id"] for item in library["folders"]}
        if body.folder_id and body.folder_id not in folders:
            raise KeyError(body.folder_id)
        provenance: dict = {"source_type": body.source_type, "imported_at": utc_now(), "imported_by": actor}
        source_ref = ""
        approval_state: ApprovalState = "quarantined"
        if body.source_type == "creative_element":
            provenance.update(self._creative_provenance(body.source_project.strip(), body.source_element_id.strip()))
            source_ref = f"creative_element:{body.source_project.strip()}:{body.source_element_id.strip()}"
            if body.rights_confirmed:
                approval_state = "approved"
        elif body.source_type == "external_url":
            source_ref = _safe_external_ref(body.source_ref)
        elif body.source_type == "artifact_ref":
            source_ref = _safe_artifact_ref(body.source_ref)
        else:
            raw = (body.source_ref or "").strip()
            source_ref = _safe_external_ref(raw) if raw.startswith(("http://", "https://")) else _safe_artifact_ref(raw)
            provenance["client_submission"] = True

        asset = {
            "id": _new_id("media"),
            "name": " ".join(body.name.split())[:240],
            "kind": body.kind,
            "source_type": body.source_type,
            "source_ref": source_ref,
            "folder_id": body.folder_id,
            "tags": list(dict.fromkeys(" ".join(tag.split())[:80] for tag in body.tags if " ".join(tag.split())))[:60],
            "rights_confirmed": bool(body.rights_confirmed),
            "rights_note": (body.rights_note or "").strip()[:2000],
            "provenance": provenance,
            "approval_state": approval_state,
            "approved_by": actor if approval_state == "approved" else "",
            "approved_at": utc_now() if approval_state == "approved" else None,
            "archived": False,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        library["assets"].append(asset)
        house.activity.append(
            ActivityEvent(
                actor=actor,
                action="media_added" if approval_state == "approved" else "media_quarantined",
                entity_type="media_asset",
                entity_id=asset["id"],
                detail=f"{body.kind}:{body.source_type}",
            )
        )
        self.social.save(house)
        return asset

    def _asset(self, house, asset_id: str) -> dict:
        asset = next((item for item in _library(house)["assets"] if item.get("id") == asset_id), None)
        if asset is None:
            raise KeyError(asset_id)
        return asset

    def approve_asset(self, space_id: str, asset_id: str, body: ApproveMediaRequest, *, actor: str) -> dict:
        house = self.social.load(space_id)
        asset = self._asset(house, asset_id)
        if asset.get("archived"):
            raise ValueError("Archived media cannot be approved")
        if not body.rights_confirmed:
            raise ValueError("Rights confirmation is required before media can be approved")
        asset["rights_confirmed"] = True
        asset["rights_note"] = (body.rights_note or asset.get("rights_note") or "").strip()[:2000]
        asset["approval_state"] = "approved"
        asset["approved_by"] = actor
        asset["approved_at"] = utc_now()
        asset["updated_at"] = utc_now()
        house.activity.append(ActivityEvent(actor=actor, action="media_approved", entity_type="media_asset", entity_id=asset_id))
        self.social.save(house)
        return asset

    def reject_asset(self, space_id: str, asset_id: str, *, actor: str) -> dict:
        house = self.social.load(space_id)
        asset = self._asset(house, asset_id)
        if _usage_count(house, asset_id):
            raise ValueError("Detach this media from planned content before rejecting it")
        asset["approval_state"] = "rejected"
        asset["approved_by"] = ""
        asset["approved_at"] = None
        asset["updated_at"] = utc_now()
        house.activity.append(ActivityEvent(actor=actor, action="media_rejected", entity_type="media_asset", entity_id=asset_id))
        self.social.save(house)
        return asset

    @staticmethod
    def _invalidate_content_approval(content) -> None:
        if content.approval_required and content.status in {"approved", "scheduled"}:
            content.status = "pending_approval"
            content.approved_by = []
            content.approval_at = None
        content.updated_at = utc_now()

    def attach(self, space_id: str, asset_id: str, body: AttachMediaRequest, *, actor: str) -> dict:
        house = self.social.load(space_id)
        asset = self._asset(house, asset_id)
        if asset.get("archived") or asset.get("approval_state") != "approved" or not asset.get("rights_confirmed"):
            raise PermissionError("Only approved, rights-confirmed media can be attached to planned content")
        content = next((item for item in house.content if item.id == body.content_id), None)
        if content is None:
            raise KeyError(body.content_id)
        if content.status in {"publishing", "published"}:
            raise ValueError("Published/publishing content cannot be mutated through the media library")
        variant = next((item for item in content.variants if item.platform == body.platform), None)
        if variant is None:
            raise ValueError("Content does not have a variant for that platform")
        ref = f"library:{asset_id}"
        if ref not in variant.media_refs:
            variant.media_refs.append(ref)
        self._invalidate_content_approval(content)
        SocialHouseStore.validate_content(content)
        house.activity.append(ActivityEvent(actor=actor, action="media_attached", entity_type="content", entity_id=content.id, detail=f"{body.platform}:{asset_id}"))
        self.social.save(house)
        return {
            "content_id": content.id,
            "platform": body.platform,
            "media_ref": ref,
            "approval_status": content.status,
            "external_publish_triggered": False,
        }

    def detach(self, space_id: str, asset_id: str, body: AttachMediaRequest, *, actor: str) -> dict:
        house = self.social.load(space_id)
        self._asset(house, asset_id)
        content = next((item for item in house.content if item.id == body.content_id), None)
        if content is None:
            raise KeyError(body.content_id)
        if content.status in {"publishing", "published"}:
            raise ValueError("Published/publishing content cannot be mutated through the media library")
        variant = next((item for item in content.variants if item.platform == body.platform), None)
        if variant is None:
            raise ValueError("Content does not have a variant for that platform")
        ref = f"library:{asset_id}"
        variant.media_refs = [value for value in variant.media_refs if value != ref]
        if variant.cover_ref == ref:
            variant.cover_ref = None
        self._invalidate_content_approval(content)
        house.activity.append(ActivityEvent(actor=actor, action="media_detached", entity_type="content", entity_id=content.id, detail=f"{body.platform}:{asset_id}"))
        self.social.save(house)
        return {"content_id": content.id, "platform": body.platform, "media_ref": ref, "external_publish_triggered": False}


library = None


def _store() -> SocialMediaLibraryStore:
    # SocialHouseStore binds to the request's tenant context, so never retain a global instance.
    return SocialMediaLibraryStore()


def _member(request: Request):
    member, _membership, _profile = require_esp_social_member(request)
    return member


@router.get("/command-center/api/social/media-library")
def library_spaces(request: Request):
    _member(request)
    db = SocialHouseStore()
    return {"spaces": db.list_spaces(), "external_publish_triggered": False}


@router.get("/command-center/api/social/media-library/{space_id}")
def library_state(space_id: str, request: Request):
    _member(request)
    try:
        return _store().state(space_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Social House not found") from exc


@router.post("/command-center/api/social/media-library/{space_id}/folders")
def create_folder(space_id: str, body: CreateFolderRequest, request: Request):
    _member(request)
    try:
        return {"folder": _store().create_folder(space_id, body.name)}
    except FileNotFoundError as exc:
        raise HTTPException(404, "Social House not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/command-center/api/social/media-library/{space_id}/assets")
def add_asset(space_id: str, body: AddMediaRequest, request: Request):
    member = _member(request)
    try:
        return {"asset": _store().add_asset(space_id, body, actor=member.user_id), "external_publish_triggered": False}
    except FileNotFoundError as exc:
        raise HTTPException(404, "Social House or Creative Project not found") from exc
    except KeyError as exc:
        raise HTTPException(404, "Folder or Creative Element not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/command-center/api/social/media-library/{space_id}/assets/{asset_id}/approve")
def approve_asset(space_id: str, asset_id: str, body: ApproveMediaRequest, request: Request):
    member = _member(request)
    try:
        return {"asset": _store().approve_asset(space_id, asset_id, body, actor=member.user_id)}
    except KeyError as exc:
        raise HTTPException(404, "Media asset not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/command-center/api/social/media-library/{space_id}/assets/{asset_id}/reject")
def reject_asset(space_id: str, asset_id: str, request: Request):
    member = _member(request)
    try:
        return {"asset": _store().reject_asset(space_id, asset_id, actor=member.user_id)}
    except KeyError as exc:
        raise HTTPException(404, "Media asset not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/command-center/api/social/media-library/{space_id}/assets/{asset_id}/attach")
def attach_asset(space_id: str, asset_id: str, body: AttachMediaRequest, request: Request):
    member = _member(request)
    try:
        return _store().attach(space_id, asset_id, body, actor=member.user_id)
    except KeyError as exc:
        raise HTTPException(404, "Media asset or content item not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/command-center/api/social/media-library/{space_id}/assets/{asset_id}/detach")
def detach_asset(space_id: str, asset_id: str, body: AttachMediaRequest, request: Request):
    member = _member(request)
    try:
        return _store().detach(space_id, asset_id, body, actor=member.user_id)
    except KeyError as exc:
        raise HTTPException(404, "Media asset or content item not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


CSS = r"""
:root{--line:#ffffff1d;--muted:#c1bfd1;--gold:#efc86f;--violet:#9d70ff;--good:#78dfa7;--warn:#ffd17b;--bad:#ff90a4}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 88% 0,#43175d,transparent 30%),#06050c;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1180px,calc(100% - 28px));margin:auto;padding:36px 0 60px}a{color:inherit;text-decoration:none}.top,.row{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap}.eyebrow{font-size:.7rem;letter-spacing:.15em;text-transform:uppercase;color:var(--gold);font-weight:950}h1{font-size:clamp(2.6rem,7vw,5.2rem);letter-spacing:-.055em;line-height:.94;margin:.15em 0}.muted{color:var(--muted);line-height:1.55}.btn,.field{border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#09070f;color:#fff;font:inherit}.btn{font-weight:850;cursor:pointer}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--violet));color:#160d1d}.card{border:1px solid var(--line);border-radius:15px;background:#14101ceb;padding:14px;margin:9px 0}.grid{display:grid;grid-template-columns:.8fr 1.2fr;gap:12px}.field{width:100%;margin:5px 0}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:5px 8px;font-size:.7rem}.approved{color:var(--good)}.quarantined{color:var(--warn)}.rejected{color:var(--bad)}.notice{display:none;border:1px solid var(--line);border-radius:10px;padding:10px}.notice.show{display:block}@media(max-width:780px){.grid{grid-template-columns:1fr}}
"""

SCRIPT = r"""
const API='/command-center/api/social/media-library',$=id=>document.getElementById(id);let spaces=[],state=null;function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function note(m,b=false){const n=$('notice');n.textContent=m;n.className='notice show';n.style.borderColor=b?'var(--bad)':''}async function req(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...opt});let d={};try{d=await r.json()}catch(_){}if(!r.ok)throw new Error(d.detail||`Request failed (${r.status})`);return d}function sid(){return $('space').value}function render(){const folders=state?.folders||[];$('folder').innerHTML='<option value="">No folder</option>'+folders.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');$('summary').textContent=`${state?.counts?.assets||0} assets · ${state?.counts?.approved||0} approved · ${state?.counts?.quarantined||0} quarantined`;$('assets').innerHTML=(state?.assets||[]).map(a=>`<article class="card"><div class="row"><div><span class="pill ${esc(a.approval_state)}">${esc(a.approval_state)}</span><h2>${esc(a.name)}</h2><p class="muted">${esc(a.kind)} · ${esc(a.source_type)} · used ${a.usage_count||0} time(s)</p><p class="muted">${esc(a.rights_note||'')}</p></div><div>${a.approval_state==='quarantined'?`<button class="btn primary" onclick="approve('${esc(a.id)}')">Approve rights</button> <button class="btn" onclick="rejectAsset('${esc(a.id)}')">Reject</button>`:''}${a.approval_state==='approved'?`<button class="btn" onclick="attach('${esc(a.id)}')">Attach to content</button>`:''}</div></div></article>`).join('')||'<div class="card muted">No media assets yet.</div>'}async function loadSpaces(){const d=await req(API);spaces=d.spaces||[];$('space').innerHTML=spaces.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');if(spaces.length)await load()}async function load(){if(!sid())return;state=await req(API+'/'+encodeURIComponent(sid()));render()}$('space').onchange=load;$('addFolder').onclick=async()=>{const name=prompt('Folder name:')||'';if(!name.trim())return;try{await req(`${API}/${encodeURIComponent(sid())}/folders`,{method:'POST',body:JSON.stringify({name})});await load()}catch(e){note(e.message,true)}};$('add').onclick=async()=>{try{await req(`${API}/${encodeURIComponent(sid())}/assets`,{method:'POST',body:JSON.stringify({name:$('name').value,kind:$('kind').value,source_type:$('sourceType').value,source_ref:$('sourceRef').value,source_project:$('project').value,source_element_id:$('element').value,folder_id:$('folder').value||null,tags:[],rights_confirmed:$('rights').checked,rights_note:$('rightsNote').value})});note('Media added. External/client sources remain quarantined until approved.');await load()}catch(e){note(e.message,true)}};async function approve(id){const rights_note=prompt('Rights/provenance note (recommended):')||'';try{await req(`${API}/${encodeURIComponent(sid())}/assets/${encodeURIComponent(id)}/approve`,{method:'POST',body:JSON.stringify({rights_confirmed:true,rights_note})});await load()}catch(e){note(e.message,true)}}async function rejectAsset(id){try{await req(`${API}/${encodeURIComponent(sid())}/assets/${encodeURIComponent(id)}/reject`,{method:'POST'});await load()}catch(e){note(e.message,true)}}async function attach(id){const content_id=prompt('Content item ID:')||'';if(!content_id)return;const platform=prompt('Platform variant (for example tiktok or instagram):','tiktok')||'';if(!platform)return;try{const d=await req(`${API}/${encodeURIComponent(sid())}/assets/${encodeURIComponent(id)}/attach`,{method:'POST',body:JSON.stringify({content_id,platform})});note(`Media attached. Content status is now ${d.approval_status}; no publishing was triggered.`);await load()}catch(e){note(e.message,true)}}loadSpaces().catch(e=>note(e.message,true));
"""


@router.get("/command-center/social/media-library", response_class=HTMLResponse, include_in_schema=False)
def media_library_page(request: Request):
    _member(request)
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>ESP Social Media Library</title><style>{CSS}</style></head><body><main class='wrap'><div class='top'><div><div class='eyebrow'>Pulsar-Frequency House · Social Media Management</div><h1>Media Library & Provenance</h1><p class='muted'>Organise Creative DNA outputs and external/client references behind a rights-confirmation and approval boundary. Adding or attaching media never publishes externally.</p></div><div><a class='btn' href='/command-center/social'>Social Media Centre</a> <a class='btn' href='/command-center/social/approvals'>Approval Inbox</a></div></div><div id='notice' class='notice'></div><section class='card row'><select id='space' class='field' style='width:auto'></select><button id='addFolder' class='btn'>+ Folder</button><span id='summary' class='muted'></span></section><section class='grid'><div class='card'><h2>Add media reference</h2><input id='name' class='field' placeholder='Asset name'><select id='kind' class='field'><option>image</option><option>video</option><option>audio</option><option>document</option><option>other</option></select><select id='sourceType' class='field'><option value='creative_element'>Creative DNA element</option><option value='external_url'>External HTTPS URL</option><option value='artifact_ref'>Application artifact reference</option><option value='client_submission'>Client submission reference</option></select><input id='sourceRef' class='field' placeholder='External URL / application ref'><input id='project' class='field' placeholder='Creative project name (Creative element only)'><input id='element' class='field' placeholder='Creative element ID (Creative element only)'><select id='folder' class='field'><option value=''>No folder</option></select><label><input id='rights' type='checkbox'> I confirm I have the required rights/permission for this material.</label><textarea id='rightsNote' class='field' placeholder='Rights / source / provenance note'></textarea><button id='add' class='btn primary'>Add to Media Library</button><p class='muted'>Verified Creative DNA elements can enter approved when rights are confirmed. External/client sources are quarantined for a separate human approval step.</p></div><div><h2>Library assets</h2><div id='assets'><div class='card muted'>Loading…</div></div></div></section></main><script>{SCRIPT}</script></body></html>""",
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router", "SocialMediaLibraryStore", "AddMediaRequest", "AttachMediaRequest"]
