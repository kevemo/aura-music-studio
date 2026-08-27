from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_agent_core import AURA_CORE_SYSTEM
from aura_music_studio.aura_chat_store import AuraChatStore
from aura_music_studio.aura_context_extensions import _inject_messages, register_context_provider, unregister_context_provider
from aura_music_studio.aura_profiles import AuraProfileStore
from aura_music_studio.brand_migration import rebrand_text


def _user(accounts: AccountStore, email: str, name: str) -> str:
    return accounts.signup(email, name, "very-long-test-password", "free").user_id


def test_profiles_are_private_and_thread_scoped(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_a = _user(accounts, "a@example.com", "Member A")
    user_b = _user(accounts, "b@example.com", "Member B")
    chat = AuraChatStore(accounts)
    profiles = AuraProfileStore(chat)
    thread_a = chat.create_thread(user_a)
    thread_b = chat.create_thread(user_b)

    profile = profiles.create(
        user_a,
        name="Studio Producer",
        description="Music production specialist",
        instructions="Prioritise arrangement, mix translation and practical production decisions.",
        default_mode="deep",
    )

    assert len(profiles.list(user_a)) == 1
    assert profiles.list(user_b) == []
    assert profiles.get(user_b, profile["id"]) is None
    assert profiles.bind(user_a, thread_a["id"], profile["id"])["id"] == profile["id"]
    assert profiles.for_thread(user_a, thread_a["id"])["name"] == "Studio Producer"

    with pytest.raises(KeyError):
        profiles.bind(user_b, thread_b["id"], profile["id"])

    assert profiles.delete(user_a, profile["id"]) is True
    assert profiles.for_thread(user_a, thread_a["id"]) is None


def test_profile_validation_rejects_invalid_mode(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _user(accounts, "mode@example.com", "Mode User")
    profiles = AuraProfileStore(AuraChatStore(accounts))

    with pytest.raises(ValueError, match="fast, auto, deep or creative"):
        profiles.create(
            user_id,
            name="Unsafe mode",
            description="",
            instructions="Be concise.",
            default_mode="unlimited",
        )


def test_context_extension_is_appended_after_rebranded_aura_core_and_not_tool_prompts():
    def provider(user_id: str, thread_id: str):
        return f"PRIVATE PROFILE FOR {user_id}/{thread_id}: prefer concise music-engineering language."

    register_context_provider(provider)
    try:
        messages = [
            {"role": "system", "content": AURA_CORE_SYSTEM},
            {"role": "user", "content": "Help with my mix"},
        ]
        injected = _inject_messages(messages, "user-a", "thread-a")
        current_core = rebrand_text(AURA_CORE_SYSTEM)
        assert injected[0]["content"].startswith(current_core)
        assert "Pulsar-Frequency House" not in injected[0]["content"]
        assert "PRIVATE PROFILE FOR user-a/thread-a" in injected[0]["content"]
        assert injected[0]["content"].index("PRIVATE PROFILE") > len(current_core) - 1
        assert messages[0]["content"] == AURA_CORE_SYSTEM

        tool_prompt = [{"role": "system", "content": "You are Aura's private tool router."}]
        untouched = _inject_messages(tool_prompt, "user-a", "thread-a")
        assert untouched == tool_prompt
    finally:
        unregister_context_provider(provider)


def test_context_provider_character_budget_is_bounded():
    from aura_music_studio.aura_context_extensions import context_extensions

    def provider(_user_id: str, _thread_id: str):
        return "x" * 5000

    register_context_provider(provider)
    try:
        rows = context_extensions("u", "t", max_chars=321)
        assert rows == ["x" * 321]
    finally:
        unregister_context_provider(provider)
