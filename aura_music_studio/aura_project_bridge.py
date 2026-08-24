from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .assets import AssetLibrary
from .aura_chat_store import AuraChatStore
from .creative_project import CreativeProjectStore, CreativeReference
from .project import ProjectWorkspace
from .rights import RightsLedger, RightsRecord
from .tenant_storage import list_project_dirs, project_path

router = APIRouter(tags=["Aura Project Bridge"])
store = AuraChatStore()


class PromoteAttachmentRequest(BaseModel):
    project_name: str | None = Field(default=None, max_length=120)
    rights_confirmed: bool = False
    attestation: str = Field(
        default="I confirm I own this material or have authorization to use it in this project.",
        min_length=12,
        max_length=1500,
    )
    usage: str = Field(default="creative reference and project source material", max_length=500)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _owned_chat_source(user_id: str, thread_id: str, attachment: dict) -> Path:
    root = Path(os.getenv("AURA_CHAT_ATTACHMENT_DIR", "data/aura/attachments")).resolve()
    thread_root = (root / user_id / thread_id).resolve()
    source = Path(str(attachment.get("stored_path") or "")).resolve()
    if root not in thread_root.parents or thread_root not in source.parents or not source.is_file():
        raise HTTPException(409, "Aura attachment storage record is no longer valid")
    return source


def _project_name(user_id: str, thread_id: str, requested: str | None) -> str:
    thread = store.thread(user_id, thread_id)
    if not thread:
        raise HTTPException(404, "Aura conversation not found")
    name = (requested or thread.get("project_name") or "").strip()
    if not name:
        raise HTTPException(409, "Pin a project to this Aura conversation before adding the attachment")
    owned = {path.name for path in list_project_dirs()}
    if name not in owned:
        raise HTTPException(404, "Pinned project is not available to this member")
    return name


def _reference_kind(attachment: dict, source: Path) -> str:
    kind = str(attachment.get("kind") or "").lower()
    if kind in {"image", "audio", "video", "text"}:
        return kind
    if source.suffix.lower() in {".txt", ".md", ".json", ".yaml", ".yml", ".csv", ".tsv"}:
        return "text"
    return "reference"


def _safe_name(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._")[:180]
    return clean or "aura_reference"


def _ensure_creative_store(project: Path, project_name_value: str) -> CreativeProjectStore:
    creative = CreativeProjectStore(project)
    if creative.exists():
        return creative
    title = project_name_value
    try:
        title = ProjectWorkspace(project).load_manifest().title
    except Exception:
        pass
    creative.initialize(
        project_name=project_name_value,
        title=title,
        project_intent="Cross-media project context managed through Aura Core",
    )
    return creative


@router.post("/aura-intelligence/api/threads/{thread_id}/attachments/{attachment_id}/promote")
def promote_attachment(thread_id: str, attachment_id: str, body: PromoteAttachmentRequest, request: Request):
    member = _member(request)
    if not body.rights_confirmed:
        raise HTTPException(400, "Confirm that you own or are authorised to use this attachment before adding it to a project")
    attachment = store.attachment(member.user_id, thread_id, attachment_id)
    if not attachment:
        raise HTTPException(404, "Aura attachment not found")
    source = _owned_chat_source(member.user_id, thread_id, attachment)
    name = _project_name(member.user_id, thread_id, body.project_name)
    try:
        project = project_path(name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc

    library = AssetLibrary(project)
    detected = library.detect_kind(source)
    asset = None
    rights_record_id = None

    if detected != "unsupported":
        # AssetLibrary is sha-idempotent at the file/index level and records the rights attestation.
        asset = library.ingest(
            source,
            kind=detected,
            rights_basis="user_owned_or_authorized_chat_attachment",
            attestation=body.attestation,
            tags=["aura-chat", "promoted"],
            notes=f"Promoted from private Aura attachment {attachment_id}",
        )
        project_relative = asset.path
        rights_record_id = asset.rights_record_id
    else:
        ledger = RightsLedger(project / ".aura_rights")
        digest = ledger.sha256(source)
        reference_dir = project / "input" / "references"
        reference_dir.mkdir(parents=True, exist_ok=True)
        destination = reference_dir / f"{digest[:12]}_{_safe_name(str(attachment.get('name') or source.name))}"
        if not destination.exists():
            shutil.copy2(source, destination)
        rights = RightsRecord(
            asset_name=Path(str(attachment.get("name") or source.name)).name,
            asset_sha256=digest,
            rights_basis="user_owned_or_authorized_chat_attachment",
            user_attestation=body.attestation,
        )
        ledger.add_rights_record(rights)
        rights_record_id = rights.id
        project_relative = destination.relative_to(project).as_posix()

    creative = _ensure_creative_store(project, name)
    manifest = creative.load()
    digest = str(attachment.get("sha256") or "")
    existing = next(
        (
            item
            for item in manifest.references
            if str(item.metadata.get("sha256") or "") == digest
            or item.source_ref == project_relative
        ),
        None,
    )
    if existing is None:
        reference = CreativeReference(
            kind=_reference_kind(attachment, source),
            label=Path(str(attachment.get("name") or source.name)).name[:200],
            source_ref=project_relative,
            usage=body.usage,
            rights_confirmed=True,
            metadata={
                "sha256": digest,
                "aura_chat_attachment_id": attachment_id,
                "promoted_by_user_id": member.user_id,
                "rights_record_id": rights_record_id,
            },
        )
        manifest = creative.add_reference(reference)
        existing = next(item for item in manifest.references if item.id == reference.id)

    return {
        "project_name": name,
        "attachment_id": attachment_id,
        "project_source_ref": project_relative,
        "asset": (
            {
                "id": asset.id,
                "name": asset.name,
                "kind": asset.kind,
                "sha256": asset.sha256,
                "analysis": asset.analysis,
            }
            if asset is not None
            else None
        ),
        "creative_reference": existing.model_dump(mode="json"),
        "rights_confirmed": True,
        "rights_recorded": bool(rights_record_id),
        "raw_private_chat_path_exposed": False,
        "idempotent": existing is not None and existing.metadata.get("aura_chat_attachment_id") != attachment_id,
    }


__all__ = ["router"]
