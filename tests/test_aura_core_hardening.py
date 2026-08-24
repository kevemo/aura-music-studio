from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_agent_core import AuraAgent, ModelReply
from aura_music_studio.aura_chat_hardening import install_aura_chat_hardening
from aura_music_studio.aura_chat_store import AuraChatStore
from aura_music_studio.plans import get_plan


class FakeModel:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, *, json_mode=False, temperature=.35):
        self.calls += 1
        return ModelReply(text="A different wording of the same result.", provider="fake", model="fake")


def _user(accounts: AccountStore, email: str = "member@example.com") -> str:
    return accounts.signup(email, "Member", "long-enough-password", "free").user_id


def test_regenerate_reuses_tool_result_without_reexecution(tmp_path):
    install_aura_chat_hardening()
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _user(accounts)
    store = AuraChatStore(accounts)
    thread = store.create_thread(user_id)
    user = store.add_message(user_id, thread["id"], "user", "Produce this project")
    run_id = store.start_tool_run(user_id, thread["id"], user["id"], "queue_full_production", {"project_name": "song"})
    store.finish_tool_run(run_id, result={"job_id": "job_1", "status": "queued"})
    store.add_message(user_id, thread["id"], "assistant", "The production was queued.")

    model = FakeModel()
    aura = AuraAgent(store=store, model=model)
    member = SimpleNamespace(user_id=user_id, plan=get_plan("free"))
    before = store.tool_runs(user_id, thread["id"])

    result = aura.regenerate(member=member, thread_id=thread["id"])
    after = store.tool_runs(user_id, thread["id"])

    assert len(before) == len(after) == 1
    assert result["regenerated_without_reexecuting_tools"] is True
    assert result["tool_runs"][0]["result"]["job_id"] == "job_1"
    assert model.calls == 1


def test_edit_invalidates_stale_tool_runs_and_downstream_messages(tmp_path):
    install_aura_chat_hardening()
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _user(accounts, "edit@example.com")
    store = AuraChatStore(accounts)
    thread = store.create_thread(user_id)
    first = store.add_message(user_id, thread["id"], "user", "Create an image")
    run1 = store.start_tool_run(user_id, thread["id"], first["id"], "create_visual", {"kind": "image"})
    store.finish_tool_run(run1, result={"directive_id": "one"})
    store.add_message(user_id, thread["id"], "assistant", "Created a plan")
    second = store.add_message(user_id, thread["id"], "user", "Now change it")
    run2 = store.start_tool_run(user_id, thread["id"], second["id"], "plan_creative_directive", {})
    store.finish_tool_run(run2, result={"directive_id": "two"})
    store.add_message(user_id, thread["id"], "assistant", "Changed")

    store.edit_user_message(user_id, thread["id"], first["id"], "Create a different image")

    rows = store.messages(user_id, thread["id"])
    assert len(rows) == 1
    assert rows[0]["content"] == "Create a different image"
    assert store.tool_runs(user_id, thread["id"]) == []


def test_branch_copies_attachments_and_survives_source_delete(tmp_path, monkeypatch):
    install_aura_chat_hardening()
    attachment_root = tmp_path / "chat-files"
    monkeypatch.setenv("AURA_CHAT_ATTACHMENT_DIR", str(attachment_root))
    monkeypatch.setenv("AURA_CHAT_AUTO_PERCEPTION", "false")

    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _user(accounts, "branch@example.com")
    store = AuraChatStore(accounts)
    thread = store.create_thread(user_id)
    message = store.add_message(user_id, thread["id"], "user", "Use this file")

    source_dir = attachment_root / user_id / thread["id"]
    source_dir.mkdir(parents=True, exist_ok=True)
    source = source_dir / "notes.txt"
    source.write_text("private branch context", encoding="utf-8")
    attachment = store.add_attachment(
        user_id,
        thread["id"],
        name="notes.txt",
        stored_path=str(source),
        mime_type="text/plain",
        kind="text",
        bytes_count=source.stat().st_size,
        sha256="a" * 64,
        extracted_text="private branch context",
        metadata={},
    )
    store.bind_attachments(user_id, thread["id"], message["id"], [attachment["id"]])

    branch = store.fork_thread(user_id, thread["id"], message["id"])
    branch_message = store.messages(user_id, branch["id"])[0]
    copied = store.message_attachments(user_id, branch["id"], branch_message["id"])

    assert len(copied) == 1
    copied_path = Path(copied[0]["stored_path"])
    assert copied_path.is_file()
    assert copied_path != source
    assert copied_path.read_text(encoding="utf-8") == "private branch context"

    store.delete_thread(user_id, thread["id"])
    assert not source.exists()
    assert copied_path.is_file()
    assert copied_path.read_text(encoding="utf-8") == "private branch context"
