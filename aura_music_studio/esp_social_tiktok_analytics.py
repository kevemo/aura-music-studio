from __future__ import annotations

import os
from typing import Any

import requests
from fastapi import APIRouter, HTTPException, Request

from .esp_niche import require_esp_social_member
from .esp_social_provider_adapters import ProviderAdapterError
from .esp_social_provider_analytics import (
    ProviderAnalyticsError,
    ProviderAnalyticsService,
    ProviderAnalyticsSnapshotStore,
)
from .esp_social_secret_refs import resolve_social_token
from .social_management import ActivityEvent, SocialConnection, SocialHouseStore, utc_now

router = APIRouter(tags=["esp-social-tiktok-analytics"])

_TIKTOK_LIST_VIDEOS = "https://open.tiktokapis.com/v2/video/list/"
_TIKTOK_VIDEO_LIST_SCOPE = "video.list"
_TIKTOK_FIELDS = (
    "id",
    "title",
    "video_description",
    "create_time",
    "duration",
    "share_url",
    "like_count",
    "comment_count",
    "share_count",
    "view_count",
    "is_aigc",
)
_RETRYABLE_HTTP = {408, 425, 429}


def _max_items() -> int:
    try:
        value = int(os.getenv("AURA_SOCIAL_ANALYTICS_MAX_ITEMS", "50"))
    except ValueError:
        value = 50
    return max(1, min(value, 200))


def _timeout() -> float:
    try:
        value = float(os.getenv("AURA_SOCIAL_ANALYTICS_HTTP_TIMEOUT", "25"))
    except ValueError:
        value = 25.0
    return max(5.0, min(value, 120.0))


def _scopes(connection: SocialConnection) -> set[str]:
    raw = connection.metadata.get("oauth_scopes") or []
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    return {str(item).strip() for item in raw if str(item).strip()}


def _connection(house) -> SocialConnection | None:
    rows = [
        item
        for item in house.connections
        if item.platform == "tiktok"
        and item.state == "connected"
        and _TIKTOK_VIDEO_LIST_SCOPE in _scopes(item)
        and bool(item.token_secret_ref)
    ]
    rows.sort(
        key=lambda item: (item.metadata.get("oauth_verified") is True, bool(item.token_secret_ref)),
        reverse=True,
    )
    return rows[0] if rows else None


def enhanced_capabilities(house) -> dict:
    caps = ProviderAnalyticsService().capabilities(house)
    connection = _connection(house)
    if connection:
        caps["tiktok"] = {
            "platform": "tiktok",
            "status": "ready",
            "ready": True,
            "reason": "Public TikTok video statistics can sync through the authorised Display API video.list connection.",
            "required_scopes": [_TIKTOK_VIDEO_LIST_SCOPE],
            "connection_id": connection.id,
            "account_label": connection.account_label,
            "tokens_exposed": False,
        }
        caps["truthful_state"] = (
            "YouTube owned-channel and TikTok public-video statistics are active when their exact authorised scopes are present. "
            "Instagram Insights remains pending until its current Meta permission path is deliberately activated."
        )
    return caps


def _safe_payload(response: requests.Response, action: str) -> dict:
    status = int(response.status_code)
    if not response.ok:
        if status in {401, 403}:
            raise ProviderAnalyticsError(
                "TikTok analytics authorization is no longer valid or lacks video.list; reconnect or re-authorise the account",
                reconnect=True,
            )
        raise ProviderAnalyticsError(
            f"TikTok analytics {action} failed with HTTP {status}",
            retryable=status >= 500 or status in _RETRYABLE_HTTP,
        )
    try:
        payload = response.json()
    except Exception as exc:
        raise ProviderAnalyticsError(
            f"TikTok analytics {action} returned an invalid response",
            retryable=True,
        ) from exc
    if not isinstance(payload, dict):
        raise ProviderAnalyticsError(
            f"TikTok analytics {action} returned an invalid response",
            retryable=True,
        )
    error = payload.get("error") or {}
    code = str(error.get("code") or "").strip()
    lowered = code.lower()
    if code and lowered not in {"ok", "0"}:
        reconnect = "token" in lowered or "scope" in lowered or "auth" in lowered
        raise ProviderAnalyticsError(
            f"TikTok analytics {action} returned provider error {code[:120]}",
            reconnect=reconnect,
            retryable=lowered in {"rate_limit_exceeded", "internal_error", "server_error"},
        )
    return payload


def _list_page(token: str, *, max_count: int, cursor: int | None = None) -> dict:
    body: dict[str, Any] = {"max_count": max(1, min(int(max_count), 20))}
    if cursor is not None:
        body["cursor"] = int(cursor)
    try:
        response = requests.post(
            _TIKTOK_LIST_VIDEOS,
            params={"fields": ",".join(_TIKTOK_FIELDS)},
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=_timeout(),
        )
    except requests.RequestException as exc:
        raise ProviderAnalyticsError(
            "TikTok analytics video listing could not reach the provider",
            retryable=True,
        ) from exc
    return _safe_payload(response, "video listing")


