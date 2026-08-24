from types import SimpleNamespace

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_chat_store import AuraChatStore
from aura_music_studio.aura_reasoning_modes import (
    detect_mode_command,
    get_reasoning_mode,
    mode_config,
    set_reasoning_mode,
)


def _store(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup("mode@example.com", "Mode User", "verysecurepassword", "free")
    return AuraChatStore(accounts), signup.user_id


def test_reasoning_mode_defaults_to_auto_and_persists(tmp_path):
    store, user_id = _store(tmp_path)
    thread = store.create_thread(user_id)
    assert get_reasoning_mode(store, user_id, thread["id"]) == "auto"
    assert set_reasoning_mode(store, user_id, thread["id"], "deep") == "deep"
    assert get_reasoning_mode(store, user_id, thread["id"]) == "deep"


def test_reasoning_modes_have_distinct_operational_profiles():
    assert mode_config("fast").history_messages < mode_config("deep").history_messages
    assert mode_config("creative").temperature > mode_config("deep").temperature


def test_conversational_mode_commands_are_strict():
    assert detect_mode_command("Aura, use Deep mode") == "deep"
    assert detect_mode_command("creative mode") == "creative"
    assert detect_mode_command("Please write a deep mode jazz song") is None
