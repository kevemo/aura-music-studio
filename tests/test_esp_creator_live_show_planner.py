from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_creator_live_show_planner import (
    ChecklistUpdate,
    CreatorLiveShowStore,
    SegmentCreate,
    ShowPlanCreate,
    StatusUpdate,
    router,
)


def _approved_user(accounts: AccountStore, email: str):
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    return accounts.decide_membership(signup.approval_token, "approve", "Owner")


def _setup(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    creator = _approved_user(accounts, "creator@example.com")
    other = _approved_user(accounts, "other@example.com")
    store = CreatorLiveShowStore(db_path=accounts.db_path)
    return creator, other, store


def test_default_show_timeline_matches_requested_duration(tmp_path):
    creator, _other, store = _setup(tmp_path)
    plan = store.create(
        creator["id"],
        ShowPlanCreate(
            title="Friday Night Music Show",
            target_duration_minutes=120,
            opening_hook="Tonight I am performing live requests and original songs.",
            room_reset_every_minutes=20,
        ),
    )
    assert len(plan["segments"]) == 6
    assert plan["planned_total_minutes"] == 120
    assert plan["timeline"][0]["start_minute"] == 0
    assert plan["timeline"][-1]["end_minute"] == 120
    assert plan["room_reset_minutes"] == [20, 40, 60, 80, 100]
    assert plan["direct_tiktok_scheduling"] is False
    assert plan["automatic_live_control"] is False


def test_ready_status_requires_human_readiness_checks(tmp_path):
    creator, _other, store = _setup(tmp_path)
    plan = store.create(
        creator["id"],
        ShowPlanCreate(title="Creator LIVE", opening_hook="Stay for tonight's three-part challenge."),
    )
    assert plan["readiness"]["ready_to_mark_ready"] is False
    with pytest.raises(ValueError, match="required readiness checks"):
        store.set_status(creator["id"], plan["id"], StatusUpdate(status="ready"))

    for item in plan["checklist"]:
        if item["required"]:
            plan = store.set_checklist(
                creator["id"], plan["id"], item["id"], ChecklistUpdate(done=True, note="Checked by creator"),
            )
    assert plan["readiness"]["required_percent"] == 100.0
    assert plan["readiness"]["ready_to_mark_ready"] is True
    ready = store.set_status(creator["id"], plan["id"], StatusUpdate(status="ready"))
    assert ready["status"] == "ready"


def test_show_plans_are_private_to_the_creator_account(tmp_path):
    creator, other, store = _setup(tmp_path)
    plan = store.create(creator["id"], ShowPlanCreate(title="Private Creator Show"))
    with pytest.raises(KeyError):
        store.get(other["id"], plan["id"])
    assert store.list_for_user(other["id"]) == []


def test_adding_segment_returns_plan_to_draft_for_review(tmp_path):
    creator, _other, store = _setup(tmp_path)
    plan = store.create(
        creator["id"],
        ShowPlanCreate(title="Ready Show", opening_hook="Welcome to the show."),
    )
    for item in plan["checklist"]:
        if item["required"]:
            plan = store.set_checklist(creator["id"], plan["id"], item["id"], ChecklistUpdate(done=True))
    plan = store.set_status(creator["id"], plan["id"], StatusUpdate(status="ready"))
    assert plan["status"] == "ready"
    updated = store.add_segment(
        creator["id"], plan["id"],
        SegmentCreate(
            name="Audience encore",
            segment_type="feature",
            planned_minutes=10,
            script_notes="Use only if pacing and room energy support it.",
        ),
    )
    assert updated["status"] == "draft"
    assert updated["segments"][-1]["name"] == "Audience encore"
    assert updated["planned_total_minutes"] == 130


def test_show_planner_router_exposes_private_creator_routes():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/api/show-planner" in paths
    assert "/command-center/api/show-planner/plans" in paths
    assert "/command-center/api/show-planner/plans/{plan_id}" in paths
    assert "/command-center/api/show-planner/plans/{plan_id}/segments" in paths
    assert "/command-center/api/show-planner/plans/{plan_id}/checklist/{item_id}" in paths
    assert "/command-center/api/show-planner/plans/{plan_id}/status" in paths
    assert "/command-center/show-planner" in paths
    assert "/command-center/show-planner/{plan_id}" in paths
