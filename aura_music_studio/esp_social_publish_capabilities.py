from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field

from .esp_social_provider_adapters import ADAPTERS
from .esp_social_secret_refs import social_token_env_name, valid_social_token_ref
from .esp_social_threads_adapter import ThreadsGraphAdapter

# This map deliberately describes only content surfaces that the current runtime
# adapters actually implement. Planning capabilities remain broader in
# social_management.PLATFORM_CAPABILITIES.
_ADAPTER_CONTENT_TYPES: dict[str, frozenset[str]] = {
    "facebook_pages_graph": frozenset({"post"}),
    "instagram_graph": frozenset({"post", "reel"}),
    "threads_graph": frozenset({"post"}),
    "tiktok_content_posting": frozenset({"video"}),
    "youtube_data_v3": frozenset({"video", "short"}),
}
_ADAPTER_PLATFORMS = {
    "facebook_pages_graph": "facebook",
    ThreadsGraphAdapter.name: ThreadsGraphAdapter.platform,
    **{name: adapter.platform for name, adapter in ADAPTERS.items()},
}


class PublishCapability(BaseModel):
    platform: str
    content_type: str
    adapter: str | None = None
    implemented: bool = False
    configured: bool = False
    connected: bool = False
    active: bool = False
    publishable: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


def implemented_content_types(platform: str) -> list[str]:
    clean_platform = (platform or "").strip()
    values: set[str] = set()
    for adapter_name, adapter_platform in _ADAPTER_PLATFORMS.items():
        if adapter_platform == clean_platform:
            values.update(_ADAPTER_CONTENT_TYPES.get(adapter_name, frozenset()))
    return sorted(values)


def implementation_capabilities() -> dict[str, dict[str, Any]]:
    platforms = sorted(set(_ADAPTER_PLATFORMS.values()))
    return {
        platform: {
            "auto_publish_implemented": bool(implemented_content_types(platform)),
            "auto_publish_content_types": implemented_content_types(platform),
            "publishing_adapters": sorted(
                name for name, value in _ADAPTER_PLATFORMS.items() if value == platform
            ),
        }
        for platform in platforms
    }


def _append(result: PublishCapability, code: str, reason: str) -> None:
    if code not in result.reason_codes:
        result.reason_codes.append(code)
        result.reasons.append(reason)


def _facebook_deployment_checks(result: PublishCapability, connection: Any, metadata: dict[str, Any]) -> None:
    """Verify local Facebook Page deployment prerequisites without a network call.

    Facebook Pages is intentionally not part of the member OAuth registry. Its
    Page token is provisioned by an ESP owner as a restricted social-token alias,
    so queue/readiness state must not claim publishability unless that alias
    resolves to a deployment secret and the Graph API version/Page identity are
    locally configured. Meta still remains authoritative at provider-call time.
    """

    token_ref = getattr(connection, "token_secret_ref", None)
    env_name = social_token_env_name(token_ref)
    if not env_name or not (os.getenv(env_name) or "").strip():
        _append(
            result,
            "deployment_credential_unavailable",
            "deployment-managed Facebook Page credential is not configured on this server",
        )

    graph_version = (
        os.getenv("AURA_FACEBOOK_GRAPH_VERSION")
        or os.getenv("AURA_META_GRAPH_VERSION")
        or ""
    ).strip()
    if not graph_version:
        _append(
            result,
            "provider_config_missing",
            "Facebook Graph API version is not configured on this server",
        )

    page_id = str(
        getattr(connection, "account_external_id", None)
        or metadata.get("facebook_page_id")
        or ""
    ).strip()
    if not page_id.isdigit() or not 2 <= len(page_id) <= 40:
        _append(
            result,
            "facebook_page_id_invalid",
            "a numeric Facebook Page ID is required for automatic Page publishing",
        )


def resolve_publish_capability(
    connection: Any | None,
    *,
    platform: str,
    content_type: str,
) -> PublishCapability:
    clean_platform = (platform or "").strip()
    clean_content_type = (content_type or "").strip()
    result = PublishCapability(platform=clean_platform, content_type=clean_content_type)

    if connection is None:
        _append(result, "no_connection", "official publishing connection unavailable")
        return result

    result.connected = getattr(connection, "state", None) == "connected"
    if not result.connected:
        _append(result, "disconnected", "official publishing connection is not connected")

    connection_platform = str(getattr(connection, "platform", "") or "").strip()
    if connection_platform != clean_platform:
        _append(result, "connection_platform_mismatch", "publishing connection does not match the requested platform")

    if getattr(connection, "supports_auto_publish", False) is not True:
        _append(result, "auto_publish_disabled", "automatic publishing is not enabled for this connection")

    # Keep credential validation ahead of adapter-state reasons so existing queue
    # diagnostics remain stable while the richer capability codes are added.
    if not valid_social_token_ref(getattr(connection, "token_secret_ref", None)):
        _append(
            result,
            "invalid_credential_reference",
            "OAuth token reference must use the restricted social-token:// alias format or encrypted social-oauth:// credential format",
        )

    metadata = getattr(connection, "metadata", {}) or {}
    adapter_name = str(metadata.get("publishing_adapter") or "").strip()
    result.adapter = adapter_name or None
    result.active = bool(adapter_name) and metadata.get("publishing_adapter_active") is True
    if not adapter_name:
        _append(result, "adapter_missing", "official publishing adapter not active")
    elif not result.active:
        _append(result, "adapter_inactive", "official publishing adapter not active")

    expected_platform = _ADAPTER_PLATFORMS.get(adapter_name) if adapter_name else None
    result.implemented = expected_platform is not None
    if adapter_name and not result.implemented:
        _append(result, "adapter_unavailable", "configured publishing adapter is not implemented by this runtime")
    elif expected_platform is not None and expected_platform != clean_platform:
        _append(result, "adapter_platform_mismatch", "configured publishing adapter belongs to a different platform")

    supported_types = _ADAPTER_CONTENT_TYPES.get(adapter_name, frozenset())
    if result.implemented and clean_content_type not in supported_types:
        _append(
            result,
            "unsupported_content_type",
            f"{clean_content_type or 'requested content'} is planning-only for the configured publishing adapter",
        )

    # Facebook Pages is deployment-managed rather than member-OAuth managed, so
    # local deployment prerequisites are part of truthful queue readiness.
    if adapter_name == "facebook_pages_graph" and expected_platform == clean_platform:
        _facebook_deployment_checks(result, connection, metadata)

    result.configured = not any(
        code in result.reason_codes
        for code in {
            "adapter_missing",
            "adapter_unavailable",
            "adapter_platform_mismatch",
            "invalid_credential_reference",
            "deployment_credential_unavailable",
            "provider_config_missing",
            "facebook_page_id_invalid",
        }
    )
    result.publishable = (
        result.implemented
        and result.configured
        and result.connected
        and result.active
        and getattr(connection, "supports_auto_publish", False) is True
        and not result.reason_codes
    )
    return result


__all__ = [
    "PublishCapability",
    "implemented_content_types",
    "implementation_capabilities",
    "resolve_publish_capability",
]
