from __future__ import annotations

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_chat_store import AuraChatStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.owner_identity import decode_persona_cookie, encode_persona_cookie
from aura_music_studio.owner_user_control import OwnerUserControl


def _active(accounts: AccountStore, email: str, name: str = "Test User") -> dict:
    signup = accounts.signup(email, name, "a-very-secure-test-password", "free")
    return accounts.decide_membership(signup.approval_token, "approve", "Test Owner")


def test_owner_persona_cookie_is_signed_and_tamper_resistant(monkeypatch):
    monkeypatch.setenv("LSS_ADMIN_KEY", "test-owner-key-that-is-not-a-real-secret")
    mary = encode_persona_cookie("mary")
    kev = encode_persona_cookie("kev")

    assert decode_persona_cookie(mary) == "mary"
    assert decode_persona_cookie(kev) == "kev"
    assert decode_persona_cookie(mary.replace("mary.", "kev.", 1)) is None
    assert decode_persona_cookie(mary[:-1] + ("0" if mary[-1] != "0" else "1")) is None


def test_owner_changes_create_audit_records_and_private_profile(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user = _active(accounts, "creator@example.com")
    esp = EspStore(accounts)
    control = OwnerUserControl(accounts, esp)

    control.set_esp_role(user["id"], "creator", "Mary Test")
    control.set_plan(user["id"], "pro", "Kev Test")
    detail = control.update_owner_profile(
        user["id"],
        sub_level="Senior Creator",
        mentor="Owner Team",
        categories=["music", "live growth"],
        owner_notes="Review retention after the next three LIVE sessions.",
        actor="Mary Test",
    )

    assert detail["owner_profile"]["sub_level"] == "Senior Creator"
    assert detail["owner_profile"]["categories"] == ["music", "live growth"]
    actions = [row["action"] for row in control.audit_log(user["id"], 20)]
    assert "esp_role_changed" in actions
    assert "subscription_plan_changed" in actions
    assert "owner_user_profile_updated" in actions


def test_aura_intelligence_threads_are_isolated_per_member(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    first = _active(accounts, "one@example.com", "One")
    second = _active(accounts, "two@example.com", "Two")
    chat = AuraChatStore(accounts)

    thread = chat.create_thread(first["id"], "Private project thinking")
    chat.add_message(first["id"], thread["id"], "user", "Help me plan a release.")
    chat.add_message(first["id"], thread["id"], "assistant", "Start with the release goal.")

    assert len(chat.messages(first["id"], thread["id"])) == 2
    assert chat.thread(second["id"], thread["id"]) is None
    assert chat.list_threads(second["id"]) == []


def test_deleting_a_chat_thread_removes_only_that_users_thread(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    first = _active(accounts, "one@example.com", "One")
    second = _active(accounts, "two@example.com", "Two")
    chat = AuraChatStore(accounts)
    first_thread = chat.create_thread(first["id"], "First")
    second_thread = chat.create_thread(second["id"], "Second")

    chat.delete_thread(first["id"], first_thread["id"])
    assert chat.thread(first["id"], first_thread["id"]) is None
    assert chat.thread(second["id"], second_thread["id"]) is not None
