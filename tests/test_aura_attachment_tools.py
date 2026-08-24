from __future__ import annotations

from types import SimpleNamespace

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_agent_tools import AuraToolRegistry, ToolCall
from aura_music_studio.aura_attachment_tools import (
    _promotion_requested,
    _rights_in_member_text,
    _select,
    install_aura_attachment_tools,
)
from aura_music_studio.aura_chat_store import AuraChatStore, sha256_file
from aura_music_studio.aura_project_bridge import PromoteAttachmentRequest, promote_attachment_for_member
from aura_music_studio.aura_runtime_context import current_turn, install_aura_runtime_context, latest_attachments
from aura_music_studio.plans import get_plan
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id


def _user(accounts: AccountStore, email: str) -> str:
    return accounts.signup(email, "Aura Attachment Test", "very-long-test-password", "free").user_id


def _prepare_attachment(tmp_path, monkeypatch, *, store: AuraChatStore, user_id: str, thread_id: str, name: str = "notes.txt"):
    attachment_root = tmp_path / "aura-attachments"
    monkeypatch.setenv("AURA_CHAT_ATTACHMENT_DIR", str(attachment_root))
    source_dir = attachment_root / user_id / thread_id
    source_dir.mkdir(parents=True, exist_ok=True)
    source = source_dir / name
    source.write_text("owned project material\n", encoding="utf-8")
    message = store.add_message(user_id, thread_id, "user", "I own this file. Add it to the project.")
    attachment = store.add_attachment(
        user_id,
        thread_id,
        name=name,
        stored_path=str(source),
        mime_type="text/plain",
        kind="text",
        bytes_count=source.stat().st_size,
        sha256=sha256_file(source),
        extracted_text="owned project material",
        metadata={},
    )
    store.bind_attachments(user_id, thread_id, message["id"], [attachment["id"]])
    return message, attachment, source


def test_rights_wording_must_come_from_member_text():
    assert _rights_in_member_text("I own this spreadsheet. Add it to the project.") is True
    assert _rights_in_member_text("I have permission to use this video; add it to the project.") is True
    assert _rights_in_member_text("Please add this file to the project.") is False
    assert _promotion_requested("I own this. Save it to the project.") is True


def test_multiple_current_files_require_unambiguous_selector():
    items = [
        {"id": "one", "name": "January.csv"},
        {"id": "two", "name": "February.csv"},
    ]
    with pytest.raises(ValueError, match="Multiple files"):
        _select(items, None)
    assert _select(items, "February.csv")["id"] == "two"
    assert _select(items, "jan")["id"] == "one"


def test_runtime_context_points_only_to_latest_user_turn(tmp_path):
    install_aura_runtime_context()
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _user(accounts, "context@example.com")
    store = AuraChatStore(accounts)
    first = store.create_thread(user_id)
    second = store.create_thread(user_id)

    message_a = store.add_message(user_id, first["id"], "user", "first")
    assert current_turn().message_id == message_a["id"]
    assert latest_attachments(store) == []

    message_b = store.add_message(user_id, second["id"], "user", "second")
    context = current_turn()
    assert context.user_id == user_id
    assert context.thread_id == second["id"]
    assert context.message_id == message_b["id"]


def test_promotion_is_idempotent_and_thread_owned(tmp_path, monkeypatch):
    import aura_music_studio.tenant_storage as tenant_storage

    install_aura_runtime_context()
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _user(accounts, "promote@example.com")
    store = AuraChatStore(accounts)
    thread = store.create_thread(user_id)
    other_thread = store.create_thread(user_id)
    _, attachment, _ = _prepare_attachment(
        tmp_path,
        monkeypatch,
        store=store,
        user_id=user_id,
        thread_id=thread["id"],
    )

    tenant_root = tmp_path / "projects"
    monkeypatch.setattr(tenant_storage, "ROOT", tenant_root.resolve())
    project = tenant_root / "members" / user_id / "song-one"
    project.mkdir(parents=True, exist_ok=True)
    token = set_current_user_id(user_id)
    member = SimpleNamespace(user_id=user_id, plan=get_plan("free"))
    body = PromoteAttachmentRequest(project_name="song-one", rights_confirmed=True)
    try:
        first = promote_attachment_for_member(member, thread["id"], attachment["id"], body, chat_store=store)
        second = promote_attachment_for_member(member, thread["id"], attachment["id"], body, chat_store=store)
        assert first["idempotent"] is False
        assert second["idempotent"] is True
        assert first["project_source_ref"] == second["project_source_ref"]
        assert first["creative_reference"]["id"] == second["creative_reference"]["id"]
        assert first["raw_private_chat_path_exposed"] is False

        with pytest.raises(KeyError):
            promote_attachment_for_member(member, other_thread["id"], attachment["id"], body, chat_store=store)
    finally:
        reset_current_user_id(token)


def test_tool_rejects_model_only_rights_flag(tmp_path, monkeypatch):
    import aura_music_studio.aura_attachment_tools as attachment_tools
    import aura_music_studio.tenant_storage as tenant_storage

    install_aura_runtime_context()
    install_aura_attachment_tools()
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _user(accounts, "gate@example.com")
    store = AuraChatStore(accounts)
    thread = store.create_thread(user_id)

    attachment_root = tmp_path / "aura-attachments"
    monkeypatch.setenv("AURA_CHAT_ATTACHMENT_DIR", str(attachment_root))
    source_dir = attachment_root / user_id / thread["id"]
    source_dir.mkdir(parents=True, exist_ok=True)
    source = source_dir / "owned.txt"
    source.write_text("hello", encoding="utf-8")
    message = store.add_message(user_id, thread["id"], "user", "Please add this file to the project")
    attachment = store.add_attachment(
        user_id,
        thread["id"],
        name="owned.txt",
        stored_path=str(source),
        mime_type="text/plain",
        kind="text",
        bytes_count=source.stat().st_size,
        sha256=sha256_file(source),
        extracted_text="hello",
        metadata={},
    )
    store.bind_attachments(user_id, thread["id"], message["id"], [attachment["id"]])

    tenant_root = tmp_path / "projects"
    monkeypatch.setattr(tenant_storage, "ROOT", tenant_root.resolve())
    (tenant_root / "members" / user_id / "song-one").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(attachment_tools, "AuraChatStore", lambda: store)
    token = set_current_user_id(user_id)
    try:
        registry = AuraToolRegistry(
            member=SimpleNamespace(user_id=user_id, plan=get_plan("free")),
            pinned_project="song-one",
            web_enabled=False,
            tools_enabled=True,
        )
        with pytest.raises(PermissionError, match="does not explicitly confirm ownership"):
            registry.execute(
                ToolCall(
                    name="promote_current_attachment",
                    arguments={"attachment": attachment["id"], "rights_confirmed": True},
                ),
                latest_user_message="Please add this file to the project",
            )
    finally:
        reset_current_user_id(token)
