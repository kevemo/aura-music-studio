from __future__ import annotations

from contextvars import ContextVar, Token

_current_user_id: ContextVar[str | None] = ContextVar("lss_current_user_id", default=None)
_current_owner_actor_id: ContextVar[str | None] = ContextVar("lss_current_owner_actor_id", default=None)
_current_owner_actor_name: ContextVar[str | None] = ContextVar("lss_current_owner_actor_name", default=None)


def set_current_user_id(user_id: str | None) -> Token:
    return _current_user_id.set(user_id)


def reset_current_user_id(token: Token) -> None:
    _current_user_id.reset(token)


def current_user_id() -> str | None:
    return _current_user_id.get()


def set_current_owner_actor(actor_id: str | None, actor_name: str | None = None) -> tuple[Token, Token]:
    """Bind the selected Kev/Mary owner identity to only the current async request context."""
    return _current_owner_actor_id.set(actor_id), _current_owner_actor_name.set(actor_name)


def reset_current_owner_actor(tokens: tuple[Token, Token]) -> None:
    actor_id_token, actor_name_token = tokens
    _current_owner_actor_id.reset(actor_id_token)
    _current_owner_actor_name.reset(actor_name_token)


def current_owner_actor_id() -> str | None:
    return _current_owner_actor_id.get()


def current_owner_actor_name() -> str | None:
    return _current_owner_actor_name.get()
