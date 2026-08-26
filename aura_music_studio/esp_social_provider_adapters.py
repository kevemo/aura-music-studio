from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, Field

from .esp_social_publish_media import ResolvedPublishMedia
from .social_management import PlatformVariant, SocialConnection, SocialContent

ProviderState = Literal["pending", "published", "failed"]


class ProviderProgress(BaseModel):
    state: ProviderState
    provider_job_id: str | None = None
    external_post_id: str | None = None
    external_post_url: str | None = None
    detail: str = ""
    retryable: bool = False
    metadata: dict = Field(default_factory=dict)


class ProviderAdapterError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class SocialProviderAdapter(Protocol):
    name: str
    platform: str

    def start(
        self,
        *,
        token: str,
        connection: SocialConnection,
        content: SocialContent,
        variant: PlatformVariant,
        media: list[ResolvedPublishMedia],
    ) -> ProviderProgress: ...

    def poll(
        self,
        *,
        token: str,
        connection: SocialConnection,
        content: SocialContent,
        variant: PlatformVariant,
        provider_job_id: str,
        provider_metadata: dict,
    ) -> ProviderProgress: ...


def _caption(variant: PlatformVariant) -> str:
    value = variant.caption.strip()
    tags = " ".join(f"#{tag.lstrip('#')}" for tag in variant.hashtags if tag.strip())
    return " ".join(part for part in (value, tags) if part).strip()


def _json_or_error(response: httpx.Response, provider: str) -> dict:
    try:
        payload = response.json()
    except Exception as exc:
        raise ProviderAdapterError(
            f"{provider} returned a non-JSON response ({response.status_code})",
            retryable=response.status_code >= 500,
        ) from exc
    if response.is_error:
        detail = payload.get("error") if isinstance(payload, dict) else payload
        raise ProviderAdapterError(
            f"{provider} request failed ({response.status_code}): {detail}",
            retryable=response.status_code >= 500 or response.status_code in {408, 425, 429},
        )
    return payload if isinstance(payload, dict) else {}


def _network_error(provider: str, exc: httpx.RequestError) -> ProviderAdapterError:
    return ProviderAdapterError(f"{provider} network request failed: {exc}", retryable=True)


