from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

import requests
from fastapi import APIRouter, HTTPException, Request

from .accounts import AccountStore
from .esp_niche import require_esp_social_member
from .esp_social_intelligence import EspSocialIntelligenceStore
from .esp_social_provider_adapters import ProviderAdapterError
from .esp_social_secret_refs import resolve_social_token
from .social_management import ActivityEvent, SocialConnection, SocialHouseStore, utc_now

router = APIRouter(tags=["esp-social-provider-analytics"])

_YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
_TIKTOK_VIDEO_LIST_SCOPE = "video.list"
_YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
_RETRYABLE_HTTP = {408, 425, 429}


class ProviderAnalyticsError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, reconnect: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.reconnect = reconnect


@dataclass(frozen=True)
class AnalyticsCapability:
    platform: str
    status: str
    ready: bool
    reason: str
    required_scopes: tuple[str, ...] = ()
    connection_id: str | None = None
    account_label: str = ""

    def as_dict(self) -> dict:
        return {
            "platform": self.platform,
            "status": self.status,
            "ready": self.ready,
            "reason": self.reason,
            "required_scopes": list(self.required_scopes),
            "connection_id": self.connection_id,
            "account_label": self.account_label,
            "tokens_exposed": False,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _connected(house, platform: str) -> list[SocialConnection]:
    return [item for item in house.connections if item.platform == platform and item.state == "connected"]


def _best_connection(house, platform: str, required_scope: str | None = None) -> SocialConnection | None:
    rows = _connected(house, platform)
    # Fail closed: a call that asks for a scope must never fall back to an otherwise
    # connected account that did not actually grant that permission.
    if required_scope:
        rows = [item for item in rows if required_scope in _scopes(item)]
    rows.sort(
        key=lambda item: (item.metadata.get("oauth_verified") is True, bool(item.token_secret_ref)),
        reverse=True,
    )
    return rows[0] if rows else None


def _safe_json(response: requests.Response, provider: str, action: str) -> dict:
    if not response.ok:
        status = int(response.status_code)
        if status in {401, 403}:
            raise ProviderAnalyticsError(
                f"{provider} analytics authorization is no longer valid or lacks permission; reconnect the account",
                reconnect=True,
            )
        raise ProviderAnalyticsError(
            f"{provider} analytics {action} failed with HTTP {status}",
            retryable=status >= 500 or status in _RETRYABLE_HTTP,
        )
    try:
        payload = response.json()
    except Exception as exc:
        raise ProviderAnalyticsError(
            f"{provider} analytics {action} returned an invalid response",
            retryable=True,
        ) from exc
    return payload if isinstance(payload, dict) else {}


def _get_json(url: str, *, token: str, params: dict, provider: str, action: str) -> dict:
    try:
        response = requests.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=_timeout(),
        )
    except requests.RequestException as exc:
        raise ProviderAnalyticsError(
            f"{provider} analytics {action} could not reach the provider",
            retryable=True,
        ) from exc
    return _safe_json(response, provider, action)


