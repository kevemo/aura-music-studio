from __future__ import annotations

import os
import re

import httpx

from .esp_social_provider_adapters import (
    ProviderAdapterError,
    ProviderProgress,
    _caption,
    _json_or_error,
    _network_error,
)
from .esp_social_publish_media import ResolvedPublishMedia
from .social_management import PlatformVariant, SocialConnection, SocialContent

_GRAPH_VERSION_RE = re.compile(r"^v[0-9]+(?:\.[0-9]+)?$")
_PAGE_ID_RE = re.compile(r"^[0-9]{2,40}$")


class FacebookPagesAdapter:
    """Bounded Facebook Page feed/photo publisher.

    This adapter deliberately supports Page text posts and single-image Page posts only.
    It does not claim personal-profile publishing, Reels, Stories, video publishing, inbox,
    analytics, or permissions that have not been granted by Meta to the configured app.
    """

    name = "facebook_pages_graph"
    platform = "facebook"

    def __init__(self, *, timeout: float = 30.0):
        self.timeout = timeout

    @staticmethod
    def _base() -> str:
        version = (
            os.getenv("AURA_FACEBOOK_GRAPH_VERSION", "").strip()
            or os.getenv("AURA_META_GRAPH_VERSION", "").strip()
        )
        if not version:
            raise ProviderAdapterError(
                "AURA_FACEBOOK_GRAPH_VERSION (or AURA_META_GRAPH_VERSION) must be configured before Facebook Page publishing is enabled"
            )
        if not _GRAPH_VERSION_RE.fullmatch(version):
            raise ProviderAdapterError("Configured Facebook Graph API version is invalid")
        return f"https://graph.facebook.com/{version}"

    @staticmethod
    def _page_id(connection: SocialConnection) -> str:
        page_id = (
            connection.account_external_id
            or str(connection.metadata.get("facebook_page_id") or "")
        ).strip()
        if not _PAGE_ID_RE.fullmatch(page_id):
            raise ProviderAdapterError("A verified numeric Facebook Page ID is required")
        return page_id

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        clean = (token or "").strip()
        if not clean:
            raise ProviderAdapterError("Facebook Page access token is missing")
        return {"Authorization": f"Bearer {clean}"}

    def start(
        self,
        *,
        token: str,
        connection: SocialConnection,
        content: SocialContent,
        variant: PlatformVariant,
        media: list[ResolvedPublishMedia],
    ) -> ProviderProgress:
        if variant.content_type != "post":
            raise ProviderAdapterError(
                "Facebook Pages adapter currently supports Page post variants only; Reels and Stories remain disabled"
            )
        if len(media) > 1:
            raise ProviderAdapterError(
                "Facebook Pages publishing currently supports at most one approved image"
            )

        page_id = self._page_id(connection)
        message = _caption(variant)
        endpoint = "feed"
        data: dict[str, str] = {"message": message}
        if media:
            item = media[0]
            if item.kind != "image":
                raise ProviderAdapterError(
                    "Facebook Pages adapter currently supports text posts or one image post only"
                )
            public_url = (item.public_url or "").strip()
            if not public_url.startswith("https://"):
                raise ProviderAdapterError(
                    "Facebook Page image publishing requires a provider-accessible HTTPS media URL"
                )
            endpoint = "photos"
            data = {"url": public_url, "caption": message, "published": "true"}
        elif not message:
            raise ProviderAdapterError("Facebook Page text posts require a non-empty message")

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self._base()}/{page_id}/{endpoint}",
                    headers=self._headers(token),
                    data=data,
                )
        except httpx.RequestError as exc:
            raise _network_error("Facebook", exc) from exc

        payload = _json_or_error(response, "Facebook")
        post_id = str(payload.get("post_id") or payload.get("id") or "").strip()
        if not post_id:
            raise ProviderAdapterError("Facebook did not return a published Page post ID")
        return ProviderProgress(
            state="published",
            provider_job_id=post_id,
            external_post_id=post_id,
            detail="Facebook Graph API confirmed Page publication.",
            metadata={"surface": "page", "kind": "photo" if media else "feed"},
        )

    def poll(
        self,
        *,
        token: str,
        connection: SocialConnection,
        content: SocialContent,
        variant: PlatformVariant,
        provider_job_id: str,
        provider_metadata: dict,
    ) -> ProviderProgress:
        post_id = (provider_job_id or "").strip()
        if not post_id or len(post_id) > 160:
            raise ProviderAdapterError("Facebook Page post ID is invalid")
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(
                    f"{self._base()}/{post_id}",
                    headers=self._headers(token),
                    params={"fields": "id,permalink_url"},
                )
        except httpx.RequestError as exc:
            raise _network_error("Facebook", exc) from exc
        payload = _json_or_error(response, "Facebook")
        confirmed_id = str(payload.get("id") or "").strip()
        if not confirmed_id:
            return ProviderProgress(
                state="failed",
                provider_job_id=post_id,
                detail="Facebook Page post could not be confirmed",
                retryable=False,
            )
        return ProviderProgress(
            state="published",
            provider_job_id=post_id,
            external_post_id=confirmed_id,
            external_post_url=str(payload.get("permalink_url") or "").strip() or None,
            detail="Facebook confirmed the Page post remains available.",
            metadata=dict(provider_metadata or {}),
        )


__all__ = ["FacebookPagesAdapter"]
