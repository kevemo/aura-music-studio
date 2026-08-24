from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.content_safety import enforce_creation_policy, evaluate_text
from aura_music_studio.creation import CreateSongRequest, build_song_project
from aura_music_studio.creative_project import CreativeDirective, CreativeProjectStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_level_up import install_esp_access_subscription_separation
from aura_music_studio.esp_niche import EspNicheStore
from aura_music_studio.esp_progress import EspProgressStore
from aura_music_studio.owner_user_control import OwnerUserControl

install_esp_access_subscription_separation()


def _active_user(tmp_path, email="creator@example.com", plan="free"):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup(email, "Creator Test", "a-very-secure-test-password", plan)
    user = accounts.decide_membership(signup.approval_token, "approve", "ESP Test Owner")
    return accounts, user


def test_subscription_and_esp_role_are_separate_owner_controls(tmp_path):
    accounts, user = _active_user(tmp_path)
    esp = EspStore(accounts)
    control = OwnerUserControl(accounts, esp)

    assert accounts.get_user(user["id"])["plan_id"] == "free"
    assert esp.membership(user["id"]) is None

    control.set_esp_role(user["id"], "creator", "Mary/Kev Test")
    membership = esp.membership(user["id"])
    assert membership["status"] == "active"
    assert membership["roles"] == "creator"
    assert accounts.get_user(user["id"])["plan_id"] == "free"
    assert accounts.get_user(user["id"])["billing_status"] == "not_required"

    control.set_plan(user["id"], "pro", "Mary/Kev Test")
    assert accounts.get_user(user["id"])["plan_id"] == "pro"
    assert accounts.get_user(user["id"])["billing_status"] == "owner_override"

    control.set_esp_role(user["id"], "regular", "Mary/Kev Test")
    assert esp.membership(user["id"])["status"] == "revoked"
    # Removing ESP does not remove a separately owner-set Pro entitlement.
    assert accounts.get_user(user["id"])["plan_id"] == "pro"


def test_owner_can_approve_self_declared_esp_request_without_email_token(tmp_path):
    accounts, user = _active_user(tmp_path)
    esp = EspStore(accounts)
    _request, _token = esp.request_access(user["id"], "agent", "creator.test", "UK+", "I am an ESP Agent")
    control = OwnerUserControl(accounts, esp)

    assert esp.pending_for_user(user["id"])["status"] == "pending"
    control.set_esp_role(user["id"], "agent", "Mary/Kev Test")

    assert esp.membership(user["id"])["roles"] == "agent"
    assert esp.pending_for_user(user["id"]) is None
    assert accounts.get_user(user["id"])["plan_id"] == "free"


def test_owner_can_decline_pending_esp_request(tmp_path):
    accounts, user = _active_user(tmp_path)
    esp = EspStore(accounts)
    esp.request_access(user["id"], "creator", "creator.test", "UK+", "request")
    control = OwnerUserControl(accounts, esp)

    control.decline_esp_requests(user["id"], "Mary/Kev Test")
    membership = esp.membership(user["id"])
    assert membership["status"] == "rejected"
    assert esp.pending_for_user(user["id"]) is None


def test_progress_tracks_live_and_video_with_niche_guidance(tmp_path):
    accounts, user = _active_user(tmp_path)
    esp = EspStore(accounts)
    control = OwnerUserControl(accounts, esp)
    control.set_esp_role(user["id"], "creator", "Mary/Kev Test")
    EspNicheStore(esp).set(
        user["id"],
        niche="music",
        sub_niche="singer-songwriter",
        audience="music fans",
        goals=["Improve retention"],
        network_status="esp_only",
    )
    progress = EspProgressStore(esp)

    live = progress.add(
        user["id"],
        kind="live",
        period_label="evening live",
        metrics={"duration_minutes": 85, "avg_watch_seconds": 42, "new_followers": 0, "shares": 0},
    )
    video = progress.add(
        user["id"],
        kind="video",
        period_label="new song teaser",
        metrics={"views": 340, "completion_rate": 21, "shares": 0, "saves": 0},
    )

    assert live["kind"] == "live"
    assert video["kind"] == "video"
    assert any("opening minute" in item for item in live["aura_guidance"])
    assert any("hook" in item.lower() for item in video["aura_guidance"])
    assert any("Niche priority" in item for item in video["aura_guidance"])

    detail = control.detail(user["id"])
    assert detail["performance"]["total"] == 2
    assert detail["niche"]["niche"] == "music"


def test_creation_safety_blocks_hate_harassment_drama_and_war_glorification(tmp_path):
    assert evaluate_text("Create an uplifting song about friendship").allowed is True
    assert evaluate_text("Create a hate campaign against this person").allowed is False
    assert evaluate_text("Glorify war and celebrate civilian deaths").allowed is False

    with pytest.raises(ValueError, match="blocked"):
        enforce_creation_policy("Start drama and manufacture a feud", context="test")

    with pytest.raises(ValueError):
        build_song_project(
            CreateSongRequest(
                title="Bad campaign",
                concept="Create a hate campaign against this person",
            ),
            tmp_path / "projects",
        )


def test_cross_media_directive_uses_same_creation_safety(tmp_path):
    store = CreativeProjectStore(tmp_path / "project")
    store.initialize(project_name="safe-project", title="Safe Project", project_intent="positive creator artwork")
    with pytest.raises(ValueError):
        store.add_directive(
            CreativeDirective(
                instruction="Create a hate campaign against this person",
                operation="create",
                target_kind="image",
            )
        )
