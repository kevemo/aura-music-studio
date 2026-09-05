from __future__ import annotations

import json

from aura_music_studio.shared_sky_live_integrations import (
    Chat2PlaybackAdapter,
    _normalise_rendition_profile,
)


class _Community:
    def _broadcast(self, broadcast_id: str):
        assert broadcast_id == "live-1"
        return {"user_id": "creator-1"}


class _Transport:
    def __init__(self, rendition_profile):
        self.rendition_profile = rendition_profile

    def status(self, owner_user_id: str, broadcast_id: str):
        assert owner_user_id == "creator-1"
        assert broadcast_id == "live-1"
        return {
            "session": {
                "state": "live",
                "rendition_profile": self.rendition_profile,
            },
            "playback": {
                "state": "live",
                "capability_state": "ready",
                "mode": "first_party_hls",
                "manifest_url": "/shared-sky/media/live-1/bootstrap",
                "authorization": {},
            },
        }

    def playback(self, owner_user_id: str, broadcast_id: str):
        raise AssertionError("status already supplied the canonical playback descriptor")


def test_canonical_chat2_rendition_list_expands_one_entry_per_rendition():
    descriptor = Chat2PlaybackAdapter(
        _Transport({"renditions": ["720p", "480p"]}),
        _Community(),
    ).descriptor("live-1", None)

    assert descriptor["available"] is True
    assert descriptor["renditions"] == [
        {"name": "720p", "profile": "720p"},
        {"name": "480p", "profile": "480p"},
    ]
    assert all("manifest_url" not in item for item in descriptor["renditions"])


def test_canonical_chat2_rendition_json_string_is_supported():
    descriptor = Chat2PlaybackAdapter(
        _Transport(json.dumps({"renditions": ["1080p", "720p"]})),
        _Community(),
    ).descriptor("live-1", "viewer-1")

    assert [item["name"] for item in descriptor["renditions"]] == ["1080p", "720p"]


def test_nested_rendition_metadata_drops_media_urls_and_authority_material():
    normalized = _normalise_rendition_profile(
        {
            "renditions": [
                {
                    "name": "720p",
                    "height": 720,
                    "video_bitrate": 2_500_000,
                    "manifest_url": "https://must-not-leak.example/720.m3u8",
                    "authorization": {"token": "must-not-leak"},
                    "token": "must-not-leak",
                }
            ]
        }
    )

    assert normalized == [
        {
            "name": "720p",
            "profile": {
                "name": "720p",
                "height": 720,
                "video_bitrate": 2_500_000,
            },
        }
    ]
    serialized = json.dumps(normalized)
    assert "manifest_url" not in serialized
    assert "authorization" not in serialized
    assert "must-not-leak" not in serialized


def test_legacy_flat_profile_map_preserves_existing_compatibility_shape():
    profile = {
        "landscape_720p": {"video_bitrate": "2500k"},
        "portrait_720p": {"video_bitrate": "2200k"},
    }

    assert _normalise_rendition_profile(profile) == [
        {"name": "landscape_720p", "profile": {"video_bitrate": "2500k"}},
        {"name": "portrait_720p", "profile": {"video_bitrate": "2200k"}},
    ]


def test_malformed_canonical_renditions_fail_closed_instead_of_becoming_fake_quality():
    assert _normalise_rendition_profile({"renditions": "720p,480p"}) == []
    assert _normalise_rendition_profile({"renditions": [None, 720, {}, "", "720p", "720p"]}) == [
        {"name": "720p", "profile": "720p"}
    ]
