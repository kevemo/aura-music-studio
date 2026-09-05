from __future__ import annotations

import importlib
import json
from typing import Any, Callable
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request

from . import shared_sky_live_community as live
from .owner_identity import owner_session_authorized

router = APIRouter(tags=["Shared Sky Live Integration Adapters"])


def _value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value:
        try:
            decoded = json.loads(value)
        except Exception:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str) and value:
        try:
            decoded = json.loads(value)
        except Exception:
            return []
        return list(decoded) if isinstance(decoded, list) else []
    return []


_RENDITION_METADATA_FIELDS = (
    "name",
    "id",
    "label",
    "profile",
    "width",
    "height",
    "fps",
    "bitrate",
    "video_bitrate",
    "audio_bitrate",
    "video_codec",
    "audio_codec",
    "codec",
    "mime_type",
)


def _safe_rendition_metadata(item: dict[str, Any]) -> dict[str, Any]:
    """Keep descriptive rendition metadata only; media URLs/auth remain Chat 2-owned."""

    safe: dict[str, Any] = {}
    for key in _RENDITION_METADATA_FIELDS:
        if key not in item:
            continue
        value = item[key]
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
            continue
        if key == "profile" and isinstance(value, dict):
            nested = {
                nested_key: nested_value
                for nested_key, nested_value in value.items()
                if nested_key in _RENDITION_METADATA_FIELDS
                and isinstance(nested_value, (str, int, float, bool, type(None)))
            }
            if nested:
                safe[key] = nested
    return safe


def _normalise_rendition_profile(value: Any) -> list[dict[str, Any]]:
    """Normalize both canonical Chat 2 list profiles and the older flat compatibility map.

    Chat 2's merged media runtime stores profiles such as ``{"renditions": ["720p", "480p"]}``.
    Older transport fixtures used ``{"landscape_720p": {...}}``. Preserve the latter exactly,
    while expanding the canonical list one item at a time. A nested-list item may carry descriptive
    metadata, but URLs, bearer material and other authority-bearing fields are intentionally dropped.
    """

    profiles = _json_dict(value)
    if "renditions" not in profiles:
        return [
            {"name": str(name), "profile": profile}
            for name, profile in sorted(profiles.items(), key=lambda item: str(item[0]))
        ]

    raw_renditions = profiles.get("renditions")
    if not isinstance(raw_renditions, list):
        return []

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_renditions:
        if isinstance(item, str):
            name = item.strip()
            profile: Any = name
        elif isinstance(item, dict):
            metadata = _safe_rendition_metadata(dict(item))
            candidate = (
                metadata.get("name")
                or metadata.get("id")
                or metadata.get("label")
                or (metadata.get("profile") if isinstance(metadata.get("profile"), str) else None)
            )
            name = str(candidate or "").strip()
            profile = metadata
        else:
            continue
        if not name or name in seen:
            continue
        seen.add(name)
        result.append({"name": name, "profile": profile})
    return result


