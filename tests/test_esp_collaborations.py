from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_collaborations import (
    CollaborationStore,
    EventStatusRequest,
    ProfileRequest,
    ProposalRequest,
    router,
)
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_niche import EspNicheStore


def _creator(accounts: AccountStore, esp: EspStore, email: str):
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    _request, token = esp.request_access(user["id"], "creator", email.split("@")[0], "UK+", "test")
    esp.decide(token, "approve", "creator", "Owner")
    return user


def _setup(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    EspNicheStore(esp)
    first = _creator(accounts, esp, "first-collab@example.com")
    second = _creator(accounts, esp, "second-collab@example.com")
    third = _creator(accounts, esp, "third-collab@example.com")
    return accounts, esp, CollaborationStore(accounts.db_path), first, second, third


def test_target_must_opt_in_before_creator_can_propose(tmp_path):
    _accounts, _esp, store, first, second, _third = _setup(tmp_path)
    with pytest.raises(PermissionError, match="not currently opted"):
        store.propose(
            first["id"],
            ProposalRequest(invited_creator_user_id=second["id"], kind="battle", title="Friendly ESP Battle"),
        )


def test_opted_in_creator_can_receive_and_accept_proposal(tmp_path):
    _accounts, _esp, store, first, second, _third = _setup(tmp_path)
    store.set_profile(
        second["id"],
        ProfileRequest(opt_in=True, display_title="Music collabs", collaboration_types=["battle", "music"]),
    )
    event = store.propose(
        first["id"],
        ProposalRequest(
            invited_creator_user_id=second["id"],
            kind="music",
            title="Acoustic collaboration",
            starts_at="2026-09-01T19:00:00+00:00",
        ),
    )
    assert event["status"] == "proposed"
    accepted = store.set_status(event["id"], second["id"], "accepted", "Sounds good")
    assert accepted["status"] == "accepted"


def test_proposing_creator_cannot_accept_on_behalf_of_invited_creator(tmp_path):
    _accounts, _esp, store, first, second, _third = _setup(tmp_path)
    store.set_profile(second["id"], ProfileRequest(opt_in=True, collaboration_types=["cohost"]))
    event = store.propose(first["id"], ProposalRequest(invited_creator_user_id=second["id"], kind="cohost", title="Co-host LIVE"))
    with pytest.raises(PermissionError, match="invited creator"):
        store.set_status(event["id"], first["id"], "accepted")


def test_unrelated_creator_cannot_read_private_event(tmp_path):
    _accounts, _esp, store, first, second, third = _setup(tmp_path)
    store.set_profile(second["id"], ProfileRequest(opt_in=True, collaboration_types=["content"]))
    event = store.propose(first["id"], ProposalRequest(invited_creator_user_id=second["id"], kind="content", title="Content collaboration"))
    with pytest.raises(PermissionError, match="not assigned"):
        store.get_event(event["id"], third["id"])


def test_no_show_requires_owner_review(tmp_path):
    _accounts, _esp, store, first, second, _third = _setup(tmp_path)
    store.set_profile(second["id"], ProfileRequest(opt_in=True, collaboration_types=["battle"]))
    event = store.propose(first["id"], ProposalRequest(invited_creator_user_id=second["id"], kind="battle", title="Battle"))
    store.set_status(event["id"], second["id"], "accepted")
    with pytest.raises(PermissionError, match="owner review"):
        store.set_status(event["id"], first["id"], "no_show", "Creator did not attend")
    owner_recorded = store.set_status(event["id"], first["id"], "no_show", "Owner reviewed attendance evidence", owner=True)
    assert owner_recorded["status"] == "no_show"
    assert owner_recorded["completed_at"]


def test_opted_in_directory_does_not_expose_private_availability_note_to_other_creator(tmp_path):
    _accounts, _esp, store, first, second, _third = _setup(tmp_path)
    store.set_profile(
        second["id"],
        ProfileRequest(
            opt_in=True,
            display_title="Open to music",
            collaboration_types=["music"],
            availability_note="Private: weekday evenings after work",
        ),
    )
    rows = store.profiles(first["id"])
    target = next(row for row in rows if row["user_id"] == second["id"])
    assert target["display_title"] == "Open to music"
    assert "availability_note" not in target


def test_collaboration_routes_are_private_esp_only():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/collaborations" in paths
    assert "/command-center/api/collaborations/profiles" in paths
    assert "/command-center/api/collaborations/profile" in paths
    assert "/command-center/api/collaborations/events" in paths
    assert "/collaborations" not in paths
