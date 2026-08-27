from types import SimpleNamespace

import pytest

from aura_music_studio import aura_agent_tools as tools
from aura_music_studio.accounts import AccountStore
from aura_music_studio.aura_chat_store import AuraChatStore
from aura_music_studio.aura_work_modes import (
    AuraWorkPlanStore,
    active_work_mode,
    detect_work_mode_command,
    get_work_mode,
    install_aura_work_modes,
    set_work_mode,
    work_mode_scope,
)


def _setup(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    first = accounts.signup("workmode@example.com", "Work Mode", "verysecurepassword", "free")
    second = accounts.signup("other@example.com", "Other User", "verysecurepassword", "free")
    chat = AuraChatStore(accounts)
    thread = chat.create_thread(first.user_id)
    return chat, first.user_id, second.user_id, thread["id"]


def test_work_mode_defaults_to_agent_and_persists_per_thread(tmp_path):
    chat, user_id, _other, thread_id = _setup(tmp_path)
    assert get_work_mode(chat, user_id, thread_id) == "agent"
    assert set_work_mode(chat, user_id, thread_id, "ask") == "ask"
    assert get_work_mode(chat, user_id, thread_id) == "ask"
    assert set_work_mode(chat, user_id, thread_id, "plan") == "plan"
    assert get_work_mode(chat, user_id, thread_id) == "plan"


def test_work_mode_commands_are_strict_not_accidental_keyword_matches():
    assert detect_work_mode_command("Aura, use Plan mode") == "plan"
    assert detect_work_mode_command("ask mode") == "ask"
    assert detect_work_mode_command("Agent work mode please") == "agent"
    assert detect_work_mode_command("Plan a cinematic agent battle mode for my game") is None
    assert detect_work_mode_command("Can you ask the agent to plan this?") is None


def test_ask_mode_filters_and_blocks_project_write_tools(tmp_path):
    chat, user_id, _other, thread_id = _setup(tmp_path)
    install_aura_work_modes()
    set_work_mode(chat, user_id, thread_id, "ask")
    registry = tools.AuraToolRegistry(
        member=SimpleNamespace(user_id=user_id),
        pinned_project=None,
        web_enabled=True,
        tools_enabled=True,
    )
    assert registry.specs()
    assert all(not row.get("write") for row in registry.specs())
    with pytest.raises(PermissionError, match="read-only"):
        registry.execute(
            tools.ToolCall(name="sync_song_dna", arguments={"project_name": "does-not-matter"}),
            latest_user_message="sync the song dna",
        )


def test_work_mode_scope_is_request_local_and_restores_previous_mode(tmp_path):
    chat, user_id, _other, thread_id = _setup(tmp_path)
    set_work_mode(chat, user_id, thread_id, "ask")
    assert active_work_mode() == "ask"
    with work_mode_scope(chat, user_id, thread_id, mode="agent"):
        assert active_work_mode() == "agent"
    assert active_work_mode() == "ask"


def test_work_plan_requires_approval_and_is_tenant_bound(tmp_path):
    chat, user_id, other_id, thread_id = _setup(tmp_path)
    plans = AuraWorkPlanStore(chat)
    plan = plans.create(
        user_id,
        thread_id,
        objective="Inspect my projects before the next approved step",
        project_name=None,
        calls=[{"name": "list_projects", "arguments": {}}],
    )
    assert plan["status"] == "draft"
    with pytest.raises(PermissionError, match="approved"):
        plans.begin_execution(user_id, thread_id, plan["id"])
    with pytest.raises(KeyError):
        plans.get(other_id, thread_id, plan["id"])
    approved = plans.approve(user_id, thread_id, plan["id"])
    assert approved["status"] == "approved"
    assert approved["approved_hash"] == approved["plan_hash"]
    running = plans.begin_execution(user_id, thread_id, plan["id"])
    assert running["status"] == "running"
    completed = plans.finish(user_id, thread_id, plan["id"], results=[{"tool": "list_projects", "ok": True, "result": []}])
    assert completed["status"] == "completed"


def test_approved_plan_hash_invalidates_if_calls_are_tampered(tmp_path):
    chat, user_id, _other, thread_id = _setup(tmp_path)
    plans = AuraWorkPlanStore(chat)
    plan = plans.create(
        user_id,
        thread_id,
        objective="List my projects",
        project_name=None,
        calls=[{"name": "list_projects", "arguments": {}}],
    )
    plans.approve(user_id, thread_id, plan["id"])
    with chat._connect() as con:
        con.execute(
            "UPDATE aura_work_plans SET calls_json=? WHERE id=?",
            ('[{"name":"inspect_project","arguments":{"project_name":"tampered"}}]', plan["id"]),
        )
    with pytest.raises(PermissionError, match="changed after approval"):
        plans.begin_execution(user_id, thread_id, plan["id"])
