from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_support_center import SupportCaseStore, _validate_evidence, router


def _user(accounts: AccountStore, email: str):
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    return accounts.decide_membership(signup.approval_token, "approve", "Owner")


def _setup(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    first = _user(accounts, "support-one@example.com")
    second = _user(accounts, "support-two@example.com")
    esp = EspStore(accounts)
    return accounts, first, second, SupportCaseStore(esp)


def test_support_case_is_private_to_member_or_owner(tmp_path):
    _accounts, first, second, store = _setup(tmp_path)
    case = store.create_case(
        first["id"],
        category="technical",
        severity="normal",
        subject="LIVE Studio audio routing",
        description="Need help checking an audio routing problem.",
    )
    assert store.get(case["id"], user_id=first["id"])["user_id"] == first["id"]
    with pytest.raises(PermissionError, match="private"):
        store.get(case["id"], user_id=second["id"])
    assert store.get(case["id"], owner=True)["id"] == case["id"]


def test_evidence_validation_blocks_file_paths_and_non_http_urls():
    assert _validate_evidence("url", "https://example.com/evidence") == "https://example.com/evidence"
    with pytest.raises(ValueError, match="http or https"):
        _validate_evidence("url", "file:///etc/passwd")
    with pytest.raises(ValueError, match="not a file path"):
        _validate_evidence("artifact_ref", "../../secret.txt")


def test_member_can_add_evidence_and_export_pack_without_fault_decision(tmp_path):
    _accounts, first, _second, store = _setup(tmp_path)
    case = store.create_case(
        first["id"],
        category="harassment",
        severity="high",
        subject="Private evidence review",
        description="Organise supplied evidence for human review.",
    )
    updated = store.add_evidence(
        case["id"],
        user_id=first["id"],
        owner=False,
        kind="note",
        value="Screenshot reference and incident context recorded privately.",
        actor=first["id"],
    )
    assert len(updated["evidence"]) == 1
    pack = store.evidence_pack(case["id"], user_id=first["id"])
    assert pack["pack"]["automated_fault_decision"] is False
    assert pack["pack"]["human_review_required"] is True
    assert len(pack["sha256"]) == 64


def test_owner_triage_is_audited_and_does_not_change_evidence(tmp_path):
    _accounts, first, _second, store = _setup(tmp_path)
    case = store.create_case(
        first["id"],
        category="policy",
        severity="normal",
        subject="Policy guidance",
        description="Need a private owner review of the current rules.",
    )
    before_evidence = list(case["evidence"])
    updated = store.owner_update(
        case["id"],
        status="in_progress",
        assigned_owner="Mary / Kev",
        resolution="Reviewing the supplied context.",
        actor="owner",
    )
    assert updated["status"] == "in_progress"
    assert updated["assigned_owner"] == "Mary / Kev"
    assert updated["evidence"] == before_evidence
    assert any(event["action"] == "owner_case_updated" for event in updated["activity"])


def test_support_routes_cover_member_evidence_pack_and_owner_triage():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/support" in paths
    assert "/command-center/api/support/cases" in paths
    assert "/command-center/api/support/cases/{case_id}" in paths
    assert "/command-center/api/support/cases/{case_id}/evidence" in paths
    assert "/command-center/api/support/cases/{case_id}/evidence-pack" in paths
    assert "/command-center/api/support/owner/cases" in paths
    assert "/command-center/api/support/owner/cases/{case_id}" in paths
