from __future__ import annotations

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_niche import EspNicheStore
from aura_music_studio.esp_owner_focus import OwnerFocusService, _change, router
from aura_music_studio.esp_progress import EspProgressStore
from aura_music_studio.owner_user_control import OwnerUserControl


def _creator(accounts: AccountStore, esp: EspStore, email: str):
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    _request, token = esp.request_access(user["id"], "creator", email.split("@")[0], "UK+", "test")
    esp.decide(token, "approve", "creator", "Owner")
    return user


def test_percent_change_is_bounded_and_handles_zero_baseline():
    assert _change(0, 0) == 0
    assert _change(5, 0) == 100
    assert _change(120, 100) == 20
    assert _change(0, 100) == -100
    assert _change(1000, 1) == 200


def test_creator_momentum_uses_two_comparable_submissions(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    EspNicheStore(esp)
    creator = _creator(accounts, esp, "momentum@example.com")
    progress = EspProgressStore(esp)
    progress.add(
        creator["id"],
        kind="live",
        period_label="Previous",
        metrics={"diamonds": 100, "duration_minutes": 120, "avg_watch_seconds": 60, "new_followers": 10, "shares": 10},
    )
    progress.add(
        creator["id"],
        kind="live",
        period_label="Current",
        metrics={"diamonds": 130, "duration_minutes": 150, "avg_watch_seconds": 75, "new_followers": 13, "shares": 13},
    )
    control = OwnerUserControl(accounts, esp)
    service = OwnerFocusService(control)

    row = next(item for item in service.creator_momentum() if item["user_id"] == creator["id"])
    assert row["state"] == "rising"
    assert row["momentum_score"] >= 10
    assert len(row["comparisons"]) == 5
    assert all(item["kind"] == "live" for item in row["comparisons"])


def test_creator_with_only_one_submission_is_not_given_fake_trend(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    EspNicheStore(esp)
    creator = _creator(accounts, esp, "one-period@example.com")
    EspProgressStore(esp).add(creator["id"], kind="video", period_label="Only", metrics={"views": 5000})
    service = OwnerFocusService(OwnerUserControl(accounts, esp))

    row = next(item for item in service.creator_momentum() if item["user_id"] == creator["id"])
    assert row["state"] == "insufficient_data"
    assert row["momentum_score"] is None
    assert row["comparisons"] == []


def test_owner_focus_routes_are_owner_namespace_only():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/owner/focus" in paths
    assert "/owner/api/focus" in paths
    assert "/focus" not in paths
    assert "/command-center/focus" not in paths
