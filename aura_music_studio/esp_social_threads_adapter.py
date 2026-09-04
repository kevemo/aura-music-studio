from __future__ import annotations

from typing import Any

import httpx

from .esp_social_provider_adapters import (
    ADAPTERS,
    ProviderAdapterError,
    ProviderProgress,
    _caption,
    _json_or_error,
    _network_error,
)
from .esp_social_publish_media import ResolvedPublishMedia
from .esp_social_threads_oauth import install_threads_oauth_token_extension
from .social_management import PlatformVariant, SocialConnection, SocialContent


class ThreadsGraphAdapter:
    """Bounded Threads single-post publisher using Meta's official container flow.

    The adapter intentionally implements only the Social Manager's existing ``post`` surface.
    A post may be text-only or contain exactly one approved public-HTTPS image/video. Carousel,
    reply, quote and location flows remain planning-only until they receive their own reviewed
    contracts. Provider-confirmed publication is required before ``published`` is returned.
    """

    name = "threads_graph"
    platform = "threads"
    base = "https://graph.threads.net"

    def __init__(self, *, timeout: float = 45.0):
        self.timeout = timeout

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        clean = (token or "").strip()
        if not clean:
            raise ProviderAdapterError("Threads access token is unavailable")
        return {"Authorization": f"Bearer {clean}"}

    @staticmethod
    def _container_params(
        variant: PlatformVariant,
        media: list[ResolvedPublishMedia],
    ) -> dict[str, str]:
        if variant.content_type != "post":
            raise ProviderAdapterError(
                "Threads worker currently enables automatic publishing for post variants only"
            )
        if len(media) > 1:
            raise ProviderAdapterError(
                "Threads single-post publishing currently accepts at most one approved media asset"
            )

        text = _caption(variant)[:500]
        params: dict[str, str] = {"text": text}
        if not media:
            if not text:
                raise ProviderAdapterError("Threads text-only posts require non-empty post text")
            params["media_type"] = "TEXT"
            return params

        item = media[0]
        public_url = (item.public_url or "").strip()
        if not public_url:
            raise ProviderAdapterError(
                "Threads image/video publishing requires a provider-accessible public HTTPS media URL"
            )
        if item.kind == "image":
            params.update({"media_type": "IMAGE", "image_url": public_url})
        elif item.kind == "video":
            params.update({"media_type": "VIDEO", "video_url": public_url})
        else:
            raise ProviderAdapterError(
                "Threads single-post adapter supports text, one image, or one video in this release"
            )
        alt_text = str(variant.metadata.get("threads_alt_text") or "").strip()
        if alt_text:
            params["alt_text"] = alt_text[:1000]
        return params

    def start(
        self,
        *,
        token: str,
        connection: SocialConnection,
        content: SocialContent,
        variant: PlatformVariant,
        media: list[ResolvedPublishMedia],
    ) -> ProviderProgress:
        params = self._container_params(variant, media)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base}/me/threads",
                    headers=self._headers(token),
                    params=params,
                )
        except httpx.RequestError as exc:
            raise _network_error("Threads", exc) from exc
        payload = _json_or_error(response, "Threads")
        container_id = str(payload.get("id") or "").strip()
        if not container_id:
            raise ProviderAdapterError("Threads did not return a media container ID")
        return ProviderProgress(
            state="pending",
            provider_job_id=container_id,
            detail="Threads media container created; waiting for provider readiness before publish.",
            metadata={
                "stage": "container",
                "media_type": params.get("media_type", "TEXT"),
            },
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
        container_id = (provider_job_id or "").strip()
        if not container_id or len(container_id) > 200:
            raise ProviderAdapterError("Stored Threads container ID is invalid")
        headers = self._headers(token)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(
                    f"{self.base}/{container_id}",
                    headers=headers,
                    params={"fields": "id,status"},
                )
                payload = _json_or_error(response, "Threads")
                status = str(payload.get("status") or "").strip().upper()
                if status in {"ERROR", "EXPIRED"}:
                    return ProviderProgress(
                        state="failed",
                        provider_job_id=container_id,
                        detail=f"Threads container status: {status}",
                        retryable=False,
                    )
                if status == "PUBLISHED":
                    return ProviderProgress(
                        state="published",
                        provider_job_id=container_id,
                        external_post_id=container_id,
                        detail="Threads confirmed the stored container is already published.",
                        metadata={
                            **dict(provider_metadata or {}),
                            "stage": "published",
                            "provider_confirmation_kind": "published_container_id",
                        },
                    )
                if status != "FINISHED":
                    return ProviderProgress(
                        state="pending",
                        provider_job_id=container_id,
                        detail=f"Threads container status: {status or 'IN_PROGRESS'}",
                        metadata={
                            **dict(provider_metadata or {}),
                            "stage": "container",
                        },
                    )
                publish = client.post(
                    f"{self.base}/me/threads_publish",
                    headers=headers,
                    params={"creation_id": container_id},
                )
        except httpx.RequestError as exc:
            raise _network_error("Threads", exc) from exc
        published = _json_or_error(publish, "Threads")
        post_id = str(published.get("id") or "").strip()
        if not post_id:
            raise ProviderAdapterError("Threads publish response did not contain a post ID")
        return ProviderProgress(
            state="published",
            provider_job_id=container_id,
            external_post_id=post_id,
            detail="Threads confirmed publication.",
            metadata={
                **dict(provider_metadata or {}),
                "stage": "published",
                "provider_confirmation_kind": "post_id",
            },
        )


def register_threads_adapter(registry: dict[str, Any] | None = None) -> ThreadsGraphAdapter:
    target = ADAPTERS if registry is None else registry
    existing = target.get(ThreadsGraphAdapter.name)
    if isinstance(existing, ThreadsGraphAdapter):
        return existing
    adapter = ThreadsGraphAdapter()
    target[adapter.name] = adapter
    return adapter


# The worker imports this module before resolving encrypted social-oauth:// credentials.
# Install only the Threads-specific refresh extension, then register the bounded provider adapter.
install_threads_oauth_token_extension()
register_threads_adapter()


__all__ = ["ThreadsGraphAdapter", "register_threads_adapter"]
