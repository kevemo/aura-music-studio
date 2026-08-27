from __future__ import annotations

import pytest
from pydantic import ValidationError

from aura_music_studio.aura_sec_optimizer import (
    CandidateKind,
    OptimizationPlan,
    OptimizerAction,
    OptimizerCandidate,
    OptimizerProposal,
)


DEVICE = "device_1234567890abcdef"
SCAN = "scan_1234567890abcdef"


def candidate(kind: CandidateKind, **kwargs) -> OptimizerCandidate:
    data = {
        "id": "candidate_12345678",
        "kind": kind,
        "display_name": "Example",
        "size_bytes": 1024,
    }
    data.update(kwargs)
    return OptimizerCandidate(**data)


def test_os_declared_temp_can_be_low_risk_cleanup():
    proposal = OptimizerProposal(
        candidate=candidate(CandidateKind.OS_TEMPORARY, os_declared_safe_temp=True),
        action=OptimizerAction.DELETE_OS_TEMP,
        reason="Operating system marked this cache as safe temporary data.",
        estimated_reclaim_bytes=1024,
        requires_confirmation=False,
        reversible=False,
    )
    plan = OptimizationPlan(device_id=DEVICE, scan_id=SCAN, proposals=[proposal])
    assert plan.estimated_reclaim_bytes == 1024
    assert plan.pending_confirmations == 0


def test_personal_document_can_never_receive_permanent_delete_action():
    with pytest.raises(ValidationError, match="personal/member files"):
        OptimizerProposal(
            candidate=candidate(CandidateKind.USER_DOCUMENT),
            action=OptimizerAction.DELETE_OS_TEMP,
            reason="Unsafe proposal fixture.",
            estimated_reclaim_bytes=1024,
            requires_confirmation=False,
            reversible=False,
        )


def test_personal_document_move_requires_reversible_trash_and_confirmation():
    user_file = candidate(CandidateKind.USER_DOCUMENT, reversible_move_available=True)
    with pytest.raises(ValidationError, match="always require confirmation"):
        OptimizerProposal(
            candidate=user_file,
            action=OptimizerAction.MOVE_TO_TRASH,
            reason="Duplicate candidate.",
            estimated_reclaim_bytes=1024,
            requires_confirmation=False,
            reversible=True,
            recovery_note="Restore from operating-system trash.",
        )

    proposal = OptimizerProposal(
        candidate=user_file,
        action=OptimizerAction.MOVE_TO_TRASH,
        reason="Duplicate candidate; exact file remains previewed for the member.",
        estimated_reclaim_bytes=1024,
        requires_confirmation=True,
        reversible=True,
        recovery_note="Restore from operating-system trash.",
    )
    assert proposal.requires_confirmation is True


def test_active_project_asset_cannot_be_moved():
    with pytest.raises(ValidationError, match="active project assets"):
        OptimizerProposal(
            candidate=candidate(
                CandidateKind.PROJECT_ASSET,
                active_project_reference=True,
                reversible_move_available=True,
            ),
            action=OptimizerAction.MOVE_TO_TRASH,
            reason="Unsafe active project move fixture.",
            estimated_reclaim_bytes=1024,
            requires_confirmation=True,
            reversible=True,
            recovery_note="Restore from trash.",
        )


def test_security_backup_and_accessibility_components_cannot_be_disabled_for_speed():
    for flag in ("security_component", "backup_component", "accessibility_component"):
        with pytest.raises(ValidationError, match="cannot be disabled"):
            OptimizerProposal(
                candidate=candidate(CandidateKind.STARTUP_ITEM, **{flag: True}),
                action=OptimizerAction.DISABLE_STARTUP,
                reason="Unsafe performance proposal fixture.",
                estimated_reclaim_bytes=0,
                requires_confirmation=True,
                reversible=True,
                recovery_note="Re-enable startup item.",
            )


def test_protected_system_location_is_keep_only():
    with pytest.raises(ValidationError, match="protected system locations"):
        OptimizerProposal(
            candidate=candidate(CandidateKind.PROTECTED_SYSTEM, protected_location=True),
            action=OptimizerAction.CLEAR_APP_CACHE,
            reason="Unsafe protected path mutation fixture.",
            estimated_reclaim_bytes=512,
            requires_confirmation=True,
            reversible=False,
        )


def test_registry_cleaner_and_permanent_member_document_flags_are_forbidden():
    with pytest.raises(ValidationError, match="registry-cleaner"):
        OptimizationPlan(device_id=DEVICE, scan_id=SCAN, registry_cleaner_used=True)
    with pytest.raises(ValidationError, match="never permanently deletes"):
        OptimizationPlan(device_id=DEVICE, scan_id=SCAN, member_documents_permanently_deleted=True)
