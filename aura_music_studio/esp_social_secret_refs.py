from __future__ import annotations

import os
import re
from collections.abc import Mapping

_TOKEN_REF = re.compile(r"^social-token://([A-Za-z0-9][A-Za-z0-9_-]{0,63})$")


def social_token_alias(secret_ref: str | None) -> str | None:
    match = _TOKEN_REF.fullmatch((secret_ref or "").strip())
    return match.group(1) if match else None


def social_token_env_name(secret_ref: str | None) -> str | None:
    alias = social_token_alias(secret_ref)
    if alias is None:
        return None
    normalized = re.sub(r"[^A-Za-z0-9]", "_", alias).upper()
    return f"AURA_SOCIAL_TOKEN_{normalized}"


def valid_social_token_ref(secret_ref: str | None) -> bool:
    return social_token_alias(secret_ref) is not None


def resolve_social_token(
    secret_ref: str | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve only the dedicated AURA_SOCIAL_TOKEN_* namespace.

    A Social House member can choose a connection alias, but cannot use that alias to
    read arbitrary process environment variables. Raw access tokens are never stored in
    Social House JSON.
    """
    env_name = social_token_env_name(secret_ref)
    if env_name is None:
        raise ValueError(
            "OAuth token reference must use social-token://<alias>; raw tokens and arbitrary environment references are forbidden"
        )
    value = (environ or os.environ).get(env_name, "").strip()
    if not value:
        raise LookupError(f"OAuth token secret is not configured for alias {social_token_alias(secret_ref)}")
    return value


__all__ = [
    "resolve_social_token",
    "social_token_alias",
    "social_token_env_name",
    "valid_social_token_ref",
]