class InstagramGraphAdapter:
    name = "instagram_graph"
    platform = "instagram"

    def __init__(self, *, timeout: float = 30.0):
        self.timeout = timeout

    @staticmethod
    def _base() -> str:
        version = os.getenv("AURA_META_GRAPH_VERSION", "").strip()
        if not version:
            raise ProviderAdapterError(
                "AURA_META_GRAPH_VERSION must be configured before Instagram publishing is enabled"
            )
        if not version.startswith("v") or not version[1:].replace(".", "").isdigit():
            raise ProviderAdapterError("AURA_META_GRAPH_VERSION is invalid")
        return f"https://graph.facebook.com/{version}"

    @staticmethod
    def _ig_user_id(connection: SocialConnection) -> str:
        value = (
            connection.account_external_id
            or str(connection.metadata.get("instagram_user_id") or "")
        ).strip()
        if not value:
            raise ProviderAdapterError("Instagram professional account ID is not configured")
        return value

    def start(
        self,
        *,
        token: str,
        connection: SocialConnection,
        content: SocialContent,
        variant: PlatformVariant,
        media: list[ResolvedPublishMedia],
    ) -> ProviderProgress:
        if len(media) != 1:
            raise ProviderAdapterError(
                "Instagram publishing currently requires exactly one approved media asset"
            )
        item = media[0]
        if not item.public_url:
            raise ProviderAdapterError(
                "Instagram Graph publishing requires a provider-accessible HTTPS media URL"
            )
        ig_user_id = self._ig_user_id(connection)
        fields: dict[str, str] = {"access_token": token, "caption": _caption(variant)}
        if variant.content_type in {"reel", "video"} or item.kind == "video":
            fields.update({"media_type": "REELS", "video_url": item.public_url})
        elif item.kind == "image":
            fields["image_url"] = item.public_url
        else:
            raise ProviderAdapterError(
                "Instagram adapter supports image posts and reels/video in this release"
            )
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(f"{self._base()}/{ig_user_id}/media", data=fields)
        except httpx.RequestError as exc:
            raise _network_error("Instagram", exc) from exc
        payload = _json_or_error(response, "Instagram")
        creation_id = str(payload.get("id") or "").strip()
        if not creation_id:
            raise ProviderAdapterError("Instagram did not return a media container ID")
        return ProviderProgress(
            state="pending",
            provider_job_id=creation_id,
            detail="Instagram media container created; waiting for provider processing before publish.",
            metadata={"stage": "container"},
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
        ig_user_id = self._ig_user_id(connection)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(
                    f"{self._base()}/{provider_job_id}",
                    params={"fields": "status_code,status", "access_token": token},
                )
                payload = _json_or_error(response, "Instagram")
                status = str(payload.get("status_code") or "").upper()
                if status in {"ERROR", "EXPIRED"}:
                    return ProviderProgress(
                        state="failed",
                        provider_job_id=provider_job_id,
                        detail=str(payload.get("status") or status),
                        retryable=False,
                    )
                if status not in {"FINISHED", "PUBLISHED"}:
                    return ProviderProgress(
                        state="pending",
                        provider_job_id=provider_job_id,
                        detail=f"Instagram container status: {status or 'IN_PROGRESS'}",
                        metadata={"stage": "container"},
                    )
                publish = client.post(
                    f"{self._base()}/{ig_user_id}/media_publish",
                    data={"creation_id": provider_job_id, "access_token": token},
                )
        except httpx.RequestError as exc:
            raise _network_error("Instagram", exc) from exc
        published = _json_or_error(publish, "Instagram")
        post_id = str(published.get("id") or "").strip()
        if not post_id:
            raise ProviderAdapterError("Instagram publish response did not contain a post ID")
        return ProviderProgress(
            state="published",
            provider_job_id=provider_job_id,
            external_post_id=post_id,
            detail="Instagram confirmed media publication.",
            metadata={"stage": "published"},
        )


class TikTokContentPostingAdapter:
    name = "tiktok_content_posting"
    platform = "tiktok"
    base = "https://open.tiktokapis.com"

    def __init__(self, *, timeout: float = 60.0):
        self.timeout = timeout

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    def _creator_info(self, client: httpx.Client, token: str) -> dict:
        response = client.post(
            f"{self.base}/v2/post/publish/creator_info/query/",
            headers=self._headers(token),
            json={},
        )
        payload = _json_or_error(response, "TikTok")
        if payload.get("error", {}).get("code") not in {None, "ok", ""}:
            raise ProviderAdapterError(f"TikTok creator-info error: {payload.get('error')}")
        return payload.get("data") or {}

    @staticmethod
    def _post_info(variant: PlatformVariant, creator: dict) -> dict:
        if variant.metadata.get("provider_user_consent") is not True:
            raise ProviderAdapterError(
                "TikTok Direct Post requires explicit user consent recorded on the platform variant"
            )
        privacy = str(variant.metadata.get("privacy_level") or "").strip()
        options = [str(value) for value in creator.get("privacy_level_options") or []]
        if not privacy:
            raise ProviderAdapterError(
                "TikTok privacy_level must be explicitly selected by the creator before Direct Post"
            )
        if options and privacy not in options:
            raise ProviderAdapterError(
                "Selected TikTok privacy_level is not currently available for this creator"
            )
        return {
            "title": _caption(variant),
            "privacy_level": privacy,
            "disable_duet": bool(variant.metadata.get("disable_duet", False)),
            "disable_comment": bool(variant.metadata.get("disable_comment", False)),
            "disable_stitch": bool(variant.metadata.get("disable_stitch", False)),
            "is_aigc": bool(variant.metadata.get("is_aigc", False)),
        }

    def _upload_file(
        self,
        client: httpx.Client,
        upload_url: str,
        item: ResolvedPublishMedia,
        *,
        video_size: int,
        chunk_size: int,
    ) -> None:
        path = Path(item.local_path or "")
        if not path.is_file():
            raise ProviderAdapterError("TikTok FILE_UPLOAD source is missing")
        with path.open("rb") as handle:
            start = 0
            while start < video_size:
                chunk = handle.read(min(chunk_size, video_size - start))
                if not chunk:
                    raise ProviderAdapterError(
                        "TikTok media file ended before the declared upload size"
                    )
                end = start + len(chunk) - 1
                try:
                    response = client.put(
                        upload_url,
                        headers={
                            "Content-Type": item.mime_type or "video/mp4",
                            "Content-Length": str(len(chunk)),
                            "Content-Range": f"bytes {start}-{end}/{video_size}",
                        },
                        content=chunk,
                    )
                except httpx.RequestError as exc:
                    raise _network_error("TikTok", exc) from exc
                if response.status_code < 200 or response.status_code >= 300:
                    raise ProviderAdapterError(
                        f"TikTok upload failed ({response.status_code})",
                        retryable=(
                            response.status_code >= 500
                            or response.status_code in {408, 425, 429}
                        ),
                    )
                start = end + 1

    def start(
        self,
        *,
        token: str,
        connection: SocialConnection,
        content: SocialContent,
        variant: PlatformVariant,
        media: list[ResolvedPublishMedia],
    ) -> ProviderProgress:
        if variant.content_type != "video":
            raise ProviderAdapterError(
                "TikTok worker currently enables Direct Post for video variants only"
            )
        if len(media) != 1 or media[0].kind != "video":
            raise ProviderAdapterError(
                "TikTok Direct Post requires exactly one approved video asset"
            )
        item = media[0]
        try:
            with httpx.Client(timeout=self.timeout) as client:
                creator = self._creator_info(client, token)
                source_info: dict
                if item.public_url:
                    source_info = {"source": "PULL_FROM_URL", "video_url": item.public_url}
                elif item.local_path:
                    size = Path(item.local_path).stat().st_size
                    if size <= 0:
                        raise ProviderAdapterError("TikTok media file is empty")
                    max_chunk = 64 * 1024 * 1024
                    preferred = 10 * 1024 * 1024
                    chunk_size = min(size, preferred, max_chunk)
                    total_chunks = max(1, math.ceil(size / chunk_size))
                    source_info = {
                        "source": "FILE_UPLOAD",
                        "video_size": size,
                        "chunk_size": chunk_size,
                        "total_chunk_count": total_chunks,
                    }
                else:
                    raise ProviderAdapterError(
                        "TikTok media is neither a provider-accessible URL nor a safe local file"
                    )
                response = client.post(
                    f"{self.base}/v2/post/publish/video/init/",
                    headers=self._headers(token),
                    json={
                        "post_info": self._post_info(variant, creator),
                        "source_info": source_info,
                    },
                )
                payload = _json_or_error(response, "TikTok")
                error = payload.get("error") or {}
                if error.get("code") not in {None, "ok", ""}:
                    raise ProviderAdapterError(f"TikTok Direct Post init failed: {error}")
                data = payload.get("data") or {}
                publish_id = str(data.get("publish_id") or "").strip()
                if not publish_id:
                    raise ProviderAdapterError("TikTok did not return a publish_id")
                if source_info["source"] == "FILE_UPLOAD":
                    upload_url = str(data.get("upload_url") or "").strip()
                    if not upload_url:
                        raise ProviderAdapterError(
                            "TikTok did not return an upload_url for FILE_UPLOAD"
                        )
                    self._upload_file(
                        client,
                        upload_url,
                        item,
                        video_size=int(source_info["video_size"]),
                        chunk_size=int(source_info["chunk_size"]),
                    )
        except httpx.RequestError as exc:
            raise _network_error("TikTok", exc) from exc
        return ProviderProgress(
            state="pending",
            provider_job_id=publish_id,
            detail="TikTok accepted the Direct Post job; provider status confirmation is pending.",
            metadata={"source": source_info["source"]},
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
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base}/v2/post/publish/status/fetch/",
                    headers=self._headers(token),
                    json={"publish_id": provider_job_id},
                )
        except httpx.RequestError as exc:
            raise _network_error("TikTok", exc) from exc
        payload = _json_or_error(response, "TikTok")
        error = payload.get("error") or {}
        if error.get("code") not in {None, "ok", ""}:
            raise ProviderAdapterError(
                f"TikTok post-status check failed: {error}", retryable=True
            )
        data = payload.get("data") or {}
        status = str(data.get("status") or "").upper()
        if status == "FAILED":
            fail_reason = str(data.get("fail_reason") or "TikTok provider reported FAILED")
            retryable = fail_reason.lower() in {
                "internal",
                "video_pull_failed",
                "photo_pull_failed",
            }
            return ProviderProgress(
                state="failed",
                provider_job_id=provider_job_id,
                detail=fail_reason,
                retryable=retryable,
            )
        if status != "PUBLISH_COMPLETE":
            return ProviderProgress(
                state="pending",
                provider_job_id=provider_job_id,
                detail=f"TikTok status: {status or 'PROCESSING'}",
            )
        post_ids = (
            data.get("publicaly_available_post_id")
            or data.get("publicly_available_post_id")
            or []
        )
        post_id = (
            str(post_ids[0]).strip()
            if isinstance(post_ids, list) and post_ids
            else provider_job_id
        )
        return ProviderProgress(
            state="published",
            provider_job_id=provider_job_id,
            external_post_id=post_id,
            detail="TikTok confirmed PUBLISH_COMPLETE.",
            metadata={
                "provider_confirmation_kind": (
                    "public_post_id" if post_id != provider_job_id else "publish_id"
                )
            },
        )


