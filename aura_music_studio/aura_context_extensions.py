from __future__ import annotations

from contextvars import ContextVar
from threading import RLock
from typing import Callable

from . import aura_agent_core as core
from .aura_esp_tools import install_aura_esp_tools
from .brand_migration import rebrand_text
from .branding import PRODUCT_FULL_NAME

ContextProvider = Callable[[str, str], str | None]

_INSTALLED = False
_PROVIDERS: list[ContextProvider] = []
_PROVIDER_LOCK = RLock()
_ACTIVE_SCOPE: ContextVar[tuple[str, str] | None] = ContextVar("aura_context_extension_scope", default=None)
_CORE_SIGNATURES = (
    "You are Aura, the general AI co-creator and operating intelligence inside Pulsar-Frequency House",
    f"You are Aura, the general AI co-creator and operating intelligence inside {PRODUCT_FULL_NAME}",
)


def register_context_provider(provider: ContextProvider) -> None:
    """Register one bounded private context provider."""
    with _PROVIDER_LOCK:
        if provider not in _PROVIDERS:
            _PROVIDERS.append(provider)


def unregister_context_provider(provider: ContextProvider) -> None:
    """Remove a previously registered provider without disturbing other extensions."""
    with _PROVIDER_LOCK:
        while provider in _PROVIDERS:
            _PROVIDERS.remove(provider)


def context_extensions(user_id: str, thread_id: str, *, max_chars: int = 18000) -> list[str]:
    rows: list[str] = []
    used = 0
    with _PROVIDER_LOCK:
        providers = list(_PROVIDERS)
    for provider in providers:
        try:
            value = str(provider(user_id, thread_id) or "").strip()
        except Exception:
            continue
        if not value:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        value = value[:remaining]
        rows.append(value)
        used += len(value)
    return rows


def _inject_messages(messages: list[dict], user_id: str, thread_id: str) -> list[dict]:
    if not messages:
        return messages
    first = messages[0]
    content = str(first.get("content") or "")
    if first.get("role") != "system" or not any(signature in content for signature in _CORE_SIGNATURES):
        return messages

    # Aura's authoritative identity must be current before inference, not only rewritten in the
    # HTTP response after the model has already received a retired product identity.
    copied = [dict(item) for item in messages]
    copied[0]["content"] = rebrand_text(content)

    extensions = context_extensions(user_id, thread_id)
    if extensions:
        copied[0]["content"] = str(copied[0].get("content") or "") + "\n\n" + "\n\n".join(extensions)
    return copied


def install_aura_context_extensions() -> None:
    """Inject private context and current branding into normal, streaming and regenerated Aura turns.

    The active user/thread scope is a ContextVar, so concurrent requests cannot inherit one
    another's profile/workspace context. Normal model calls are changed only when the first
    system message is Aura Core itself; private tool routing and summarisation prompts remain
    unaffected by user profile instructions.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    # ESP tools are registered here because this installer is already part of the canonical Aura
    # composition path. Registration is read-only and the tool remains undiscoverable unless the
    # signed-in member has current server-authoritative ESP access.
    install_aura_esp_tools()

    original_respond = core.AuraAgent.respond
    original_complete = core.AuraModelClient.complete
    original_regenerate = getattr(core.AuraAgent, "regenerate", None)

    def respond(self: core.AuraAgent, *, member, thread_id: str, **kwargs):
        token = _ACTIVE_SCOPE.set((member.user_id, thread_id))
        try:
            return original_respond(self, member=member, thread_id=thread_id, **kwargs)
        finally:
            _ACTIVE_SCOPE.reset(token)

    def complete(self: core.AuraModelClient, messages: list[dict], **kwargs):
        scope = _ACTIVE_SCOPE.get()
        if scope:
            messages = _inject_messages(messages, scope[0], scope[1])
        return original_complete(self, messages, **kwargs)

    core.AuraAgent.respond = respond
    core.AuraModelClient.complete = complete

    if original_regenerate is not None:
        def regenerate(self: core.AuraAgent, *, member, thread_id: str, **kwargs):
            token = _ACTIVE_SCOPE.set((member.user_id, thread_id))
            try:
                return original_regenerate(self, member=member, thread_id=thread_id, **kwargs)
            finally:
                _ACTIVE_SCOPE.reset(token)
        core.AuraAgent.regenerate = regenerate

    from . import aura_streaming

    original_build_generation = aura_streaming._build_generation

    def build_generation(*, member, thread_id: str, text: str, attachment_ids: list[str]):
        result = original_build_generation(
            member=member,
            thread_id=thread_id,
            text=text,
            attachment_ids=attachment_ids,
        )
        # Streaming gained reasoning configuration as a fifth return value. Preserve the whole
        # contract instead of destructuring a fixed tuple, so future metadata additions do not
        # silently break the context-extension layer again.
        if not isinstance(result, tuple) or len(result) < 4:
            return result
        values = list(result)
        values[1] = _inject_messages(values[1], member.user_id, thread_id)
        return tuple(values)

    aura_streaming._build_generation = build_generation
    _INSTALLED = True


__all__ = [
    "ContextProvider",
    "register_context_provider",
    "unregister_context_provider",
    "context_extensions",
    "install_aura_context_extensions",
]
