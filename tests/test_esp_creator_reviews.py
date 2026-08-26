from __future__ import annotations

import sqlite3

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_creator_plan import CreatorPlanStore
from aura_music_studio.esp_creator_reviews import CreatorReviewStore, router
from aura_music_studio.esp_niche import EspNicheStore


def _setup(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup("review@example.com", "Review Creator", "a-very-secure-test-password", "free")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    esp = EspStore(accounts)
    EspNicheStore(esp).set(
        user["id"],
        niche="music",
        sub_niche="singer-songwriter",
        audience="music fans",
        goals=["Improve retention"],
        network_status="esp_only",
    )
    plans = CreatorPlanStore(accounts.db_path)
    return accounts, esp, plans, user


def test_review_snapshot_is_immutable_after_plan_changes(tmp_path):
    accounts, esp, plans, user = _setup(tmp_path)
    store = CreatorReviewStore(esp, plans)
    review = store.create(
        user["id"],
        review_day=30,
        wins="More consistent",
        next_focus="Retention",
        actor=user["id"],
    )
    original = review["snapshot"]["plan"]["monthly_objective"]
    with plans._connect() as con:
        con.execute(
            "UPDATE esp_creator_plans SET monthly_objective='Future objective' WHERE user_id=?",
            (user["id"],),
        )
    stored = store.get(review["id"], user["id"])
    assert stored["snapshot"]["plan"]["monthly_objective"] == original
    assert stored["snapshot_integrity"] is True


def test_review_comparison_keeps_milestone_timeline_and_remaining_points(tmp_path):
    accounts, esp, plans, user = _setup(tmp_path)
    store = CreatorReviewStore(esp, plans)
    store.create(user["id"], review_day=30, next_focus="Consistency", actor=user["id"])
    store.create(user["id"], review_day=60, next_focus="Retention", actor=user["id"])
    comparison = store.comparison(user["id"])
    assert [row["review_day"] for row in comparison["timeline"]] == [30, 60]
    assert comparison["captured_milestones"] == [30, 60]
    assert comparison["remaining_milestones"] == [90]
    assert comparison["historical_snapshots_immutable"] is True
    assert comparison["integrity_verified"] is True
    assert comparison["timeline"][1]["delta"] is not None


def test_review_rejects_non_milestone_day(tmp_path):
    accounts, esp, plans, user = _setup(tmp_path)
    store = CreatorReviewStore(esp, plans)
    with pytest.raises(ValueError, match="30, 60 or 90"):
        store.create(user["id"], review_day=45, actor=user["id"])


def test_each_review_milestone_is_append_only_and_unique(tmp_path):
    accounts, esp, plans, user = _setup(tmp_path)
    store = CreatorReviewStore(esp, plans)
    store.create(user["id"], review_day=30, actor=user["id"])
    with pytest.raises(FileExistsError, match="already been captured"):
        store.create(user["id"], review_day=30, actor=user["id"])


def test_snapshot_integrity_detects_manual_database_tampering(tmp_path):
    accounts, esp, plans, user = _setup(tmp_path)
    store = CreatorReviewStore(esp, plans)
    review = store.create(user["id"], review_day=30, actor=user["id"])
    with sqlite3.connect(store.db_path) as con:
        con.execute(
            "UPDATE esp_creator_reviews SET snapshot_json='{}' WHERE id=?",
            (review["id"],),
        )
    assert store.get(review["id"], user["id"])["snapshot_integrity"] is False


def test_review_routes_exist_without_mutation_routes():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/api/my-plan/reviews" in paths
    assert "/command-center/my-plan/reviews" in paths
    methods = {
        (getattr(route, "path", None), tuple(sorted(getattr(route, "methods", set()) or set())))
        for route in router.routes
    }
    assert not any(path and path.endswith("/{review_id}") for path, _ in methods)