class YouTubeDataApiAdapter:
    name = "youtube_data_v3"
    platform = "youtube"
    upload_endpoint = "https://www.googleapis.com/upload/youtube/v3/videos"
    video_endpoint = "https://www.googleapis.com/youtube/v3/videos"

    def __init__(self, *, timeout: float = 120.0):
        self.timeout = timeout

    def start(
        self,
        *,
        token: str,
        connection: SocialConnection,
        content: SocialContent,
        variant: PlatformVariant,
        media: list[ResolvedPublishMedia],
    ) -> ProviderProgress:
        if len(media) != 1 or not media[0].local_path:
            raise ProviderAdapterError(
                "YouTube resumable upload currently requires exactly one approved, materialized local video file"
            )
        item = media[0]
        if item.kind != "video":
            raise ProviderAdapterError("YouTube publishing requires a video asset")
        path = Path(item.local_path)
        if not path.is_file():
            raise ProviderAdapterError("YouTube media file is missing")
        privacy = str(variant.metadata.get("youtube_privacy") or "private").strip().lower()
        if privacy not in {"private", "unlisted", "public"}:
            raise ProviderAdapterError("youtube_privacy must be private, unlisted or public")
        metadata = {
            "snippet": {
                "title": str(variant.metadata.get("youtube_title") or content.title)[:100],
                "description": _caption(variant)[:5000],
            },
            "status": {"privacyStatus": privacy},
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(path.stat().st_size),
            "X-Upload-Content-Type": item.mime_type or "video/*",
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                init = client.post(
                    self.upload_endpoint,
                    params={"uploadType": "resumable", "part": "snippet,status"},
                    headers=headers,
                    json=metadata,
                )
                if init.is_error:
                    _json_or_error(init, "YouTube")
                location = init.headers.get("Location", "").strip()
                if not location:
                    raise ProviderAdapterError(
                        "YouTube resumable upload did not return an upload Location"
                    )
                with path.open("rb") as handle:
                    upload = client.put(
                        location,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": item.mime_type or "application/octet-stream",
                            "Content-Length": str(path.stat().st_size),
                        },
                        content=handle,
                    )
                payload = _json_or_error(upload, "YouTube")
        except httpx.RequestError as exc:
            raise _network_error("YouTube", exc) from exc
        video_id = str(payload.get("id") or "").strip()
        if not video_id:
            raise ProviderAdapterError("YouTube upload response did not contain a video ID")
        return ProviderProgress(
            state="pending",
            provider_job_id=video_id,
            detail="YouTube accepted the upload; video processing confirmation is pending.",
            metadata={"privacy": privacy},
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
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(
                    self.video_endpoint,
                    params={"part": "processingDetails,status", "id": provider_job_id},
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.RequestError as exc:
            raise _network_error("YouTube", exc) from exc
        payload = _json_or_error(response, "YouTube")
        items = payload.get("items") or []
        if not items:
            return ProviderProgress(
                state="failed",
                provider_job_id=provider_job_id,
                detail="YouTube video resource is no longer available",
                retryable=False,
            )
        item = items[0]
        processing = str(
            (item.get("processingDetails") or {}).get("processingStatus") or "processing"
        ).lower()
        if processing in {"failed", "terminated"}:
            reason = str(
                (item.get("processingDetails") or {}).get("processingFailureReason")
                or processing
            )
            return ProviderProgress(
                state="failed",
                provider_job_id=provider_job_id,
                detail=f"YouTube processing {processing}: {reason}",
                retryable=False,
            )
        if processing != "succeeded":
            return ProviderProgress(
                state="pending",
                provider_job_id=provider_job_id,
                detail=f"YouTube processing status: {processing}",
            )
        return ProviderProgress(
            state="published",
            provider_job_id=provider_job_id,
            external_post_id=provider_job_id,
            external_post_url=f"https://www.youtube.com/watch?v={provider_job_id}",
            detail="YouTube confirmed video processing succeeded.",
        )


ADAPTERS: dict[str, SocialProviderAdapter] = {
    InstagramGraphAdapter.name: InstagramGraphAdapter(),
    TikTokContentPostingAdapter.name: TikTokContentPostingAdapter(),
    YouTubeDataApiAdapter.name: YouTubeDataApiAdapter(),
}


def provider_adapter(name: str) -> SocialProviderAdapter:
    adapter = ADAPTERS.get((name or "").strip())
    if adapter is None:
        raise ProviderAdapterError(
            f"Unknown or unavailable social publishing adapter: {name}"
        )
    return adapter


__all__ = [
    "ADAPTERS",
    "ProviderAdapterError",
    "ProviderProgress",
    "SocialProviderAdapter",
    "provider_adapter",
]
