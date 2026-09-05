from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio.aura_live_guardian_policy import AuraLiveGuardianPolicyStore
from aura_music_studio.aura_live_guardian_policy_ui import guardian_policy_page, router, save_guardian_policy


class RequestStub:
    def __init__(self, member=None):
        self.state = SimpleNamespace(member=member)


def _member():
    return SimpleNamespace(user_id="creator-policy-1", display_name="Creator")


def test_policy_routes_are_private_creator_controls():
    paths = [route.path for route in router.routes]
    assert paths.count("/live-guardian/policy") == 2
    assert not any("source" in path for path in paths)


def test_policy_page_requires_member(monkeypatch, tmp_path):
    monkeypatch.setenv("AURA_LIVE_MODERATOR_DB", str(tmp_path / "guardian.sqlite3"))
    with pytest.raises(HTTPException) as exc:
        guardian_policy_page(RequestStub())
    assert exc.value.status_code == 401


def test_policy_page_locks_critical_categories(monkeypatch, tmp_path):
    monkeypatch.setenv("AURA_LIVE_MODERATOR_DB", str(tmp_path / "guardian.sqlite3"))
    response = guardian_policy_page(RequestStub(_member()))
    body = response.body.decode("utf-8")
    assert "Threats" in body
    assert "Personal-information exposure / doxxing" in body
    assert "Grooming concern" in body
    assert body.count("Always protected") == 3
    assert "do not create TikTok API authority" in body
    assert response.headers["cache-control"] == "private, no-store"


def test_creator_policy_save_persists_preferences_and_forces_critical_protection(monkeypatch, tmp_path):
    db = tmp_path / "guardian.sqlite3"
    monkeypatch.setenv("AURA_LIVE_MODERATOR_DB", str(db))
    response = save_guardian_policy(
        RequestStub(_member()), blocked_phrases="no drama\nspam me",
        language_tolerance="relaxed", spam_sensitivity="high", category=["spam", "harassment"],
    )
    assert response.status_code == 303
    policy = AuraLiveGuardianPolicyStore(db).get(_member().user_id)
    assert policy is not None
    assert policy.blocked_phrases == ("no drama", "spam me")
    assert policy.language_tolerance == "relaxed"
    assert policy.spam_sensitivity == "high"
    assert {"spam", "harassment", "threat", "doxxing", "grooming_concern"}.issubset(policy.enabled_categories)