class Chat2PlaybackAdapter:
    """Chat 4 viewer adapter for Chat 2's canonical transport/playback service.

    The adapter consumes the server-issued playback descriptor exactly as supplied. It never
    constructs a manifest URL, signs a token, starts transport, or treats discovery state as media
    truth. The broadcast owner is resolved from the canonical Shared Sky broadcast row because the
    Chat 2 store is tenant/owner scoped internally.
    """

    ACTIVE_STATES = {"live", "degraded", "reconnecting"}

    def __init__(self, transport_store: Any, community_store: Any = live.community):
        self.transport = transport_store
        self.community = community_store

    def _owner(self, broadcast_id: str) -> str:
        return str(self.community._broadcast(broadcast_id)["user_id"])

    @staticmethod
    def _state(raw_state: Any, available: bool) -> str:
        state = _value(raw_state)
        if state == "live":
            return "ready" if available else "unavailable"
        if state in {"degraded", "reconnecting", "ended", "failed"}:
            return state
        return "unavailable"

    def descriptor(self, broadcast_id: str, viewer_user_id: str | None) -> dict:
        del viewer_user_id  # Chat 2 currently issues a broadcast-scoped playback descriptor.
        try:
            owner_user_id = self._owner(broadcast_id)
            status = _dict(self.transport.status(owner_user_id, broadcast_id))
            raw = _dict(status.get("playback"))
            if not raw:
                raw = _dict(self.transport.playback(owner_user_id, broadcast_id))
        except Exception as exc:
            return {
                "available": False,
                "state": "unavailable",
                "reason": str(getattr(exc, "code", "chat2_playback_unavailable"))[:120],
                "broadcast_id": broadcast_id,
                "manifest_url": None,
                "authorization": None,
                "token_expires_at": None,
                "renditions": [],
                "captions": [],
                "dvr": False,
                "source": "chat2_transport",
            }

        capability = _value(raw.get("capability_state"))
        raw_state = raw.get("state") or _dict(status.get("session")).get("state")
        transport_state = _value(raw_state)
        available = capability == "ready" and transport_state in self.ACTIVE_STATES
        auth = _dict(raw.get("authorization")) if available else {}
        token = str(auth.get("token") or "")
        safe_auth = None
        if token:
            safe_auth = {
                "scheme": str(auth.get("scheme") or "Bearer"),
                "token": token,
                "expires_at": auth.get("expires_at"),
            }

        session = _dict(status.get("session"))
        renditions = _normalise_rendition_profile(session.get("rendition_profile"))
        captions = raw.get("captions") if isinstance(raw.get("captions"), list) else []

        return {
            "available": available,
            "state": self._state(raw_state, available),
            "reason": None if available else str(raw.get("reason_code") or "playback_not_ready")[:120],
            "broadcast_id": broadcast_id,
            "mode": raw.get("mode"),
            "manifest_url": raw.get("manifest_url") if available else None,
            "authorization": safe_auth,
            "token_expires_at": auth.get("expires_at") if safe_auth else None,
            "renditions": renditions,
            "captions": captions,
            "dvr": bool(raw.get("dvr", False)),
            "transport_state": transport_state,
            "source": "chat2_transport",
        }

    def replay(self, broadcast_id: str, viewer_user_id: str | None) -> dict:
        del viewer_user_id
        try:
            owner_user_id = self._owner(broadcast_id)
            status = _dict(self.transport.status(owner_user_id, broadcast_id))
        except Exception as exc:
            return {
                "available": False,
                "state": "unavailable",
                "reason": str(getattr(exc, "code", "chat2_replay_unavailable"))[:120],
                "broadcast_id": broadcast_id,
                "source": "chat2_recording_handoff",
            }

        recordings = status.get("recordings") if isinstance(status.get("recordings"), list) else []
        ordered = sorted(
            (_dict(item) for item in recordings),
            key=lambda item: {"programme": 0, "clean_feed": 1}.get(str(item.get("kind")), 10),
        )
        processing = False
        for item in ordered:
            state = _value(item.get("state"))
            if state in {"requested", "recording", "processing"}:
                processing = True
            if state != "complete" or not item.get("asset_id"):
                continue
            playback_url = str(item.get("playback_url") or item.get("replay_url") or "").strip()
            parsed = urlparse(playback_url) if playback_url else None
            playable = bool(
                playback_url
                and (
                    playback_url.startswith("/")
                    or (parsed is not None and parsed.scheme in {"http", "https"} and parsed.netloc)
                )
            )
            return {
                "available": playable,
                "state": "ready" if playable else "asset_ready",
                "reason": None if playable else "replay_asset_resolver_unavailable",
                "broadcast_id": broadcast_id,
                "asset_id": item.get("asset_id"),
                "kind": item.get("kind"),
                "duration_ms": item.get("duration_ms"),
                "checksum_sha256": item.get("checksum_sha256"),
                "replay_url": playback_url if playable else None,
                "source": "chat2_recording_handoff",
            }
        return {
            "available": False,
            "state": "processing" if processing else "unavailable",
            "reason": "replay_processing" if processing else "replay_not_available",
            "broadcast_id": broadcast_id,
            "source": "chat2_recording_handoff",
        }


class SharedSkyGiftLiveSessionDirectory:
    """Authoritative Shared Sky live-session validation seam consumed by Chat 5.

    This adapter validates only live-session identity and recipient ownership. It does not make age,
    region, spending, risk, payout, Coin-price or Battle-scoring decisions; those remain inside the
    Chat 5/6/10 contracts.
    """

    def __init__(self, context_type: Callable[..., Any], community_store: Any = live.community):
        self.context_type = context_type
        self.community = community_store

    def gift_context(self, *, live_session_id: str, recipient_creator_id: str):
        try:
            broadcast = self.community._broadcast(live_session_id)
        except Exception:
            return self.context_type(
                live_session_id=live_session_id,
                recipient_creator_id=recipient_creator_id,
                active=False,
                gift_eligible=False,
                recipient_eligible=False,
                battle_id=None,
                battle_round_id=None,
            )
        canonical_creator = str(broadcast.get("user_id") or "")
        active = str(broadcast.get("state") or "") == "live"
        recipient_matches = bool(canonical_creator and canonical_creator == recipient_creator_id)
        return self.context_type(
            live_session_id=live_session_id,
            recipient_creator_id=canonical_creator or recipient_creator_id,
            active=active,
            gift_eligible=bool(active and recipient_matches),
            recipient_eligible=recipient_matches,
            battle_id=None,
            battle_round_id=None,
        )


