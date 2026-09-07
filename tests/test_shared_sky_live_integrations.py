from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI

from aura_music_studio.shared_sky_live_bootstrap import install_shared_sky_live_community
from aura_music_studio.shared_sky_live_integrations import (
    Chat2PlaybackAdapter,
    Chat5GiftDisplayAdapter,
    SharedSkyGiftLiveSessionDirectory,
    integration_status,
)


class StubCommunity:
    def __init__(self, *, state: str = "live", creator: str = "creator-1"):
        self.state = state
        self.creator = creator

    def _broadcast(self, broadcast_id: str) -> dict:
        if broadcast_id == "missing":
            raise KeyError(broadcast_id)
        return {"id": broadcast_id, "user_id": self.creator, "state": self.state}


class StubTransport:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    def status(self, user_id: str, broadcast_id: str) -> dict:
        self.calls.append((user_id, broadcast_id))
        return self.payload

    def playback(self, user_id: str, broadcast_id: str) -> dict:
        raise AssertionError("status() already supplied the canonical playback descriptor")


def test_chat2_playback_adapter_preserves_server_descriptor_without_inventing_manifest():
    transport = StubTransport(
        {
            "session": {
                "state": "live",
                "rendition_profile": '{"landscape":"1080p30","portrait":"1080x1920p30"}',
            },
            "playback": {
                "capability_state": "ready",
                "mode": "ll-hls",
                "manifest_url": "https://origin.example/live-1/master.m3u8",
                "authorization": {
                    "scheme": "Bearer",
                    "token": "server-issued-token",
                    "expires_at": "2026-09-05T03:00:00+00:00",
                },
                "state": "live",
            },
            "recordings": [],
        }
    )
    adapter = Chat2PlaybackAdapter(transport, StubCommunity())

    result = adapter.descriptor("live-1", "viewer-1")

    assert result["available"] is True
    assert result["state"] == "ready"
    assert result["manifest_url"] == "https://origin.example/live-1/master.m3u8"
    assert result["authorization"]["token"] == "server-issued-token"
    assert result["token_expires_at"] == "2026-09-05T03:00:00+00:00"
    assert result["renditions"] == [
        {"name": "landscape", "profile": "1080p30"},
        {"name": "portrait", "profile": "1080x1920p30"},
    ]
    assert result["captions"] == [] and result["dvr"] is False
    assert transport.calls == [("creator-1", "live-1")]


def test_chat2_playback_adapter_fails_closed_for_nonready_capability():
    transport = StubTransport(
        {
            "session": {"state": "live", "rendition_profile": {}},
            "playback": {
                "capability_state": "credentials_missing",
                "reason_code": "internal_playback_unconfigured",
                "state": "live",
                "manifest_url": "https://must-not-be-exposed.invalid/master.m3u8",
            },
            "recordings": [],
        }
    )
    result = Chat2PlaybackAdapter(transport, StubCommunity()).descriptor("live-1", None)
    assert result["available"] is False
    assert result["state"] == "unavailable"
    assert result["manifest_url"] is None
    assert result["authorization"] is None
    assert result["reason"] == "internal_playback_unconfigured"


def test_chat2_replay_exposes_asset_reference_without_fabricating_playback_url():
    transport = StubTransport(
        {
            "session": {"state": "ended"},
            "playback": {"capability_state": "unsupported", "state": "ended"},
            "recordings": [
                {
                    "kind": "programme",
                    "state": "complete",
                    "asset_id": "asset-programme-1",
                    "checksum_sha256": "a" * 64,
                    "duration_ms": 123000,
                    "storage_uri": "s3://private-bucket/secret/object",
                }
            ],
        }
    )
    result = Chat2PlaybackAdapter(transport, StubCommunity(state="ended")).replay("live-1", "viewer-1")
    assert result["available"] is False
    assert result["state"] == "asset_ready"
    assert result["asset_id"] == "asset-programme-1"
    assert result["replay_url"] is None
    assert "storage_uri" not in result


@dataclass
class GiftContext:
    live_session_id: str
    recipient_creator_id: str
    active: bool
    gift_eligible: bool
    recipient_eligible: bool
    battle_id: str | None = None
    battle_round_id: str | None = None


