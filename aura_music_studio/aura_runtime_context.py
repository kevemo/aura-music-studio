from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from .aura_chat_store import AuraChatStore

_INSTALLED = False


@dataclass(frozen=True)
class AuraTurnContext:
    user_id: str
    thread_id: str
    message_id: str


_CURRENT_TURN: ContextVar[AuraTurnContext | None] = ContextVar("aura_current_turn", default=None)


def current_turn() -> AuraTurnContext | None:
    return _CURRENT_TURN.get()


def latest_attachments(store: AuraChatStore | None = None) -> list[dict]:
    context = current_turn()
    if context is None:
        return []
    source = store or AuraChatStore()
    try:
        return source.message_attachments(context.user_id, context.thread_id, context.message_id)
    except Exception:
        return []


def install_aura_runtime_context() -> None:
    """Capture only the current user turn for tool execution in this request context.

    ContextVar prevents one concurrent request from reading another request's current turn.
    Durable ownership checks still happen in AuraChatStore/project APIs; this context is only
    a convenient pointer to the already-owned user message.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    original_add_message = AuraChatStore.add_message

    def add_message(self: AuraChatStore, user_id: str, thread_id: str, role: str, content: str):
        item = original_add_message(self, user_id, thread_id, role, content)
        if role == "user":
            _CURRENT_TURN.set(AuraTurnContext(user_id=user_id, thread_id=thread_id, message_id=item["id"]))
        return item

    AuraChatStore.add_message = add_message
    _INSTALLED = True


__all__ = ["AuraTurnContext", "current_turn", "latest_attachments", "install_aura_runtime_context"]
