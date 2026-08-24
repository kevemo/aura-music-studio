from __future__ import annotations

from types import SimpleNamespace

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_agent_core import AuraAgent, ModelReply, _explicit_memory
from aura_music_studio.aura_chat_store import AuraChatStore
from aura_music_studio.plans import get_plan


class FakeModel:
    def __init__(self):
        self.calls = []

    def complete(self, messages, *, json_mode=False, temperature=.35):
        self.calls.append({"messages": messages, "json_mode": json_mode, "temperature": temperature})
        return ModelReply(text="Aura test response", provider="fake", model="fake-fast")

    def diagnostics(self):
        return {"provider_mode": "fake", "offline_first": True}


def _user(accounts: AccountStore, email: str, name: str):
    result = accounts.signup(email, name, "very-long-test-password", "free")
    return result.user_id


def test_chat_threads_are_private_per_member(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_a = _user(accounts, "a@example.com", "Member A")
    user_b = _user(accounts, "b@example.com", "Member B")
    store = AuraChatStore(accounts)

    thread_a = store.create_thread(user_a, "Private A")
    store.add_message(user_a, thread_a["id"], "user", "A secret project thought")
    thread_b = store.create_thread(user_b, "Private B")

    assert store.thread(user_b, thread_a["id"]) is None
    assert store.thread(user_a, thread_b["id"]) is None
    assert [row["title"] for row in store.list_threads(user_a)] == ["A secret project thought"]
    assert [row["title"] for row in store.list_threads(user_b)] == ["Private B"]


def test_search_edit_and_branch_conversation(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _user(accounts, "member@example.com", "Member")
    store = AuraChatStore(accounts)
    thread = store.create_thread(user_id, "Original")
    first = store.add_message(user_id, thread["id"], "user", "Plan the chorus")
    store.add_message(user_id, thread["id"], "assistant", "First answer")
    second = store.add_message(user_id, thread["id"], "user", "Now make it softer")
    store.add_message(user_id, thread["id"], "assistant", "Second answer")

    assert store.list_threads(user_id, query="softer")[0]["id"] == thread["id"]

    branch = store.fork_thread(user_id, thread["id"], second["id"])
    branch_messages = store.messages(user_id, branch["id"])
    assert [row["content"] for row in branch_messages] == [
        "Plan the chorus", "First answer", "Now make it softer"
    ]

    store.edit_user_message(user_id, thread["id"], first["id"], "Plan a bigger chorus")
    edited = store.messages(user_id, thread["id"])
    assert len(edited) == 1
    assert edited[0]["content"] == "Plan a bigger chorus"


def test_memory_is_explicit_and_deletable(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _user(accounts, "memory@example.com", "Memory User")
    store = AuraChatStore(accounts)

    assert _explicit_memory("I like acoustic guitars") is None
    explicit = _explicit_memory("Remember that I prefer acoustic guitars in ballads")
    assert explicit is not None
    label, content = explicit
    item = store.add_memory(user_id, label, content)
    assert store.memories(user_id)[0]["content"] == "I prefer acoustic guitars in ballads"
    assert store.delete_memory(user_id, item["id"]) is True
    assert store.memories(user_id) == []


def test_attachment_can_be_bound_only_inside_owned_thread(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _user(accounts, "files@example.com", "File User")
    store = AuraChatStore(accounts)
    thread = store.create_thread(user_id)
    message = store.add_message(user_id, thread["id"], "user", "Read this file")
    attachment = store.add_attachment(
        user_id,
        thread["id"],
        name="notes.txt",
        stored_path=str(tmp_path / "notes.txt"),
        mime_type="text/plain",
        kind="text",
        bytes_count=12,
        sha256="a" * 64,
        extracted_text="hello world",
        metadata={"characters": 11},
    )
    store.bind_attachments(user_id, thread["id"], message["id"], [attachment["id"]])
    bound = store.message_attachments(user_id, thread["id"], message["id"])
    assert len(bound) == 1
    assert bound[0]["name"] == "notes.txt"
    assert bound[0]["extracted_text"] == "hello world"


def test_general_chat_uses_fast_single_model_call_and_saves_no_memory(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _user(accounts, "fast@example.com", "Fast User")
    store = AuraChatStore(accounts)
    thread = store.create_thread(user_id)
    model = FakeModel()
    aura = AuraAgent(store=store, model=model)
    member = SimpleNamespace(user_id=user_id, plan=get_plan("free"))

    result = aura.respond(member=member, thread_id=thread["id"], text="Explain compression in simple terms")

    assert result["message"]["content"] == "Aura test response"
    assert len(model.calls) == 1
    assert model.calls[0]["json_mode"] is False
    assert store.memories(user_id) == []


def test_explicit_remember_request_is_saved_and_injected(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _user(accounts, "remember@example.com", "Remember User")
    store = AuraChatStore(accounts)
    thread = store.create_thread(user_id)
    model = FakeModel()
    aura = AuraAgent(store=store, model=model)
    member = SimpleNamespace(user_id=user_id, plan=get_plan("free"))

    result = aura.respond(
        member=member,
        thread_id=thread["id"],
        text="Remember that my preferred mastering target is natural rather than aggressive",
    )

    assert result["memory_saved"] is not None
    memories = store.memories(user_id)
    assert len(memories) == 1
    assert "natural rather than aggressive" in memories[0]["content"]
    system = model.calls[-1]["messages"][0]["content"]
    assert "Explicit user-approved Aura memories" in system
