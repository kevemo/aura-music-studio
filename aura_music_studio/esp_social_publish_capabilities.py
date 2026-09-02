from __future__ import annotations

import os
import re
from typing import Any

from pydantic import BaseModel, Field

from .esp_social_provider_adapters import ADAPTERS
from .esp_social_secret_refs import valid_social_token_ref

# This map deliberately describes only content surfaces that the current runtime
# adapters actually implement. Planning capabilities remain broader in
# social_management.PLATFORM_CAPABILITIES.
_ADAPTER_CONTENT_TYPES: dict[str, frozenset[str]] = {
    "facebook_pages_graph": frozenset({"post"}),
    "instagram_graph": frozenset({"post", "reel"}),
    "tiktok_content_posting": frozenset({"video"}),
    "youtube_data_v3": frozenset({"video", "short"}),
}
_ADAPTER_PLATFORMS = {
    "facebook_pages_graph": "facebook",
    **{name: adapter.platform for name, adapter in ADAPTERS.items()},
}
_GRAPH_VERSION_RE = re.compile(r"^v[0-9]+(?:\.[0-9]+)?$")
_PAGE_ID_RE = re.compile(r"^[0-9]{2,40}$")


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


def _provider_configuration_reasons(
    result: PublishCapability,
    connection: Any,
    adapter_name: str,
) -> None:
    if adapter_name == "facebook_pages_graph":
        page_id = (
            getattr(connection, "account_external_id", None)
            or str(getattr(connection, "metadata", {}).get("facebook_page_id") or "")
        ).strip()
        if not _PAGE_ID_RE.fullmatch(page_id):
            _append(result, "missing_provider_configuration", "verified numeric Facebook Page ID is required")
        version = (
            os.getenv("AURA_FACEBOOK_GRAPH_VERSION", "").strip()
            or os.getenv("AURA_META_GRAPH_VERSION", "").strip()
        )
        if not _GRAPH_VERSION_RE.fullmatch(version):
            _append(result, "missing_provider_configuration", "Facebook Graph API version is not configured")
    elif adapter_name == "instagram_graph":
        account_id = (
            getattr(connection, "account_external_id", None)
            or str(getattr(connection, "metadata", {}).get("instagram_user_id") or "")
        ).strip()
        if not account_id:
            _append(result, "missing_provider_configuration", "Instagram professional account ID is required")
        version = (
            os.getenv("AURA_INSTAGRAM_GRAPH_VERSION", "").strip()
            or os.getenv("AURA_META_GRAPH_VERSION", "").strip()
        )
        if not _GRAPH_VERSION_RE.fullmatch(version):
            _append(result, "missing_provider_configuration", "Instagram Graph API version is not configured")


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

    metadata = getattr(connection, "metadata", {}) or {}
    adapter_name = str(metadata.get("publishing_adapter") or "").strip()
    result.adapter = adapter_name or None
    if not adapter_name:
        _append(result, "adapter_missing", "official publishing adapter is not configured")
        return result

    result.active = metadata.get("publishing_adapter_active") is True
    if not result.active:
        _append(result, "adapter_inactive", "official publishing adapter is not active")

    expected_platform = _ADAPTER_PLATFORMS.get(adapter_name)
    result.implemented = expected_platform is not None
    if not result.implemented:
        _append(result, "adapter_unavailable", "configured publishing adapter is not implemented by this runtime")
    elif expected_platform != clean_platform:
        _append(result, "adapter_platform_mismatch", "configured publishing adapter belongs to a different platform")

    supported_types = _ADAPTER_CONTENT_TYPES.get(adapter_name, frozenset())
    if result.implemented and clean_content_type not in supported_types:
        _append(
            result,
            "unsupported_content_type",
            f"{clean_content_type or 'requested content'} is planning-only for the configured publishing adapter",
        )

    if not valid_social_token_ref(getattr(connection, "token_secret_ref", None)):
        _append(
            result,
            "invalid_credential_reference",
            "OAuth credential must use an approved social-token:// or social-oauth:// reference",
        )

    if result.implemented and expected_platform == clean_platform:
        _provider_configuration_reasons(result, connection, adapter_name)

    result.configured = not any(
        code in result.reason_codes
        for code in {
            "adapter_missing",
            "adapter_unavailable",
            "adapter_platform_mismatch",
            "invalid_credential_reference",
            "missing_provider_configuration",
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
