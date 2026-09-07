from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_backstage_evidence import BackstageEvidenceStore
from aura_music_studio.esp_backstage_vision import BackstageVisionStore, _extract_json, router as vision_router
from aura_music_studio.esp_backstage_vision_portal import router as portal_router
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_level_up import EspAgentAssignmentStore
from aura_music_studio.esp_niche import EspNicheStore
from aura_music_studio.esp_progress import EspProgressStore, save_progress_upload


class FakeVision:
    configured = True
    model = "fake-vision"

    def analyze_images(self, images, instruction):
        assert len(images) == 1
        assert "strict JSON" in instruction
        return '{"metrics":{"views":1200,"avg_watch_seconds":44,"peak_viewers":86,"shares":3},"confidence":0.91,"notes":"visible values only"}'

    def diagnostics(self):
        return {"configured": True, "provider": "fake", "model": self.model}


class DisabledVision:
    configured = False
    model = ""


def _active(accounts: AccountStore, esp: EspStore, email: str, role: str):
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    _request, token = esp.request_access(user["id"], role, email.split("@")[0], "UK+", "test")
    esp.decide(token, "approve", role, "Owner")
    return user


def _setup(tmp_path, monkeypatch):
    root = tmp_path / "progress"
    monkeypatch.setenv("ESP_PROGRESS_ROOT", str(root))
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    EspNicheStore(esp)
    agent = _active(accounts, esp, "agent@example.com", "agent")
    creator = _active(accounts, esp, "creator@example.com", "creator")
    other = _active(accounts, esp, "other@example.com", "creator")
    assignments = EspAgentAssignmentStore(esp)
    assignments.assign(agent["id"], creator["id"], actor="Owner")
    progress = EspProgressStore(esp)
    evidence = BackstageEvidenceStore(esp, assignments, progress)
    return accounts, esp, agent, creator, other, assignments, progress, evidence


def _screenshot(evidence, actor_id, creator_id, *, owner=False):
    name, path = save_progress_upload(creator_id, "manage-creator.png", b"fake-image-bytes")
    return evidence.record(
        actor_id, creator_id, owner=owner, source_kind="screenshot",
        source_label="TikTok Manage Creator screenshot", captured_at="2026-08-27T00:00:00+00:00",
        period_label="Weekly review", metrics=None, extraction_status="visual_review_required",
        upload_name=name, upload_path=path, upload_content_type="image/png",
    )


def test_json_parser_accepts_fenced_vision_json():
    payload = _extract_json('```json\n{"metrics":{"views":42},"confidence":0.8}\n```')
    assert payload["metrics"]["views"] == 42
    with pytest.raises(ValueError):
        _extract_json("I cannot find any structured values")


def test_vision_proposes_metrics_but_does_not_update_progress_until_confirmed(tmp_path, monkeypatch):
    _accounts, _esp, agent, creator, _other, _assignments, progress, evidence = _setup(tmp_path, monkeypatch)
    row = _screenshot(evidence, agent["id"], creator["id"])
    store = BackstageVisionStore(evidence, vision_factory=FakeVision)
    result = store.analyze(row["id"], agent["id"], owner=False)
    assert result["proposed_metrics"]["views"] == 1200
    assert result["proposed_metrics"]["avg_watch_seconds"] == 44
    assert result["confidence"] == 0.91
    assert result["human_confirmation_required"] is True
    assert result["progress_updated"] is False
    assert result["direct_backstage_access"] is False
    assert progress.summary(creator["id"])["total"] == 0


def test_human_can_correct_metrics_before_confirmation(tmp_path, monkeypatch):
    _accounts, _esp, agent, creator, _other, _assignments, progress, evidence = _setup(tmp_path, monkeypatch)
    row = _screenshot(evidence, agent["id"], creator["id"])
    store = BackstageVisionStore(evidence, vision_factory=FakeVision)
    proposed = store.analyze(row["id"], agent["id"], owner=False)
    confirmed = store.confirm(
        proposed["run_id"], agent["id"], owner=False, confirm=True,
        metrics={"views": 1200, "avg_watch_seconds": 51, "peak_viewers": 86, "shares": 3},
        reviewer_note="Corrected average watch time after checking the screenshot.",
    )
    assert confirmed["status"] == "confirmed"
    assert confirmed["progress_updated"] is True
    assert confirmed["evidence"]["metrics"]["avg_watch_seconds"] == 51
    assert confirmed["evidence"]["extraction_status"] == "vision_confirmed"
    assert progress.summary(creator["id"])["total"] == 1


def test_rejected_vision_never_updates_creator_progress(tmp_path, monkeypatch):
    _accounts, _esp, agent, creator, _other, _assignments, progress, evidence = _setup(tmp_path, monkeypatch)
    row = _screenshot(evidence, agent["id"], creator["id"])
    store = BackstageVisionStore(evidence, vision_factory=FakeVision)
    proposed = store.analyze(row["id"], agent["id"], owner=False)
    rejected = store.confirm(
        proposed["run_id"], agent["id"], owner=False, confirm=False,
        reviewer_note="Screenshot labels were not clear enough to trust.",
    )
    assert rejected["status"] == "rejected"
    assert rejected["progress_updated"] is False
    assert progress.summary(creator["id"])["total"] == 0
    public = evidence.get(row["id"], actor_user_id=agent["id"], owner=False)
    assert public["extraction_status"] == "visual_review_required"


def test_agent_cannot_analyze_screenshot_for_unassigned_creator(tmp_path, monkeypatch):
    _accounts, _esp, agent, _creator, other, _assignments, _progress, evidence = _setup(tmp_path, monkeypatch)
    # Create owner-authorised evidence only to prove subsequent Agent access is still checked.
    row = _screenshot(evidence, agent["id"], other["id"], owner=True)
    store = BackstageVisionStore(evidence, vision_factory=FakeVision)
    with pytest.raises(PermissionError, match="not actively assigned"):
        store.analyze(row["id"], agent["id"], owner=False)


def test_unconfigured_vision_fails_truthfully_without_progress_mutation(tmp_path, monkeypatch):
    _accounts, _esp, agent, creator, _other, _assignments, progress, evidence = _setup(tmp_path, monkeypatch)
    row = _screenshot(evidence, agent["id"], creator["id"])
    store = BackstageVisionStore(evidence, vision_factory=DisabledVision)
    with pytest.raises(RuntimeError, match="not configured"):
        store.analyze(row["id"], agent["id"], owner=False)
    assert progress.summary(creator["id"])["total"] == 0


def test_vision_and_private_image_routes_stay_inside_agent_command_center():
    vision_paths = {getattr(route, "path", None) for route in vision_router.routes}
    portal_paths = {getattr(route, "path", None) for route in portal_router.routes}
    assert "/command-center/api/agent/backstage-evidence/{evidence_id}/vision" in vision_paths
    assert "/command-center/api/agent/backstage-vision/{run_id}/confirm" in vision_paths
    assert "/command-center/agent/backstage-vision" in portal_paths
    assert "/command-center/api/agent/backstage-evidence/{evidence_id}/image" in portal_paths
    assert all(path is None or path.startswith("/command-center/") for path in vision_paths | portal_paths)
