from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio.plans import (
    ADVANCED_VIDEO_GENERATION,
    VIDEO_DIRECTOR,
    VIDEO_EXTENDED_DURATION,
    VIDEO_GENERATION,
    VIDEO_HIGH_QUALITY,
    VIDEO_PROVIDER_CONTROL,
    VIDEO_TO_VIDEO,
    get_plan,
)
from aura_music_studio.video_api import GenerateVideoBody, _enforce_tier
from aura_music_studio.video_generation import VideoGenerationRequest, VideoGenerationService


def test_video_request_defaults_are_social_ready(tmp_path):
    service = VideoGenerationService(tmp_path)
    request = VideoGenerationRequest(prompt="cinematic live performance")
    assert request.aspect_ratio == "9:16"
    assert request.duration_seconds == 8
    assert request.provider == "auto"
    assert "text_to_video" in service.VALID_MODES


def test_video_generation_unlocks_on_base_and_not_free():
    free = get_plan("free")
    base = get_plan("base")
    pro = get_plan("pro")

    assert not free.has(VIDEO_GENERATION)
    assert base.has(VIDEO_GENERATION)
    assert pro.has(VIDEO_GENERATION)


def test_pro_contains_advanced_video_controls():
    base = get_plan("base")
    pro = get_plan("pro")
    advanced = {
        ADVANCED_VIDEO_GENERATION,
        VIDEO_DIRECTOR,
        VIDEO_TO_VIDEO,
        VIDEO_EXTENDED_DURATION,
        VIDEO_HIGH_QUALITY,
        VIDEO_PROVIDER_CONTROL,
    }
    assert all(not base.has(feature) for feature in advanced)
    assert all(pro.has(feature) for feature in advanced)


def test_base_video_creation_is_core_not_advanced():
    member = SimpleNamespace(plan=get_plan("base"))
    _enforce_tier(member, GenerateVideoBody(prompt="performance video", duration_seconds=12))
    _enforce_tier(
        member,
        GenerateVideoBody(
            prompt="animate this cover",
            mode="image_to_video",
            duration_seconds=8,
            reference_url="https://example.com/cover.png",
        ),
    )

    restricted = [
        GenerateVideoBody(prompt="long video", duration_seconds=13),
        GenerateVideoBody(
            prompt="restyle this clip",
            mode="video_to_video",
            reference_url="https://example.com/source.mp4",
        ),
        GenerateVideoBody(prompt="high quality", quality="high"),
        GenerateVideoBody(prompt="manual provider", provider="runway"),
        GenerateVideoBody(prompt="advanced prompt", negative_prompt="no text"),
    ]
    for body in restricted:
        with pytest.raises(HTTPException) as exc:
            _enforce_tier(member, body)
        assert exc.value.status_code == 403


def test_pro_can_use_advanced_video_generation_controls():
    member = SimpleNamespace(plan=get_plan("pro"))
    _enforce_tier(
        member,
        GenerateVideoBody(
            prompt="cinematic professional music video",
            mode="video_to_video",
            reference_url="https://example.com/source.mp4",
            duration_seconds=60,
            quality="professional",
            provider="runway",
            negative_prompt="no logos",
        ),
    )
