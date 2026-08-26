from __future__ import annotations

import os
import re
from collections.abc import Mapping

_TOKEN_REF = re.compile(r"^social-token://([A-Za-z0-9][A-Za-z0-9_-]{0,63})$")
_OAUTH_REF = re.compile(r"^social-oauth://([A-Za-z0-9][A-Za-z0-9_-]{0,95})$")


def social_token_alias(secret_ref: str | None) -> str | None:
    match = _TOKEN_REF.fullmatch((secret_ref or "").strip())
    return match.group(1) if match else None


def social_oauth_credential_id(secret_ref: str | None) -> str | None:
    match = _OAUTH_REF.fullmatch((secret_ref or "").strip())
    return match.group(1) if match else None


def social_token_env_name(secret_ref: str | None) -> str | None:
    alias = social_token_alias(secret_ref)
    if alias is None:
        return None
    normalized = re.sub(r"[^A-Za-z0-9]", "_", alias).upper()
    return f"AURA_SOCIAL_TOKEN_{normalized}"


def valid_social_token_ref(secret_ref: str | None) -> bool:
    """Accept only ESP's two non-raw credential reference namespaces."""
    return social_token_alias(secret_ref) is not None or social_oauth_credential_id(secret_ref) is not None


def resolve_social_token(
    secret_ref: str | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve a provider credential without allowing arbitrary secret lookup.

    `social-token://` is the owner/deployment-managed environment alias path retained for
    controlled integrations. `social-oauth://` resolves the signed-in ESP member's
    encrypted OAuth credential through the tenant-bound vault. Raw access tokens, file
    paths and arbitrary process environment names are never accepted.
    """
    credential_id = social_oauth_credential_id(secret_ref)
    if credential_id is not None:
        # Local import prevents the OAuth service from becoming a dependency of ordinary
        # Social House model loading and avoids an import cycle.
        from .esp_social_oauth import resolve_social_oauth_token

        return resolve_social_oauth_token(secret_ref)

    env_name = social_token_env_name(secret_ref)
    if env_name is None:
        raise ValueError(
            "OAuth token reference must use social-token://<alias> or social-oauth://<credential-id>; raw tokens and arbitrary secret references are forbidden"
        )
    value = (environ or os.environ).get(env_name, "").strip()
    if not value:
        raise LookupError(f"OAuth token secret is not configured for alias {social_token_alias(secret_ref)}")
    return value


__all__ = [
    "resolve_social_token",
    "social_oauth_credential_id",
    "social_token_alias",
    "social_token_env_name",
    "valid_social_token_ref",
]
