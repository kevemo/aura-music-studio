from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .aura_agent_core import (
    AURA_CORE_SYSTEM,
    AuraAgent,
    ModelReply,
    _attachment_context,
    _history_messages,
    _memory_context,
)
from .aura_agent_tools import project_snapshot
from .aura_chat_store import AuraChatStore, sha256_file

_INSTALLED = False


def _copy_branch_attachments(store: AuraChatStore, user_id: str, source_thread: str, target_thread: str, source_messages: list[dict], target_messages: list[dict]) -> None:
    root = Path(os.getenv("AURA_CHAT_ATTACHMENT_DIR", "data/aura/attachments")).resolve()
    target_root = (root / user_id / target_thread).resolve()
    if root not in target_root.parents:
        raise ValueError("Invalid Aura branch attachment directory")
    target_root.mkdir(parents=True, exist_ok=True)
    for source_message, target_message in zip(source_messages, target_messages):
        attachments = store.message_attachments(user_id, source_thread, source_message["id"])
        for item in attachments:
            source = Path(str(item.get("stored_path") or "")).resolve()
            if not source.is_file():
                continue
            name = Path(str(item.get("name") or source.name)).name
            destination = target_root / f"branch_{item['id']}_{source.name}"
            if not destination.exists():
                shutil.copy2(source, destination)
            copied = store.add_attachment(
                user_id,
                target_thread,
                name=name,
                stored_path=str(destination),
                mime_type=item.get("mime_type"),
                kind=str(item.get("kind") or "document"),
                bytes_count=destination.stat().st_size,
                sha256=sha256_file(destination),
                extracted_text=item.get("extracted_text"),
                metadata={**(item.get("metadata") or {}), "branched_from_attachment_id": item["id"]},
            )
            store.bind_attachments(user_id, target_thread, target_message["id"], [copied["id"]])


def _tool_results_for_message(store: AuraChatStore, user_id: str, thread_id: str, message_id: str) -> list[dict]:
    rows = []
    for row in reversed(store.tool_runs(user_id, thread_id, limit=200)):
        if row.get("message_id") != message_id:
            continue
        result = row.get("result")
        if row.get("status") == "completed":
            rows.append({"tool": row.get("tool_name"), "ok": True, "result": result, "reused": True})
        elif row.get("status") == "failed":
            error = result.get("error") if isinstance(result, dict) else "Previous tool run failed"
            rows.append({"tool": row.get("tool_name"), "ok": False, "error": error, "reused": True})
    return rows


def _safe_regenerate(self: AuraAgent, *, member, thread_id: str) -> dict:
    """Regenerate prose without re-running project-changing or external tools.

    Tool results are evidence from the original user turn and are reused verbatim. This makes
    'Regenerate' semantically equivalent to 'give me another answer' rather than 'do the work again'.
    """
    user_id = member.user_id
    thread = self.store.thread(user_id, thread_id)
    if not thread:
        raise KeyError(thread_id)
    self.store.delete_last_assistant(user_id, thread_id)
    rows = self.store.messages(user_id, thread_id, limit=400)
    if not rows or rows[-1]["role"] != "user":
        raise ValueError("There is no user message to regenerate from")
    user_message = rows[-1]
    tool_results = _tool_results_for_message(self.store, user_id, thread_id, user_message["id"])
    attachments = self.store.message_attachments(user_id, thread_id, user_message["id"])
    pinned_project = thread.get("project_name")
    project_context = None
    if pinned_project:
        try:
            project_context = project_snapshot(pinned_project)
        except Exception as exc:
            project_context = {"project_name": pinned_project, "context_error": f"{type(exc).__name__}: {exc}"}
    summary = self._maybe_summarize(user_id, thread_id, rows)
    memories = self.store.memories(user_id, enabled_only=True, limit=50)
    system_parts = [AURA_CORE_SYSTEM]
    if summary:
        system_parts.append("Conversation summary from older turns:\n" + summary)
    memory_text = _memory_context(memories)
    if memory_text:
        system_parts.append(memory_text)
    if project_context:
        system_parts.append("Pinned project snapshot (private member context):\n" + json.dumps(project_context, ensure_ascii=False, default=str)[:45000])
    attach_text = _attachment_context(attachments)
    if attach_text:
        system_parts.append(attach_text)
    if tool_results:
        system_parts.append(
            "These are the authoritative tool results from the original member turn. Regenerate the answer using them; do not imply the tools were run again:\n"
            + json.dumps(tool_results, ensure_ascii=False, default=str)[:65000]
        )
    messages = [{"role": "system", "content": "\n\n".join(system_parts)}]
    messages.extend(_history_messages(rows, maximum=70))
    try:
        reply = self.model.complete(messages, temperature=0.52)
        text = reply.text
    except Exception as exc:
        if tool_results:
            text = "The original connected tool result is still preserved, but Aura's conversational model is currently unavailable for a regenerated wording."
            reply = ModelReply(text=text, provider="tool_result_fallback", model="none")
        else:
            raise RuntimeError(f"Aura reasoning is unavailable: {type(exc).__name__}: {exc}") from exc
    assistant = self.store.add_message(user_id, thread_id, "assistant", text)
    return {
        "message": assistant,
        "thread": self.store.thread(user_id, thread_id),
        "tool_runs": tool_results,
        "provider": reply.provider,
        "model": reply.model,
        "pinned_project": pinned_project,
        "regenerated_without_reexecuting_tools": True,
    }


