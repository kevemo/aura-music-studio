from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_creator_niche_discovery import (
    CandidateCreate,
    CreatorNicheDiscoveryStore,
    ExperimentUpdate,
    SCORE_WEIGHTS,
    router,
)
from aura_music_studio.esp_niche import EspNicheStore


def _active_creator(accounts: AccountStore, esp: EspStore, email: str):
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    _request, token = esp.request_access(user["id"], "creator", email.split("@")[0], "UK+", "test")
    esp.decide(token, "approve", "creator", "Owner")
    return user


def _setup(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    niches = EspNicheStore(esp)
    creator = _active_creator(accounts, esp, "creator@example.com")
    other = _active_creator(accounts, esp, "other@example.com")
    store = CreatorNicheDiscoveryStore(db_path=esp.db_path, niche_store=niches)
    return creator, other, niches, store


def _candidate(**overrides):
    data = {
        "niche_key": "music",
        "sub_niche": "acoustic originals",
        "audience": "Adults who enjoy live acoustic songwriting and requests",
        "promise": "A warm interactive acoustic show with originals and audience-led moments",
        "interest": 5,
        "expertise": 5,
        "content_depth": 5,
        "audience_clarity": 4,
        "live_fit": 5,
        "repeatability": 5,
        "distinctiveness": 4,
    }
    data.update(overrides)
    return CandidateCreate(**data)


def test_transparent_score_uses_documented_weights_only():
    result = CreatorNicheDiscoveryStore.score({key: 5 for key in SCORE_WEIGHTS})
    assert sum(SCORE_WEIGHTS.values()) == 100
    assert result["score"] == 100.0
    assert result["band"] == "strong_test_candidate"
    assert result["method"] == "transparent_weighted_self_assessment"
    assert all(component["rating"] == 5 for component in result["components"].values())


def test_candidate_generates_experiments_without_using_fake_trend_data(tmp_path):
    creator, _other, _niches, store = _setup(tmp_path)
    candidate = store.create_candidate(creator["id"], _candidate())
    assert candidate["scorecard"]["score"] >= 80
    assert candidate["trend_data_used_for_score"] is False
    assert candidate["automatic_niche_change"] is False
    assert [row["day_number"] for row in candidate["experiments"]] == [1, 2, 4, 7]
    assert {row["channel"] for row in candidate["experiments"]} == {"profile", "shortform", "live", "community"}
    assert candidate["experiment_progress"]["percent"] == 0.0


def test_experiment_completion_requires_human_result_note_and_cleans_metrics(tmp_path):
    creator, _other, _niches, store = _setup(tmp_path)
    candidate = store.create_candidate(creator["id"], _candidate())
    experiment_id = candidate["experiments"][0]["id"]
    with pytest.raises(ValueError, match="result note"):
        store.update_experiment(
            creator["id"], experiment_id,
            ExperimentUpdate(status="completed", result_note="", metrics={"views": 100}),
        )
    updated = store.update_experiment(
        creator["id"], experiment_id,
        ExperimentUpdate(
            status="completed",
            result_note="The audience promise was understood and produced useful questions.",
            metrics={"views": 1250, "shares": 12, "negative": -5, "boolean": True},
        ),
    )
    row = next(item for item in updated["experiments"] if item["id"] == experiment_id)
    assert row["status"] == "completed"
    assert row["metrics"] == {"shares": 12, "views": 1250}
    assert updated["experiment_progress"]["completed"] == 1


def test_selecting_candidate_preserves_network_affiliation_and_existing_goals(tmp_path):
    creator, _other, niches, store = _setup(tmp_path)
    niches.set(
        creator["id"], niche="gaming", sub_niche="co-op", audience="Gaming community",
        goals=["Keep existing creator goal"], network_status="esp_only",
    )
    candidate = store.create_candidate(creator["id"], _candidate())
    result = store.activate_candidate(creator["id"], candidate["id"])
    profile = result["active_profile"]
    assert result["explicit_creator_selection"] is True
    assert profile["niche"] == "music"
    assert profile["network_status"] == "esp_only"
    assert profile["sub_niche"] == "acoustic originals"
    assert candidate["promise"] in profile["goals"]
    assert "Keep existing creator goal" in profile["goals"]
    assert result["candidate"]["status"] == "selected"


def test_niche_candidates_are_private_to_creator(tmp_path):
    creator, other, _niches, store = _setup(tmp_path)
    candidate = store.create_candidate(creator["id"], _candidate())
    with pytest.raises(KeyError):
        store.get_candidate(other["id"], candidate["id"])
    assert store.list_candidates(other["id"]) == []


def test_niche_lab_router_exposes_private_creator_routes():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/api/niche-lab" in paths
    assert "/command-center/api/niche-lab/candidates" in paths
    assert "/command-center/api/niche-lab/candidates/{candidate_id}/experiments" in paths
    assert "/command-center/api/niche-lab/experiments/{experiment_id}" in paths
    assert "/command-center/api/niche-lab/candidates/{candidate_id}/activate" in paths
    assert "/command-center/niche-lab" in paths
