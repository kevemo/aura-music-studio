from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .request_context import current_user_id
from .rights import RightsLedger, VoiceProfile
from .tenant_storage import project_path

router = APIRouter(tags=["Voice House"])


class RenameVoiceProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class DeleteVoiceProfileRequest(BaseModel):
    confirm_delete: bool = False
    reason: str = Field(default="Voice profile deleted by authorised user", max_length=1000)


def _project(name: str) -> Path:
    try:
        return project_path(name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc


def _ledger(project: Path) -> RightsLedger:
    return RightsLedger(project / ".aura_rights")


def _profile_for_request(project: Path, profile_id: str) -> VoiceProfile:
    try:
        profile = _ledger(project).get_voice(profile_id)
    except KeyError as exc:
        raise HTTPException(404, "Voice Profile not found") from exc
    try:
        profile.assert_tenant(current_user_id())
    except PermissionError as exc:
        # Do not disclose whether a cross-tenant identifier exists.
        raise HTTPException(404, "Voice Profile not found") from exc
    return profile


def _bind_legacy_profile_to_current_tenant(project: Path, profile: VoiceProfile) -> VoiceProfile:
    """Safely bind old project-scoped profiles when accessed inside an authenticated tenant project."""
    user_id = current_user_id()
    if not user_id or profile.tenant_user_id:
        return profile
    profile.tenant_user_id = user_id
    if not profile.created_by_user_id:
        profile.created_by_user_id = user_id
    profile.metadata.setdefault("tenant_binding_migrated_from_project_scope", True)
    return _ledger(project).save_voice(profile)


def _safe_profile_root(project: Path, profile_id: str) -> Path:
    root = (project / "input" / "voice_profiles").resolve()
    target = (root / profile_id).resolve()
    if root not in target.parents:
        raise HTTPException(400, "Invalid Voice Profile storage identifier")
    return target


def _public_profile(profile: VoiceProfile) -> dict:
    return {
        "id": profile.id,
        "name": profile.name,
        "owner_label": profile.owner_label,
        "subject_relationship": profile.subject_relationship,
        "consent_confirmed": profile.consent_confirmed,
        "consent_recorded_at": profile.consent_recorded_at,
        "verification_state": profile.verification_state,
        "verification_method": profile.verification_method,
        "verification_confidence": profile.verification_confidence,
        "allowed_uses": profile.allowed_uses,
        "similarity_limit": profile.similarity_limit,
        "version": profile.version,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
        "last_used_at": profile.last_used_at,
        "revoked_at": profile.revoked_at,
        "revoked_reason": profile.revoked_reason,
        "active": profile.active,
        "reference_count": len(profile.reference_files),
        "private": True,
        "raw_reference_paths_exposed": False,
    }


@router.get("/projects/{project_name}/voice-house/profiles/{profile_id}")
def get_voice_profile(project_name: str, profile_id: str):
    project = _project(project_name)
    profile = _bind_legacy_profile_to_current_tenant(project, _profile_for_request(project, profile_id))
    return {"voice_profile": _public_profile(profile)}


@router.patch("/projects/{project_name}/voice-house/profiles/{profile_id}")
def rename_voice_profile(project_name: str, profile_id: str, request: RenameVoiceProfileRequest):
    project = _project(project_name)
    profile = _bind_legacy_profile_to_current_tenant(project, _profile_for_request(project, profile_id))
    ledger = _ledger(project)
    try:
        updated = ledger.rename_voice(profile.id, request.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "voice_profile": _public_profile(updated),
        "detail": "Voice Profile renamed and version advanced; consent and reference identity are unchanged.",
    }


@router.delete("/projects/{project_name}/voice-house/profiles/{profile_id}")
def delete_voice_profile(project_name: str, profile_id: str, request: DeleteVoiceProfileRequest):
    if not request.confirm_delete:
        raise HTTPException(409, "Explicit confirm_delete=true is required to erase a Voice Profile")

    project = _project(project_name)
    profile = _bind_legacy_profile_to_current_tenant(project, _profile_for_request(project, profile_id))
    ledger = _ledger(project)

    # Revoke first so a concurrent execution boundary cannot legitimately authorise the profile
    # once deletion has begun. Runtime callers re-read this authoritative ledger before execution.
    revoked_before_delete = False
    if profile.active:
        profile = ledger.revoke_voice(profile.id, request.reason)
        revoked_before_delete = True

    storage_root = _safe_profile_root(project, profile.id)
    deleted_private_artifacts = False
    if storage_root.is_dir():
        shutil.rmtree(storage_root)
        deleted_private_artifacts = True

    ledger.delete_voice(profile.id)
    return {
        "deleted": True,
        "profile_id": profile.id,
        "revoked_before_delete": revoked_before_delete,
        "private_artifacts_deleted": deleted_private_artifacts,
        "raw_reference_paths_exposed": False,
        "detail": "Voice Profile removed from the active rights ledger; bounded private profile artefacts were erased when present.",
    }


__all__ = ["router"]
