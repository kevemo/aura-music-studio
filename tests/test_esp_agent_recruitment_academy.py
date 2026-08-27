from __future__ import annotations

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_agent_recruitment_academy import (
    CURRICULUM,
    SCENARIOS,
    RecruitmentAcademyStore,
    academy_for,
    router,
)


def _user(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup("agent@example.com", "Agent", "a-very-secure-test-password", "free")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    return accounts, user


def test_curriculum_covers_recruitment_content_live_pipeline_and_creator_success():
    titles = " ".join(row["title"] for row in CURRICULUM).lower()
    categories = {row["category"] for row in CURRICULUM}
    assert len(CURRICULUM) >= 12
    assert "video strategy" in titles
    assert "recruitment live" in titles
    assert "creator discovery" in titles
    assert "follow-up" in titles
    assert {"Foundations", "Discovery", "Content", "LIVE Recruitment", "Pipeline", "Creator Success"}.issubset(categories)


def test_academy_encodes_non_poaching_and_owner_activation_boundaries(tmp_path, monkeypatch):
    accounts, user = _user(tmp_path)
    store = RecruitmentAcademyStore(accounts.db_path)
    monkeypatch.setattr("aura_music_studio.esp_agent_recruitment_academy.academy", store)
    data = academy_for(user["id"])
    assert data["agent_only"] is True
    assert data["ethical_recruitment_required"] is True
    assert data["poaching_allowed"] is False
    assert data["earnings_guarantees_allowed"] is False
    assert data["owner_activation_required"] is True


def test_module_progress_and_evidence_are_persisted(tmp_path):
    accounts, user = _user(tmp_path)
    store = RecruitmentAcademyStore(accounts.db_path)
    state = store.set_module(
        user["id"], CURRICULUM[0]["id"], completed=True,
        evidence_note="Completed the non-poaching validation practice.",
    )
    assert state["completed"] is True
    assert "non-poaching" in state["evidence_note"]
    summary = store.summary(user["id"])
    assert summary["modules_completed"] == 1
    assert summary["modules_total"] == len(CURRICULUM)
    assert summary["certified"] is False


def test_scenario_attempt_scores_safe_answer_and_does_not_reveal_key_in_catalog(tmp_path, monkeypatch):
    accounts, user = _user(tmp_path)
    store = RecruitmentAcademyStore(accounts.db_path)
    scenario = SCENARIOS[0]
    good = store.attempt(user["id"], scenario["id"], scenario["correct"])
    assert good["correct"] is True
    bad_index = next(index for index in range(len(scenario["options"])) if index != scenario["correct"])
    bad = store.attempt(user["id"], scenario["id"], bad_index)
    assert bad["correct"] is False
    monkeypatch.setattr("aura_music_studio.esp_agent_recruitment_academy.academy", store)
    public = academy_for(user["id"])
    assert all("correct" not in row and "explanation" not in row for row in public["scenarios"])
    assert public["summary"]["scenario_attempts"] == 2
    assert public["summary"]["scenario_correct"] == 1
    assert public["summary"]["scenario_percent"] == 50.0


def test_certification_requires_all_modules_and_perfect_scenario_attempts(tmp_path):
    accounts, user = _user(tmp_path)
    store = RecruitmentAcademyStore(accounts.db_path)
    for module in CURRICULUM:
        store.set_module(user["id"], module["id"], completed=True, evidence_note="Practice completed")
    for scenario in SCENARIOS:
        store.attempt(user["id"], scenario["id"], scenario["correct"])
    summary = store.summary(user["id"])
    assert summary["modules_completed"] == len(CURRICULUM)
    assert summary["scenario_attempts"] == len(SCENARIOS)
    assert summary["scenario_correct"] == len(SCENARIOS)
    assert summary["certified"] is True


def test_recruitment_academy_routes_are_private_command_center_paths():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/agent/recruitment-academy" in paths
    assert "/command-center/api/agent/recruitment-academy" in paths
    assert "/command-center/api/agent/recruitment-academy/modules/{module_id}" in paths
    assert "/command-center/api/agent/recruitment-academy/scenarios/{scenario_id}" in paths
    assert all(path is None or path.startswith("/command-center/") for path in paths)