class Chat5GiftDisplayAdapter:
    """Display-only projection of Chat 5 financial truth for the Watch experience."""

    def __init__(self, economy_service: Any, community_store: Any = live.community):
        self.economy = economy_service
        self.community = community_store

    @staticmethod
    def _gift(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "gift_id": item.get("gift_id"),
            "version": int(item.get("version") or 0),
            "display_name": item.get("display_name"),
            "description": item.get("description") or "",
            "coin_cost": int(item.get("coin_cost") or 0),
            "asset_ref": item.get("asset_ref"),
            "reduced_motion_asset_ref": item.get("reduced_motion_asset_ref"),
            "sound_ref": item.get("sound_ref"),
            "category": item.get("category") or "support",
            "tags": _json_list(item.get("tags_json")),
            "battle_eligible": bool(item.get("battle_eligible", False)),
        }

    @staticmethod
    def _decision(value: Any) -> dict[str, Any]:
        if value is None:
            return {"eligible": False, "code": "eligibility_unavailable", "reason": None}
        if isinstance(value, dict):
            return {
                "eligible": bool(value.get("eligible")),
                "code": value.get("code") or ("eligible" if value.get("eligible") else "ineligible"),
                "reason": value.get("reason"),
            }
        return {
            "eligible": bool(getattr(value, "eligible", False)),
            "code": getattr(value, "code", "eligible" if getattr(value, "eligible", False) else "ineligible"),
            "reason": getattr(value, "reason", None),
        }

    def state(self, broadcast_id: str, viewer_user_id: str | None) -> dict:
        try:
            broadcast = self.community._broadcast(broadcast_id)
        except Exception:
            return {"available": False, "reason": "live_session_not_found", "catalogue": [], "goal": None}
        creator_user_id = str(broadcast.get("user_id") or "")
        live_now = str(broadcast.get("state") or "") == "live"
        try:
            catalogue = [self._gift(dict(item)) for item in self.economy.list_gifts(active_only=True)]
        except Exception:
            return {
                "available": False,
                "reason": "gift_catalogue_unavailable",
                "catalogue": [],
                "goal": None,
                "recipient_creator_id": creator_user_id,
                "live_session_id": broadcast_id,
            }

        try:
            context = self.economy.live_sessions.gift_context(
                live_session_id=broadcast_id,
                recipient_creator_id=creator_user_id,
            )
            live_eligible = bool(
                getattr(context, "active", False)
                and getattr(context, "gift_eligible", False)
                and getattr(context, "recipient_eligible", False)
            )
        except Exception:
            live_eligible = False

        sender_decision = {"eligible": False, "code": "sign_in_required", "reason": None}
        receiver_decision = {"eligible": False, "code": "eligibility_unavailable", "reason": None}
        try:
            receiver_decision = self._decision(
                self.economy.eligibility.check(
                    feature="gift_receive",
                    creator_recipient_id=creator_user_id,
                    live_session_id=broadcast_id,
                )
            )
        except Exception:
            pass

        balance = None
        spending = None
        if viewer_user_id:
            try:
                sender_decision = self._decision(
                    self.economy.eligibility.check(
                        feature="gift_send",
                        user_id=viewer_user_id,
                        creator_recipient_id=creator_user_id,
                        live_session_id=broadcast_id,
                    )
                )
            except Exception:
                sender_decision = {"eligible": False, "code": "eligibility_unavailable", "reason": None}
            try:
                balance = self.economy.get_balance(viewer_user_id)
            except Exception:
                balance = None
            try:
                spending = self.economy.spending_state(viewer_user_id)
            except Exception:
                spending = None

        send_enabled = bool(
            viewer_user_id
            and live_now
            and live_eligible
            and sender_decision["eligible"]
            and receiver_decision["eligible"]
            and catalogue
        )
        reason = None
        if not live_now:
            reason = "live_session_ended"
        elif not live_eligible:
            reason = "live_session_not_gift_eligible"
        elif not viewer_user_id:
            reason = "sign_in_required"
        elif not sender_decision["eligible"]:
            reason = str(sender_decision.get("code") or "sender_not_eligible")
        elif not receiver_decision["eligible"]:
            reason = str(receiver_decision.get("code") or "creator_not_eligible")
        elif not catalogue:
            reason = "gift_catalogue_empty"

        return {
            "available": bool(catalogue),
            "send_enabled": send_enabled,
            "reason": reason,
            "catalogue": catalogue,
            "goal": None,
            "recipient_creator_id": creator_user_id,
            "live_session_id": broadcast_id,
            "send_endpoint": "/economy/me/gifts/send",
            "idempotency_required": True,
            "balance": balance,
            "spending": spending,
            "sender_eligibility": sender_decision,
            "creator_eligibility": receiver_decision,
            "source": "chat5_cosmic_economy",
        }


