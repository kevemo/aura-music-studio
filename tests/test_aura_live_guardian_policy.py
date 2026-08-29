import pytest

from aura_music_studio.aura_live_guardian_policy import AuraLiveGuardianPolicyStore


def test_creator_policy_deduplicates_phrases_and_matches_case_insensitively(tmp_path):
    store = AuraLiveGuardianPolicyStore(tmp_path / "guardian.sqlite3")
    policy = store.save(user_id="creator-1", blocked_phrases=["no drama", "  NO   DRAMA ", "spam link"], language_tolerance="balanced", spam_sensitivity="high", actor="member:creator-1")
    assert policy.blocked_phrases == ("no drama", "spam link")
    assert store.phrase_matches("creator-1", "Please stop the NO DRAMA comments") == ("no drama",)


def test_high_risk_categories_cannot_be_disabled(tmp_path):
    store = AuraLiveGuardianPolicyStore(tmp_path / "guardian.sqlite3")
    policy = store.save(user_id="creator-1", blocked_phrases=[], enabled_categories={"spam"}, actor="member:creator-1")
    assert {"threat", "doxxing", "grooming_concern"}.issubset(policy.enabled_categories)
    assert "spam" in policy.enabled_categories


def test_explicit_empty_optional_categories_keep_only_mandatory_protections(tmp_path):
    store = AuraLiveGuardianPolicyStore(tmp_path / "guardian.sqlite3")
    policy = store.save(user_id="creator-1", blocked_phrases=[], enabled_categories=set(), actor="member:creator-1")
    assert policy.enabled_categories == frozenset({"threat", "doxxing", "grooming_concern"})


def test_unknown_category_and_invalid_sensitivity_fail_closed(tmp_path):
    store = AuraLiveGuardianPolicyStore(tmp_path / "guardian.sqlite3")
    with pytest.raises(ValueError, match="unknown moderation category"):
        store.save(user_id="creator-1", blocked_phrases=[], enabled_categories={"made_up_category"}, actor="member:creator-1")
    with pytest.raises(ValueError, match="spam sensitivity"):
        store.save(user_id="creator-1", blocked_phrases=[], spam_sensitivity="extreme", actor="member:creator-1")  # type: ignore[arg-type]


def test_phrase_limits_are_bounded(tmp_path):
    store = AuraLiveGuardianPolicyStore(tmp_path / "guardian.sqlite3")
    with pytest.raises(ValueError, match="too long"):
        store.save(user_id="creator-1", blocked_phrases=["x" * 121], actor="member:creator-1")
    with pytest.raises(ValueError, match="too many"):
        store.save(user_id="creator-1", blocked_phrases=[f"phrase {index}" for index in range(101)], actor="member:creator-1")