class TikTokAnalyticsService:
    def __init__(self, snapshots: ProviderAnalyticsSnapshotStore | None = None):
        self.snapshots = snapshots or ProviderAnalyticsSnapshotStore()

    @staticmethod
    def _linked_content_id(house, video_id: str) -> str | None:
        for content in house.content:
            for variant in content.variants:
                if variant.platform == "tiktok" and str(variant.external_post_id or "") == video_id:
                    return content.id
        return None

    @staticmethod
    def _integer(video: dict, name: str) -> int:
        try:
            return int(video.get(name) or 0)
        except (TypeError, ValueError):
            return 0

    def sync(self, *, user_id: str, house) -> dict:
        capability = enhanced_capabilities(house)["tiktok"]
        if not capability["ready"]:
            raise ProviderAnalyticsError(str(capability["reason"]))
        connection = next(
            item for item in house.connections if item.id == capability["connection_id"]
        )
        try:
            token = resolve_social_token(connection.token_secret_ref)
        except ProviderAdapterError as exc:
            raise ProviderAnalyticsError(
                "The TikTok OAuth credential could not be refreshed safely; retry later"
                if exc.retryable
                else "The TikTok OAuth credential is no longer usable; reconnect or re-authorise the account",
                retryable=bool(exc.retryable),
                reconnect=not bool(exc.retryable),
            ) from exc
        except (LookupError, ValueError, PermissionError) as exc:
            raise ProviderAnalyticsError(
                "The TikTok credential is unavailable; reconnect or re-authorise the account",
                reconnect=True,
            ) from exc

        maximum = _max_items()
        videos: list[dict] = []
        seen_ids: set[str] = set()
        cursor: int | None = None
        seen_cursors: set[int] = set()
        while len(videos) < maximum:
            payload = _list_page(
                token,
                max_count=min(20, maximum - len(videos)),
                cursor=cursor,
            )
            data = payload.get("data") or {}
            rows = data.get("videos") or []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                video_id = str(row.get("id") or "").strip()
                if not video_id or video_id in seen_ids:
                    continue
                seen_ids.add(video_id)
                videos.append(row)
                if len(videos) >= maximum:
                    break
            if not bool(data.get("has_more")) or len(videos) >= maximum:
                break
            try:
                next_cursor = int(data.get("cursor"))
            except (TypeError, ValueError):
                break
            if next_cursor in seen_cursors or next_cursor == cursor:
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        inserted = 0
        updated = 0
        synced_ids: list[str] = []
        for video in videos:
            video_id = str(video.get("id") or "").strip()
            if not video_id:
                continue
            title = str(video.get("title") or video.get("video_description") or f"TikTok {video_id}")[:120]
            metrics = {
                "views": self._integer(video, "view_count"),
                "likes": self._integer(video, "like_count"),
                "comments": self._integer(video, "comment_count"),
                "shares": self._integer(video, "share_count"),
                "duration_seconds": self._integer(video, "duration"),
                "create_time": self._integer(video, "create_time"),
                "is_aigc": int(bool(video.get("is_aigc"))),
            }
            _snapshot, was_inserted = self.snapshots.upsert(
                user_id=user_id,
                space_id=house.id,
                platform="tiktok",
                period_label=f"TikTok · {title}",
                content_id=self._linked_content_id(house, video_id),
                metrics=metrics,
                source_ref=f"tiktok:video:{video_id}",
            )
            inserted += int(was_inserted)
            updated += int(not was_inserted)
            synced_ids.append(video_id)

        connection.supports_analytics = True
        connection.metadata["analytics_adapter"] = "tiktok_display_api_video_list"
        connection.metadata["analytics_last_synced_at"] = utc_now()
        connection.updated_at = utc_now()
        house.activity.append(
            ActivityEvent(
                actor=user_id,
                action="provider_analytics_sync",
                entity_type="connection",
                entity_id=connection.id,
                detail=f"tiktok:inserted={inserted}:updated={updated}:videos={len(synced_ids)}",
            )
        )
        SocialHouseStore().save(house)
        return {
            "platform": "tiktok",
            "connection_id": connection.id,
            "account_label": connection.account_label,
            "videos_seen": len(videos),
            "snapshots_synced": len(synced_ids),
            "inserted": inserted,
            "updated": updated,
            "tokens_exposed": False,
            "public_video_statistics": True,
            "advanced_tiktok_analytics": False,
            "detail": (
                "Synced public video statistics from TikTok's Display API into Aura Social Intelligence. "
                "This does not claim private retention, audience-demographic, LIVE or revenue analytics."
            ),
        }


def _load_house(space_id: str):
    try:
        return SocialHouseStore().load(space_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _raise_provider(exc: ProviderAnalyticsError):
    if exc.reconnect:
        raise HTTPException(409, str(exc)) from exc
    if exc.retryable:
        raise HTTPException(503, str(exc)) from exc
    raise HTTPException(409, str(exc)) from exc


@router.get("/spaces/{space_id}/provider-analytics/capabilities")
def provider_analytics_capabilities_with_tiktok(space_id: str, request: Request):
    require_esp_social_member(request)
    return enhanced_capabilities(_load_house(space_id))


@router.post("/spaces/{space_id}/provider-analytics/tiktok/sync")
def sync_tiktok_provider_analytics_live(space_id: str, request: Request):
    member, _membership, _profile = require_esp_social_member(request)
    house = _load_house(space_id)
    try:
        return TikTokAnalyticsService().sync(user_id=member.user_id, house=house)
    except ProviderAnalyticsError as exc:
        _raise_provider(exc)


__all__ = [
    "TikTokAnalyticsService",
    "enhanced_capabilities",
    "router",
]
