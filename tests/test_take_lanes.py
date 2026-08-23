from __future__ import annotations

from aura_music_studio.mixer import selected_audio_clips
from aura_music_studio.session import Clip, Track
from aura_music_studio.take_api import _base_comp_segments, _copy_segment, _public_track


def _clip(name: str, lane: int, committed: bool = False) -> Clip:
    return Clip(
        name=name,
        kind="audio",
        source=f"work/private/{name}.wav",
        duration=10.0,
        take_lane=lane,
        metadata={"committed": committed, "generated": True},
    )


def test_latest_take_is_selected_when_nothing_is_committed():
    track = Track(name="Guitar", role="guitar", clips=[_clip("one", 0), _clip("two", 1), _clip("three", 2)])
    selected = selected_audio_clips(track)
    assert [clip.take_lane for clip in selected] == [2]


def test_committed_take_overrides_newest_instead_of_layering_with_it():
    track = Track(name="Guitar", role="guitar", clips=[_clip("one", 0), _clip("two", 1, True), _clip("three", 2)])
    selected = selected_audio_clips(track)
    assert [clip.take_lane for clip in selected] == [1]
    assert all(clip.name != "three" for clip in selected)


def test_public_take_metadata_hides_filesystem_source():
    track = Track(name="Lead Vocal", role="vocals", clips=[_clip("private-vocal", 0)])
    public = _public_track(track, "my-song")
    clip = public["lanes"][0]["clips"][0]
    assert "source" not in clip
    assert clip["preview_url"].startswith("/projects/my-song/takes/audio/")
    assert "private" not in clip["preview_url"]


def test_comp_segment_uses_correct_timeline_and_source_offset():
    original = _clip("take-two", 1)
    segment = _copy_segment(original, 2.5, 6.0, source_lane=1, fade_seconds=.005)
    assert segment.start == 2.5
    assert segment.duration == 3.5
    assert segment.source_offset == 2.5
    assert segment.metadata["committed"] is True
    assert segment.metadata["comp_segment"] is True
    assert segment.metadata["comp_source_lane"] == 1


def test_existing_comp_becomes_base_for_next_phrase_edit():
    first = _copy_segment(_clip("take-one", 0), 0.0, 4.0, source_lane=0, fade_seconds=.005)
    second = _copy_segment(_clip("take-two", 1), 4.0, 10.0, source_lane=1, fade_seconds=.005)
    track = Track(name="Vocal", role="vocals", clips=[_clip("one", 0), _clip("two", 1), first, second])
    base = _base_comp_segments(track)
    assert len(base) == 2
    assert [clip.metadata["comp_source_lane"] for clip in base] == [0, 1]
    assert selected_audio_clips(track) == base