class ProviderAnalyticsSnapshotStore:
    """Upsert official provider data into Aura's canonical ESP analytics table."""

    def __init__(self):
        EspSocialIntelligenceStore()
        self.db_path = AccountStore().db_path

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _clean_metrics(metrics: dict) -> dict:
        clean: dict[str, int | float | str] = {}
        for key, value in metrics.items():
            name = str(key).strip()[:100]
            if not name:
                continue
            if isinstance(value, bool):
                clean[name] = int(value)
            elif isinstance(value, (int, float)):
                clean[name] = value
            elif isinstance(value, str):
                clean[name] = value[:300]
        return clean

    def upsert(
        self,
        *,
        user_id: str,
        space_id: str,
        platform: str,
        period_label: str,
        content_id: str | None,
        metrics: dict,
        source_ref: str,
    ) -> tuple[dict, bool]:
        clean_metrics = self._clean_metrics(metrics)
        stamp = _now()
        with self._connect() as con:
            row = con.execute(
                """SELECT id FROM esp_social_analytics
                   WHERE user_id=? AND space_id=? AND platform=? AND source_ref=?
                   ORDER BY created_at DESC LIMIT 1""",
                (user_id, space_id, platform, source_ref),
            ).fetchone()
            inserted = row is None
            if inserted:
                analytics_id = uuid4().hex
                con.execute(
                    """INSERT INTO esp_social_analytics
                       (id,user_id,space_id,platform,period_label,content_id,metrics_json,source_ref,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        analytics_id,
                        user_id,
                        space_id,
                        platform,
                        period_label[:180],
                        (content_id or "")[:160] or None,
                        json.dumps(clean_metrics, ensure_ascii=False),
                        source_ref[:1200],
                        stamp,
                    ),
                )
            else:
                analytics_id = str(row["id"])
                con.execute(
                    """UPDATE esp_social_analytics
                       SET period_label=?,content_id=?,metrics_json=?,created_at=?
                       WHERE id=? AND user_id=?""",
                    (
                        period_label[:180],
                        (content_id or "")[:160] or None,
                        json.dumps(clean_metrics, ensure_ascii=False),
                        stamp,
                        analytics_id,
                        user_id,
                    ),
                )
            saved = con.execute(
                "SELECT * FROM esp_social_analytics WHERE id=? AND user_id=?",
                (analytics_id, user_id),
            ).fetchone()
        result = dict(saved)
        result["metrics"] = json.loads(result.pop("metrics_json") or "{}")
        return result, inserted


class ProviderAnalyticsService:
    def __init__(self, snapshot_store: ProviderAnalyticsSnapshotStore | None = None):
        self.snapshots = snapshot_store or ProviderAnalyticsSnapshotStore()

    @staticmethod
    def capabilities(house) -> dict:
        youtube = _best_connection(house, "youtube", _YOUTUBE_SCOPE)
        youtube_any = _best_connection(house, "youtube")
        if youtube and youtube.token_secret_ref:
            youtube_cap = AnalyticsCapability(
                "youtube",
                "ready",
                True,
                "Owned-channel video statistics can sync through the authorised YouTube Data API connection.",
                (_YOUTUBE_SCOPE,),
                youtube.id,
                youtube.account_label,
            )
        elif youtube_any:
            youtube_cap = AnalyticsCapability(
                "youtube",
                "blocked",
                False,
                "The connected YouTube account does not expose the required read-only scope or secure credential reference.",
                (_YOUTUBE_SCOPE,),
                youtube_any.id,
                youtube_any.account_label,
            )
        else:
            youtube_cap = AnalyticsCapability(
                "youtube",
                "not_connected",
                False,
                "Connect a YouTube account before syncing provider statistics.",
                (_YOUTUBE_SCOPE,),
            )

        tiktok = _best_connection(house, "tiktok", _TIKTOK_VIDEO_LIST_SCOPE)
        tiktok_any = _best_connection(house, "tiktok")
        if tiktok and tiktok.token_secret_ref:
            tiktok_cap = AnalyticsCapability(
                "tiktok",
                "adapter_pending",
                False,
                "The required video.list permission is present. TikTok provider analytics ingestion is not activated in this deployment yet.",
                (_TIKTOK_VIDEO_LIST_SCOPE,),
                tiktok.id,
                tiktok.account_label,
            )
        elif tiktok_any:
            tiktok_cap = AnalyticsCapability(
                "tiktok",
                "blocked",
                False,
                "TikTok video analytics requires the video.list permission; the current publishing authorization does not grant it.",
                (_TIKTOK_VIDEO_LIST_SCOPE,),
                tiktok_any.id,
                tiktok_any.account_label,
            )
        else:
            tiktok_cap = AnalyticsCapability(
                "tiktok",
                "not_connected",
                False,
                "Connect TikTok first. Analytics will still require the separate video.list permission.",
                (_TIKTOK_VIDEO_LIST_SCOPE,),
            )

        instagram_any = _best_connection(house, "instagram")
        instagram_cap = AnalyticsCapability(
            "instagram",
            "adapter_pending" if instagram_any else "not_connected",
            False,
            (
                "Instagram Insights ingestion is intentionally not activated until the deployment's current Meta insights permission path is separately reviewed and configured."
                if instagram_any
                else "Connect Instagram first; Insights ingestion remains disabled until the required Meta permissions are separately activated."
            ),
            (),
            instagram_any.id if instagram_any else None,
            instagram_any.account_label if instagram_any else "",
        )
        return {
            "youtube": youtube_cap.as_dict(),
            "tiktok": tiktok_cap.as_dict(),
            "instagram": instagram_cap.as_dict(),
            "tokens_exposed": False,
            "truthful_state": "Only YouTube owned-channel video statistics are active in this provider-sync stage. TikTok and Instagram remain blocked/pending until their additional permissions and adapters are deliberately activated.",
        }

    @staticmethod
    def _youtube_uploads_playlist(token: str, connection: SocialConnection) -> str:
        params = {"part": "contentDetails", "maxResults": 1}
        if connection.account_external_id:
            params["id"] = connection.account_external_id
        else:
            params["mine"] = "true"
        data = _get_json(
            f"{_YOUTUBE_API}/channels",
            token=token,
            params=params,
            provider="YouTube",
            action="channel lookup",
        )
        items = data.get("items") or []
        if not items:
            raise ProviderAnalyticsError("The authorised YouTube channel could not be found; reconnect the account")
        playlist = (((items[0] or {}).get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        if not playlist:
            raise ProviderAnalyticsError("The authorised YouTube channel did not expose an uploads playlist")
        return str(playlist)

    @staticmethod
    def _youtube_video_ids(token: str, playlist_id: str) -> list[str]:
        maximum = _max_items()
        result: list[str] = []
        page_token: str | None = None
        while len(result) < maximum:
            params = {
                "part": "contentDetails",
                "playlistId": playlist_id,
                "maxResults": min(50, maximum - len(result)),
            }
            if page_token:
                params["pageToken"] = page_token
            data = _get_json(
                f"{_YOUTUBE_API}/playlistItems",
                token=token,
                params=params,
                provider="YouTube",
                action="uploads listing",
            )
            for item in data.get("items") or []:
                video_id = str(((item or {}).get("contentDetails") or {}).get("videoId") or "").strip()
                if video_id and video_id not in result:
                    result.append(video_id)
                    if len(result) >= maximum:
                        break
            page_token = str(data.get("nextPageToken") or "").strip() or None
            if not page_token:
                break
        return result

    @staticmethod
    def _youtube_videos(token: str, video_ids: list[str]) -> list[dict]:
        result: list[dict] = []
        for start in range(0, len(video_ids), 50):
            batch = video_ids[start : start + 50]
            # videos.list does not accept maxResults when filtering with explicit IDs.
            data = _get_json(
                f"{_YOUTUBE_API}/videos",
                token=token,
                params={
                    "part": "snippet,statistics,contentDetails,status",
                    "id": ",".join(batch),
                },
                provider="YouTube",
                action="video statistics",
            )
            result.extend(item for item in (data.get("items") or []) if isinstance(item, dict))
        return result

    @staticmethod
    def _linked_content_id(house, video_id: str) -> str | None:
        for content in house.content:
            for variant in content.variants:
                if variant.platform == "youtube" and str(variant.external_post_id or "") == video_id:
                    return content.id
        return None

    def sync_youtube(self, *, user_id: str, house) -> dict:
        capability = self.capabilities(house)["youtube"]
        if not capability["ready"]:
            raise ProviderAnalyticsError(str(capability["reason"]))
        connection = next(item for item in house.connections if item.id == capability["connection_id"])
        try:
            token = resolve_social_token(connection.token_secret_ref)
        except ProviderAdapterError as exc:
            raise ProviderAnalyticsError(
                "The YouTube OAuth credential could not be refreshed safely; retry later"
                if exc.retryable
                else "The YouTube OAuth credential is no longer usable; reconnect the account",
                retryable=bool(exc.retryable),
                reconnect=not bool(exc.retryable),
            ) from exc
        except (LookupError, ValueError, PermissionError) as exc:
            raise ProviderAnalyticsError(
                "The YouTube credential is unavailable; reconnect the account",
                reconnect=True,
            ) from exc

        playlist_id = self._youtube_uploads_playlist(token, connection)
        video_ids = self._youtube_video_ids(token, playlist_id)
        videos = self._youtube_videos(token, video_ids) if video_ids else []
        inserted = 0
        updated = 0
        synced_ids: list[str] = []
        for video in videos:
            video_id = str(video.get("id") or "").strip()
            if not video_id:
                continue
            snippet = video.get("snippet") or {}
            statistics = video.get("statistics") or {}
            details = video.get("contentDetails") or {}
            status = video.get("status") or {}

            def integer(name: str) -> int:
                try:
                    return int(statistics.get(name) or 0)
                except (TypeError, ValueError):
                    return 0

            title = str(snippet.get("title") or f"YouTube {video_id}")[:120]
            published_at = str(snippet.get("publishedAt") or "")[:80]
            metrics = {
                "views": integer("viewCount"),
                "likes": integer("likeCount"),
                "comments": integer("commentCount"),
                "duration": str(details.get("duration") or "")[:80],
                "privacy_status": str(status.get("privacyStatus") or "")[:80],
                "published_at": published_at,
            }
            _snapshot, was_inserted = self.snapshots.upsert(
                user_id=user_id,
                space_id=house.id,
                platform="youtube",
                period_label=f"YouTube · {title}",
                content_id=self._linked_content_id(house, video_id),
                metrics=metrics,
                source_ref=f"youtube:video:{video_id}",
            )
            inserted += int(was_inserted)
            updated += int(not was_inserted)
            synced_ids.append(video_id)

        connection.supports_analytics = True
        connection.metadata["analytics_adapter"] = "youtube_data_v3_statistics"
        connection.metadata["analytics_last_synced_at"] = utc_now()
        connection.updated_at = utc_now()
        house.activity.append(
            ActivityEvent(
                actor=user_id,
                action="provider_analytics_sync",
                entity_type="connection",
                entity_id=connection.id,
                detail=f"youtube:inserted={inserted}:updated={updated}:videos={len(synced_ids)}",
            )
        )
        SocialHouseStore().save(house)
        return {
            "platform": "youtube",
            "connection_id": connection.id,
            "account_label": connection.account_label,
            "videos_seen": len(video_ids),
            "snapshots_synced": len(synced_ids),
            "inserted": inserted,
            "updated": updated,
            "tokens_exposed": False,
            "advanced_youtube_analytics": False,
            "detail": "Synced owned-channel video statistics from the YouTube Data API into Aura Social Intelligence. Audience retention, demographics and other YouTube Analytics API metrics are not claimed by this sync.",
        }


def _service() -> ProviderAnalyticsService:
    # Resolve the configured database lazily so deployment/test environment settings are
    # honored before constructing the canonical analytics store.
    return ProviderAnalyticsService()


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
def provider_analytics_capabilities(space_id: str, request: Request):
    require_esp_social_member(request)
    return _service().capabilities(_load_house(space_id))


@router.post("/spaces/{space_id}/provider-analytics/youtube/sync")
def sync_youtube_provider_analytics(space_id: str, request: Request):
    member, _membership, _profile = require_esp_social_member(request)
    house = _load_house(space_id)
    try:
        return _service().sync_youtube(user_id=member.user_id, house=house)
    except ProviderAnalyticsError as exc:
        _raise_provider(exc)


@router.post("/spaces/{space_id}/provider-analytics/tiktok/sync")
def sync_tiktok_provider_analytics(space_id: str, request: Request):
    require_esp_social_member(request)
    capability = _service().capabilities(_load_house(space_id))["tiktok"]
    raise HTTPException(
        409,
        {
            "platform": "tiktok",
            "status": capability["status"],
            "reason": capability["reason"],
            "required_scopes": capability["required_scopes"],
            "network_request_made": False,
        },
    )


@router.post("/spaces/{space_id}/provider-analytics/instagram/sync")
def sync_instagram_provider_analytics(space_id: str, request: Request):
    require_esp_social_member(request)
    capability = _service().capabilities(_load_house(space_id))["instagram"]
    raise HTTPException(
        409,
        {
            "platform": "instagram",
            "status": capability["status"],
            "reason": capability["reason"],
            "required_scopes": capability["required_scopes"],
            "network_request_made": False,
        },
    )


__all__ = [
    "AnalyticsCapability",
    "ProviderAnalyticsError",
    "ProviderAnalyticsService",
    "ProviderAnalyticsSnapshotStore",
    "router",
]
