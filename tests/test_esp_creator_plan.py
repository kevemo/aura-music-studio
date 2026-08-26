from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_creator_plan import CreatorPlanStore, router as creator_plan_router


def _user(tmp_path, email: str):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup(email, "Creator Plan Test", "a-very-secure-test-password", "free")
    user = accounts.decide_membership(signup.approval_token, "approve", "ESP Test Owner")
    return accounts, user


def test_creator_plan_builds_seven_day_activation_path(tmp_path):
    accounts, user = _user(tmp_path, "plan@example.com")
    store = CreatorPlanStore(accounts.db_path)

    plan = store.ensure(user["id"])

    assert plan["phase"] == "activate"
    assert plan["live_days_target"] == 8
    assert plan["live_hours_target"] == 20
    assert plan["videos_per_week"] == 15
    assert len(plan["actions"]) == 7
    assert [row["day_number"] for row in plan["actions"]] == list(range(1, 8))
    assert plan["completion"] == {"done": 0, "total": 7, "percent": 0.0}


def test_creator_plan_requires_evidence_for_evidence_actions(tmp_path):
    accounts, user = _user(tmp_path, "evidence@example.com")
    store = CreatorPlanStore(accounts.db_path)
    plan = store.ensure(user["id"])
    evidence_action = next(row for row in plan["actions"] if row["evidence_required"])

    with pytest.raises(ValueError, match="evidence"):
        store.set_action(user["id"], evidence_action["id"], done=True, evidence_note="")

    updated = store.set_action(
        user["id"],
        evidence_action["id"],
        done=True,
        evidence_note="Completed the planned action and reviewed the result.",
    )
    completed = next(row for row in updated["actions"] if row["id"] == evidence_action["id"])
    assert completed["status"] == "done"
    assert completed["evidence_note"]
    assert updated["completion"]["done"] == 1


def test_creator_plan_actions_are_isolated_by_user(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    first_signup = accounts.signup("one@example.com", "One", "a-very-secure-test-password", "free")
    second_signup = accounts.signup("two@example.com", "Two", "a-very-secure-test-password", "free")
    first = accounts.decide_membership(first_signup.approval_token, "approve", "ESP Test Owner")
    second = accounts.decide_membership(second_signup.approval_token, "approve", "ESP Test Owner")
    store = CreatorPlanStore(accounts.db_path)

    first_plan = store.ensure(first["id"])
    second_plan = store.ensure(second["id"])
    first_action = first_plan["actions"][0]
    store.set_action(first["id"], first_action["id"], done=True)

    assert store.get(first["id"])["completion"]["done"] == 1
    assert store.get(second["id"])["completion"]["done"] == 0
    with pytest.raises(KeyError):
        store.set_action(second["id"], first_action["id"], done=True)


def test_creator_plan_router_exposes_private_page_and_api():
    paths = {getattr(route, "path", None) for route in creator_plan_router.routes}
    assert "/command-center/my-plan" in paths
    assert "/command-center/api/my-plan" in paths
    assert "/command-center/api/my-plan/actions/{action_id}/complete" in paths
    assert "/command-center/api/my-plan/actions/{action_id}/reopen" in paths