_INTEGRATION_STATUS: dict[str, dict[str, Any]] = {
    "chat2_playback": {"state": "pending", "reason": "chat2_transport_module_not_merged"},
    "chat5_gifts": {"state": "pending", "reason": "chat5_economy_module_not_merged"},
    "chat6_battles": {"state": "pending", "reason": "chat6_battle_contract_not_merged"},
}


def configure_neighbor_live_integrations() -> dict[str, dict[str, Any]]:
    """Bind neighbouring canonical modules when present; otherwise retain fail-closed adapters."""

    try:
        transport_module = importlib.import_module(f"{__package__}.shared_sky_transport_domain")
        transport_store = getattr(transport_module, "transport")
        live.register_playback_adapter(Chat2PlaybackAdapter(transport_store, live.community))
        _INTEGRATION_STATUS["chat2_playback"] = {
            "state": "registered",
            "source": "aura_music_studio.shared_sky_transport_domain.transport",
        }
    except ModuleNotFoundError:
        _INTEGRATION_STATUS["chat2_playback"] = {
            "state": "pending",
            "reason": "chat2_transport_module_not_merged",
        }
    except Exception as exc:
        _INTEGRATION_STATUS["chat2_playback"] = {
            "state": "degraded",
            "reason": str(getattr(exc, "code", "chat2_adapter_registration_failed"))[:120],
        }

    try:
        economy_module = importlib.import_module(f"{__package__}.cosmic_economy")
        integration_module = importlib.import_module(f"{__package__}.cosmic_economy_integrations")
        live_context = getattr(economy_module, "LiveGiftContext")
        configure_economy = getattr(integration_module, "configure_economy_integrations")
        economy_service = getattr(integration_module, "economy_service")
        configure_economy(
            live_sessions=SharedSkyGiftLiveSessionDirectory(live_context, live.community)
        )
        service = economy_service()
        live.register_gift_display_adapter(Chat5GiftDisplayAdapter(service, live.community))
        _INTEGRATION_STATUS["chat5_gifts"] = {
            "state": "registered",
            "source": "aura_music_studio.cosmic_economy_integrations.economy_service",
            "financial_authority": "chat5",
        }
    except ModuleNotFoundError:
        _INTEGRATION_STATUS["chat5_gifts"] = {
            "state": "pending",
            "reason": "chat5_economy_module_not_merged",
        }
    except Exception as exc:
        _INTEGRATION_STATUS["chat5_gifts"] = {
            "state": "degraded",
            "reason": str(getattr(exc, "code", "chat5_adapter_registration_failed"))[:120],
        }

    return {key: dict(value) for key, value in _INTEGRATION_STATUS.items()}


def integration_status() -> dict[str, dict[str, Any]]:
    return {key: dict(value) for key, value in _INTEGRATION_STATUS.items()}


def _access_or_raise(broadcast_id: str, request: Request) -> str | None:
    member = live.optional_member(request)
    user_id = member.user_id if member else None
    try:
        decision = live.community.access(
            broadcast_id,
            user_id,
            direct=True,
            owner=owner_session_authorized(request),
        )
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky LIVE not found") from exc
    if not decision.allowed:
        raise HTTPException(403, decision.reason)
    return user_id


@router.get("/shared-sky/live/api/integration-status")
def shared_sky_live_integration_status():
    return {"integrations": integration_status()}


@router.get("/shared-sky/live/api/watch/{broadcast_id}/playback")
def refresh_playback_descriptor(broadcast_id: str, request: Request):
    user_id = _access_or_raise(broadcast_id, request)
    try:
        return live._playback_adapter.descriptor(broadcast_id, user_id)
    except Exception as exc:
        raise HTTPException(503, "Shared Sky playback descriptor is unavailable") from exc


@router.get("/shared-sky/live/api/watch/{broadcast_id}/gift-display")
def refresh_gift_display(broadcast_id: str, request: Request):
    user_id = _access_or_raise(broadcast_id, request)
    try:
        return live._gift_adapter.state(broadcast_id, user_id)
    except Exception as exc:
        raise HTTPException(503, "Shared Sky Gift display state is unavailable") from exc


__all__ = [
    "router",
    "Chat2PlaybackAdapter",
    "SharedSkyGiftLiveSessionDirectory",
    "Chat5GiftDisplayAdapter",
    "configure_neighbor_live_integrations",
    "integration_status",
]