def test_shared_sky_gift_directory_validates_canonical_creator_and_live_state():
    directory = SharedSkyGiftLiveSessionDirectory(GiftContext, StubCommunity())
    allowed = directory.gift_context(live_session_id="live-1", recipient_creator_id="creator-1")
    assert allowed.active is True and allowed.gift_eligible is True and allowed.recipient_eligible is True

    mismatch = directory.gift_context(live_session_id="live-1", recipient_creator_id="other-creator")
    assert mismatch.active is True
    assert mismatch.gift_eligible is False and mismatch.recipient_eligible is False
    assert mismatch.recipient_creator_id == "creator-1"

    ended = SharedSkyGiftLiveSessionDirectory(GiftContext, StubCommunity(state="ended")).gift_context(
        live_session_id="live-1", recipient_creator_id="creator-1"
    )
    assert ended.active is False and ended.gift_eligible is False


class Decision:
    def __init__(self, eligible: bool, code: str = "eligible", reason: str | None = None):
        self.eligible = eligible
        self.code = code
        self.reason = reason


class Eligibility:
    def __init__(self, *, sender: bool = True, receiver: bool = True):
        self.sender = sender
        self.receiver = receiver

    def check(self, *, feature: str, **kwargs):
        del kwargs
        if feature == "gift_send":
            return Decision(self.sender, "eligible" if self.sender else "sender_policy_block")
        if feature == "gift_receive":
            return Decision(self.receiver, "eligible" if self.receiver else "creator_policy_block")
        return Decision(False, "unsupported_feature")


class LiveSessions:
    def gift_context(self, *, live_session_id: str, recipient_creator_id: str):
        return GiftContext(live_session_id, recipient_creator_id, True, True, True)


class StubEconomy:
    def __init__(self, *, sender: bool = True, receiver: bool = True):
        self.eligibility = Eligibility(sender=sender, receiver=receiver)
        self.live_sessions = LiveSessions()
        self.balance_calls = 0
        self.spending_calls = 0

    def list_gifts(self, *, active_only: bool = True):
        assert active_only is True
        return [
            {
                "gift_id": "starlight-spark",
                "version": 1,
                "display_name": "Starlight Spark",
                "description": "Original Shared Sky Gift",
                "coin_cost": 10,
                "asset_ref": "gift://starlight-spark",
                "reduced_motion_asset_ref": "gift://starlight-spark/static",
                "sound_ref": "gift://starlight-spark/sound",
                "category": "support",
                "tags_json": '["stars","support"]',
                "battle_eligible": 1,
                "approved_by": "must-not-leak",
            }
        ]

    def get_balance(self, user_id: str):
        self.balance_calls += 1
        assert user_id == "viewer-1"
        return {
            "account_id": "account-internal",
            "available_coins": 900,
            "held_coins": 0,
            "recovery_debt_coins": 0,
            "ledger_version": 4,
            "status": "active",
        }

    def spending_state(self, user_id: str):
        self.spending_calls += 1
        assert user_id == "viewer-1"
        return {
            "spent": {"daily": 100, "weekly": 100, "monthly": 100},
            "limits": {"daily_hard_limit": 1000},
            "warnings": [{"period": "daily", "threshold": 100, "spent": 100}],
        }


def test_chat5_gift_display_projects_authoritative_catalogue_and_safety_state_only():
    economy = StubEconomy()
    result = Chat5GiftDisplayAdapter(economy, StubCommunity()).state("live-1", "viewer-1")

    assert result["available"] is True and result["send_enabled"] is True
    assert result["send_endpoint"] == "/economy/me/gifts/send"
    assert result["idempotency_required"] is True
    assert result["recipient_creator_id"] == "creator-1"
    assert result["catalogue"][0]["coin_cost"] == 10
    assert "approved_by" not in result["catalogue"][0]
    assert result["balance"]["available_coins"] == 900
    assert result["spending"]["warnings"][0]["period"] == "daily"
    assert economy.balance_calls == 1 and economy.spending_calls == 1


def test_chat5_gift_display_disables_send_when_canonical_eligibility_blocks():
    economy = StubEconomy(sender=False)
    result = Chat5GiftDisplayAdapter(economy, StubCommunity()).state("live-1", "viewer-1")
    assert result["available"] is True
    assert result["send_enabled"] is False
    assert result["reason"] == "sender_policy_block"
    assert result["sender_eligibility"]["eligible"] is False


def test_live_bootstrap_mounts_refresh_and_capability_routes_idempotently():
    app = FastAPI()
    install_shared_sky_live_community(app)
    install_shared_sky_live_community(app)
    paths = [getattr(route, "path", "") for route in app.routes]
    assert paths.count("/shared-sky/live/api/integration-status") == 1
    assert paths.count("/shared-sky/live/api/watch/{broadcast_id}/playback") == 1
    assert paths.count("/shared-sky/live/api/watch/{broadcast_id}/gift-display") == 1
    status = integration_status()
    assert {"chat2_playback", "chat5_gifts", "chat6_battles"}.issubset(status)
