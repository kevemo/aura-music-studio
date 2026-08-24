from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_artifacts import AuraArtifactStore, _select_artifact
from aura_music_studio.aura_chat_store import AuraChatStore


def _user(accounts: AccountStore, email: str) -> str:
    return accounts.signup(email, "Artifact User", "very-long-test-password", "free").user_id


def test_artifacts_are_private_versioned_and_restorable(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_a = _user(accounts, "artifact-a@example.com")
    user_b = _user(accounts, "artifact-b@example.com")
    chat = AuraChatStore(accounts)
    store = AuraArtifactStore(chat)
    thread_a = chat.create_thread(user_a)
    thread_b = chat.create_thread(user_b)

    item = store.create(
        user_a,
        thread_a["id"],
        title="Launch Plan",
        kind="markdown",
        content="# Version one\n",
    )
    assert item["current_version"] == 1
    assert item["code_execution_enabled"] is False
    assert store.list(user_b, thread_b["id"]) == []
    assert store.get(user_b, thread_b["id"], item["id"]) is None

    second = store.update(
        user_a,
        thread_a["id"],
        item["id"],
        content="# Version two\n",
        note="Second draft",
    )
    assert second["current_version"] == 2
    assert second["content"] == "# Version two\n"
    versions = store.versions(user_a, thread_a["id"], item["id"])
    assert [row["version"] for row in versions] == [2, 1]

    restored = store.restore(user_a, thread_a["id"], item["id"], 1)
    assert restored["current_version"] == 3
    assert restored["content"] == "# Version one\n"
    assert store.versions(user_a, thread_a["id"], item["id"])[0]["note"] == "Restored version 1"


def test_artifact_code_is_storage_only(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _user(accounts, "artifact-code@example.com")
    chat = AuraChatStore(accounts)
    store = AuraArtifactStore(chat)
    thread = chat.create_thread(user_id)
    item = store.create(
        user_id,
        thread["id"],
        title="Example Python",
        kind="code",
        language="python",
        content="print('hello')\n",
    )
    assert item["kind"] == "code"
    assert item["language"] == "python"
    assert item["code_execution_enabled"] is False


def test_artifact_selector_requires_unique_match():
    rows = [
        {"id": "a", "title": "Launch Plan"},
        {"id": "b", "title": "Launch Checklist"},
    ]
    assert _select_artifact(rows, "a")["title"] == "Launch Plan"
    assert _select_artifact(rows, "checklist")["id"] == "b"
    with pytest.raises(ValueError, match="ambiguous"):
        _select_artifact(rows, "launch")


def test_delete_removes_artifact_and_versions(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _user(accounts, "artifact-delete@example.com")
    chat = AuraChatStore(accounts)
    store = AuraArtifactStore(chat)
    thread = chat.create_thread(user_id)
    item = store.create(user_id, thread["id"], title="Temporary", kind="text", content="one")
    store.update(user_id, thread["id"], item["id"], content="two")
    assert store.delete(user_id, thread["id"], item["id"]) is True
    assert store.get(user_id, thread["id"], item["id"]) is None
    with chat._connect() as con:
        count = con.execute("SELECT COUNT(*) AS c FROM aura_artifact_versions WHERE artifact_id=?", (item["id"],)).fetchone()["c"]
    assert count == 0
