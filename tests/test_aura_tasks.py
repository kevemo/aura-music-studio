from __future__ import annotations

from datetime import timedelta

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_chat_store import AuraChatStore
from aura_music_studio.aura_task_worker import run_task
from aura_music_studio.aura_tasks import AuraTaskStore, TaskCreateRequest, iso, utcnow


def _user(accounts: AccountStore, email: str) -> str:
    return accounts.signup(email, "Task User", "very-long-test-password", "free").user_id


def test_task_is_private_and_worker_claim_is_leased(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_a = _user(accounts, "task-a@example.com")
    user_b = _user(accounts, "task-b@example.com")
    chat = AuraChatStore(accounts)
    tasks = AuraTaskStore(chat)
    thread = chat.create_thread(user_a)
    task = tasks.create(
        user_a,
        thread["id"],
        TaskCreateRequest(title="Reminder", kind="reminder", prompt="Check the mix", delay_minutes=1),
    )
    assert len(tasks.list(user_a)) == 1
    assert tasks.list(user_b) == []
    assert task["background_project_writes_allowed"] is False

    with chat._connect() as con:
        con.execute("UPDATE aura_tasks SET next_run_at=? WHERE id=?", (iso(utcnow() - timedelta(seconds=1)), task["id"]))

    claimed = tasks.claim_due("worker-a")
    assert claimed is not None
    assert claimed["id"] == task["id"]
    assert tasks.claim_due("worker-b") is None


def test_one_time_task_completes_and_recurring_task_advances(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _user(accounts, "task-recurring@example.com")
    chat = AuraChatStore(accounts)
    tasks = AuraTaskStore(chat)
    thread = chat.create_thread(user_id)

    one = tasks.create(user_id, thread["id"], TaskCreateRequest(title="Once", kind="reminder", prompt="Once", delay_minutes=1))
    with chat._connect() as con:
        con.execute("UPDATE aura_tasks SET next_run_at=? WHERE id=?", (iso(utcnow() - timedelta(seconds=1)), one["id"]))
    claimed = tasks.claim_due("worker")
    tasks.finish(claimed)
    finished = tasks.get(user_id, one["id"])
    assert finished["status"] == "completed"
    assert finished["enabled"] is False

    recurring = tasks.create(
        user_id,
        thread["id"],
        TaskCreateRequest(title="Hourly", kind="reminder", prompt="Hourly", delay_minutes=1, interval_minutes=60),
    )
    with chat._connect() as con:
        con.execute("UPDATE aura_tasks SET next_run_at=? WHERE id=?", (iso(utcnow() - timedelta(seconds=1)), recurring["id"]))
    claimed = tasks.claim_due("worker")
    old_due = claimed["next_run_at"]
    tasks.finish(claimed)
    updated = tasks.get(user_id, recurring["id"])
    assert updated["status"] == "active"
    assert updated["enabled"] is True
    assert updated["next_run_at"] != old_due


def test_failures_retry_then_fail_closed(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user_id = _user(accounts, "task-failure@example.com")
    chat = AuraChatStore(accounts)
    tasks = AuraTaskStore(chat)
    thread = chat.create_thread(user_id)
    task = tasks.create(user_id, thread["id"], TaskCreateRequest(title="Fail", kind="prompt", prompt="test", delay_minutes=1))

    for expected in (1, 2, 3):
        with chat._connect() as con:
            con.execute("UPDATE aura_tasks SET next_run_at=? WHERE id=?", (iso(utcnow() - timedelta(seconds=1)), task["id"]))
        claimed = tasks.claim_due("worker")
        assert claimed is not None
        tasks.finish(claimed, error="temporary failure")
        state = tasks.get(user_id, task["id"])
        assert state["failure_count"] == expected
    assert state["status"] == "failed"
    assert state["enabled"] is False


def test_recurring_frequency_minimum_is_hourly():
    with pytest.raises(Exception):
        TaskCreateRequest(title="Too fast", kind="reminder", prompt="No", delay_minutes=1, interval_minutes=30)


def test_worker_reminder_is_prose_only():
    result = run_task({"kind": "reminder", "prompt": "Check your draft"})
    assert result == "⏰ Aura reminder: Check your draft"
    assert "tool" not in result.lower()
