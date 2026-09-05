from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_support_center import SupportCaseStore
import aura_music_studio.esp_support_conversations as support_conversations


def _user(accounts: AccountStore, email: str, name: str) -> dict:
    signup = accounts.signup(email, name, "a-very-secure-test-password", "free")
    return accounts.decide_membership(signup.approval_token, "approve", "ESP Test Owner")


def test_member_cannot_read_owner_internal_support_note(tmp_path, monkeypatch):
    accounts = AccountStore(tmp_path / "support-conversations.sqlite3")
    esp = EspStore(accounts)
    member = _user(accounts, "member@example.com", "Member")
    owner = _user(accounts, "owner@example.com", "Owner")
    support = SupportCaseStore(esp)
    monkeypatch.setattr(support_conversations, "support", support)
    store = support_conversations.SupportConversationStore()

    case = support.create_case(
        member["id"],
        category="technical",
        severity="normal",
        subject="Creator dashboard issue",
        description="The creator dashboard is showing the wrong workflow state.",
    )
    visible = store.add_message(
        case["id"],
        author_user_id=member["id"],
        owner=False,
        visibility="user_visible",
        body="Here is the extra detail requested by support.",
    )
    internal = store.add_message(
        case["id"],
        author_user_id=owner["id"],
        owner=True,
        visibility="internal",
        body="Owner-only diagnostic note. Never expose this to the member.",
    )

    member_rows = store.list_messages(case["id"], user_id=member["id"], owner=False)
    owner_rows = store.list_messages(case["id"], user_id=owner["id"], owner=True)

    assert [row["id"] for row in member_rows] == [visible["id"]]
    assert {row["id"] for row in owner_rows} == {visible["id"], internal["id"]}
    assert all(row["visibility"] == "user_visible" for row in member_rows)


def test_member_cannot_create_internal_support_note(tmp_path, monkeypatch):
    accounts = AccountStore(tmp_path / "support-conversations.sqlite3")
    esp = EspStore(accounts)
    member = _user(accounts, "member2@example.com", "Member Two")
    support = SupportCaseStore(esp)
    monkeypatch.setattr(support_conversations, "support", support)
    store = support_conversations.SupportConversationStore()
    case = support.create_case(
        member["id"],
        category="other",
        severity="low",
        subject="Question for support",
        description="A private support question for the test suite.",
    )

    with pytest.raises(PermissionError, match="Members cannot create internal"):
        store.add_message(
            case["id"],
            author_user_id=member["id"],
            owner=False,
            visibility="internal",
            body="This must be rejected.",
        )


def test_support_conversation_routes_are_exposed():
    paths = {getattr(route, "path", None) for route in support_conversations.router.routes}
    assert "/command-center/api/support/cases/{case_id}/messages" in paths
    assert "/command-center/support/cases/{case_id}" in paths
