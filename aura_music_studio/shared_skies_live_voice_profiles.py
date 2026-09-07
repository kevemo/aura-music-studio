from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request

from . import tenant_storage
from .esp_niche import require_esp_hub_member
from .request_context import current_user_id
from .rights import RightsLedger, VoiceProfile
from .shared_sky_control_room import studio_repo

router = APIRouter(tags=["Shared Skies LIVE Voice Profiles"])

VoicePurpose = Literal["speech", "voice_conversion", "singing", "backing_harmony", "dubbing"]


def _tenant_rights_root(user_id: str, project_name: str):
    """Resolve Chat 2 rights storage only inside the authenticated tenant context."""

    active_user = current_user_id()
    if not active_user or str(active_user) != str(user_id):
        raise PermissionError("Authenticated tenant context does not match the LIVE member.")
    project = tenant_storage.project_path(project_name, must_exist=True)
    return project / ".aura_rights"


def _public_profile(profile: VoiceProfile, purpose: VoicePurpose, user_id: str) -> dict[str, Any]:
    """Return a deliberately narrow discovery projection, never raw references or provider state."""

    profile.assert_tenant(user_id)
    profile.assert_usable(purpose)
    return {
        "profile_id": profile.id,
        "profile_name": profile.name,
        "owner": profile.owner_label,
        "consent_state": profile.verification_state,
        "consent_confirmed": bool(profile.consent_confirmed),
        "active": bool(profile.active),
        "available_modes": sorted({str(item) for item in profile.allowed_uses if str(item).strip()}),
        "purpose_authorised": True,
        "language_support": "not_declared_by_chat2_profile_contract",
        "provider_runtime": "not_attached_to_shared_skies_live",
        "real_time_capability": False,
        "latency": "unavailable",
        "entitlement_state": "not_evaluated_chat6_authority",
        "live_binding": "candidate_only",
        "raw_reference_files_exposed": False,
        "model_or_provider_secrets_exposed": False,
    }


def authorised_profiles_for_live(
    user_id: str,
    project_name: str,
    purpose: VoicePurpose = "speech",
) -> list[dict[str, Any]]:
    """Reload Chat 2 Voice Profiles and return only currently usable tenant-scoped candidates."""

    rights_root = _tenant_rights_root(user_id, project_name)
    if not rights_root.exists():
        return []
    ledger = RightsLedger(rights_root)
    rows: list[dict[str, Any]] = []
    for profile in ledger.list_voices():
        try:
            rows.append(_public_profile(profile, purpose, user_id))
        except PermissionError:
            # Revoked, unconsented, wrong-purpose and cross-tenant profiles are not selectable.
            continue
    return sorted(rows, key=lambda item: (str(item["profile_name"]).lower(), str(item["profile_id"])))


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, "Shared Skies Studio session was not found")
    if isinstance(exc, FileNotFoundError):
        return HTTPException(404, "Chat 2 Voice House project was not found")
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    return HTTPException(400, "Authorised LIVE voice profile discovery could not be completed")


@router.get("/shared-sky/studio/api/sessions/{session_id}/voice/profiles")
def live_voice_profile_candidates(
    session_id: str,
    request: Request,
    chat2_project_name: str,
    purpose: VoicePurpose = "speech",
):
    """Discover Chat 2-authorised profile candidates without creating Chat 5 voice authority."""

    member, _membership = require_esp_hub_member(request)
    user_id = str(member.user_id)
    try:
        # A member may discover profiles only for a Studio session they actually own.
        studio_repo.get_session(user_id, session_id)
        profiles = authorised_profiles_for_live(user_id, chat2_project_name, purpose)
        return {
            "product": "Shared Skies Streaming Studios LIVE Voice",
            "session_id": session_id,
            "chat2_project_name": chat2_project_name,
            "purpose": purpose,
            "profiles": profiles,
            "selection_contract": {
                "profile_authority": "Chat 2 Voice House / RightsLedger",
                "chat5_profile_database": False,
                "selection_state": "candidate_only",
                "server_authoritative_live_binding": "unavailable",
                "processor_runtime_attached": False,
                "real_time_processing_proven": False,
                "final_execution_reauthorisation_required": True,
                "final_execution_authority": "Chat 2 authorize_voice_profile",
                "entitlement_authority": "Chat 6",
                "entitlement_evaluated_by_chat5": False,
                "client_entitlement_authority": False,
                "joining_or_recording_live_implies_clone_consent": False,
            },
        }
    except Exception as exc:
        raise _http_error(exc) from exc


def install_shared_skies_live_voice_profiles(app: Any) -> None:
    existing = {
        (
            str(getattr(route, "path", "")),
            tuple(sorted(str(method).upper() for method in (getattr(route, "methods", set()) or set()))),
        )
        for route in app.router.routes
    }
    for route in tuple(router.routes):
        signature = (
            str(getattr(route, "path", "")),
            tuple(sorted(str(method).upper() for method in (getattr(route, "methods", set()) or set()))),
        )
        if signature in existing:
            continue
        app.router.routes.append(route)
        existing.add(signature)


__all__ = [
    "VoicePurpose",
    "authorised_profiles_for_live",
    "install_shared_skies_live_voice_profiles",
    "live_voice_profile_candidates",
    "router",
]