def install_aura_chat_hardening() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_edit = AuraChatStore.edit_user_message
    original_fork = AuraChatStore.fork_thread
    original_delete = AuraChatStore.delete_thread

    def edit_user_message(self: AuraChatStore, user_id: str, thread_id: str, message_id: str, content: str):
        with self._connect() as con:
            row = con.execute(
                "SELECT created_at,role FROM aura_chat_messages WHERE id=? AND thread_id=?",
                (message_id, thread_id),
            ).fetchone()
            if not row or row["role"] != "user":
                raise KeyError(message_id)
            affected = [
                item["id"]
                for item in con.execute(
                    "SELECT id FROM aura_chat_messages WHERE thread_id=? AND created_at>=?",
                    (thread_id, row["created_at"]),
                ).fetchall()
            ]
        result = original_edit(self, user_id, thread_id, message_id, content)
        if affected:
            placeholders = ",".join("?" for _ in affected)
            with self._connect() as con:
                con.execute(
                    f"DELETE FROM aura_chat_tool_runs WHERE user_id=? AND thread_id=? AND message_id IN ({placeholders})",
                    (user_id, thread_id, *affected),
                )
                con.execute("DELETE FROM aura_chat_summaries WHERE user_id=? AND thread_id=?", (user_id, thread_id))
        return result

    def fork_thread(self: AuraChatStore, user_id: str, thread_id: str, through_message_id: str):
        source_rows = self.messages(user_id, thread_id, limit=400)
        selected = []
        for row in source_rows:
            selected.append(row)
            if row["id"] == through_message_id:
                break
        else:
            raise KeyError(through_message_id)
        result = original_fork(self, user_id, thread_id, through_message_id)
        target_rows = self.messages(user_id, result["id"], limit=400)
        _copy_branch_attachments(self, user_id, thread_id, result["id"], selected, target_rows)
        return result

    def delete_thread(self: AuraChatStore, user_id: str, thread_id: str) -> None:
        root = Path(os.getenv("AURA_CHAT_ATTACHMENT_DIR", "data/aura/attachments")).resolve()
        owned_dir = (root / user_id / thread_id).resolve()
        if root not in owned_dir.parents:
            raise ValueError("Invalid Aura attachment directory")
        original_delete(self, user_id, thread_id)
        shutil.rmtree(owned_dir, ignore_errors=True)

    AuraChatStore.edit_user_message = edit_user_message
    AuraChatStore.fork_thread = fork_thread
    AuraChatStore.delete_thread = delete_thread
    AuraAgent.regenerate = _safe_regenerate
    _INSTALLED = True


__all__ = ["install_aura_chat_hardening"]
