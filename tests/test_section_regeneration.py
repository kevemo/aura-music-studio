from pathlib import Path

import pytest

from aura_music_studio import section_regeneration as sr
from aura_music_studio.session import Clip, StudioSession, Track
from aura_music_studio.song_dna import InstrumentLayer, SongDNA, SongEditDirective, SongSection


def _fixture_project(tmp_path: Path):
    project = tmp_path / "song"
    project.mkdir()
    audio = project / "audio"
    audio.mkdir()
    vocal = audio / "vocal.wav"
    guitar = audio / "guitar.wav"
    strings = audio / "strings.wav"
    for path in (vocal, guitar, strings):
        path.write_bytes(b"source")

    vocal_clip = Clip(
        id="clip-vocal",
        name="Lead vocal",
        kind="audio",
        source="audio/vocal.wav",
        start=0.0,
        duration=30.0,
        source_offset=0.0,
        metadata={"real_audio": True, "committed": True},
    )
    guitar_clip = Clip(
        id="clip-guitar",
        name="Guitar",
        kind="audio",
        source="audio/guitar.wav",
        start=8.0,
        duration=20.0,
        source_offset=2.0,
        metadata={"real_audio": True, "committed": True},
    )
    strings_clip = Clip(
        id="clip-strings",
        name="Strings",
        kind="audio",
        source="audio/strings.wav",
        start=0.0,
        duration=30.0,
        metadata={"real_audio": True, "committed": True},
    )
    session = StudioSession(
        name="Editable song",
        tracks=[
            Track(id="track-vocal", name="Lead vocal", role="vocals", clips=[vocal_clip]),
            Track(id="track-guitar", name="Guitar", role="guitar", clips=[guitar_clip]),
            Track(id="track-strings", name="Strings", role="strings", clips=[strings_clip]),
        ],
    )
    section = SongSection(id="sec-chorus", name="Chorus", start_seconds=10.0, end_seconds=20.0)
    dna = SongDNA(
        project_name="song",
        title="Song",
        sections=[section],
        instruments=[
            InstrumentLayer(id="layer-vocal", role="vocals", label="Lead vocal", track_id="track-vocal"),
            InstrumentLayer(id="layer-guitar", role="guitar", label="Guitar", track_id="track-guitar"),
            InstrumentLayer(id="layer-strings", role="strings", label="Strings", track_id="track-strings", locked=True),
        ],
    )
    directive = SongEditDirective(
        id="edit-section",
        action="regenerate_section",
        instruction="Make the chorus more powerful without changing the song identity.",
        target_ids=["sec-chorus"],
    )
    return project, session, dna, directive


def test_generate_section_candidate_batch_repaints_each_unlocked_layer(monkeypatch, tmp_path):
    project, session, dna, directive = _fixture_project(tmp_path)
    calls = []

    def fake_region_take(source, request, work_dir, take_number):
        calls.append((source, request, work_dir, take_number))
        work_dir.mkdir(parents=True, exist_ok=True)
        result = work_dir / "candidate.wav"
        result.write_bytes(b"candidate")
        return result

    monkeypatch.setattr(sr, "generate_region_take", fake_region_take)
    batch = sr.generate_section_candidate_batch(
        project,
        dna,
        directive,
        work_dir=project / "work" / "section",
        session=session,
    )

    assert [item.track_id for item in batch.tracks] == ["track-vocal", "track-guitar"]
    assert len(calls) == 2
    assert calls[0][1].start_seconds == 10.0
    assert calls[0][1].end_seconds == 20.0
    # Guitar clip starts at session second 8 with a two-second source offset.
    assert calls[1][1].start_seconds == 4.0
    assert calls[1][1].end_seconds == 14.0
    assert all(Path(item.candidate_path).is_file() for item in batch.tracks)


def test_preserve_ids_leave_selected_layer_untouched(monkeypatch, tmp_path):
    project, session, dna, directive = _fixture_project(tmp_path)
    directive.preserve_ids = ["layer-guitar"]

    def fake_region_take(source, request, work_dir, take_number):
        work_dir.mkdir(parents=True, exist_ok=True)
        result = work_dir / "candidate.wav"
        result.write_bytes(b"candidate")
        return result

    monkeypatch.setattr(sr, "generate_region_take", fake_region_take)
    batch = sr.generate_section_candidate_batch(
        project,
        dna,
        directive,
        work_dir=project / "work" / "section",
        session=session,
    )
    assert [item.track_id for item in batch.tracks] == ["track-vocal"]


def test_stage_section_candidate_batch_is_non_destructive_and_grouped(monkeypatch, tmp_path):
    project, session, dna, directive = _fixture_project(tmp_path)

    def fake_region_take(source, request, work_dir, take_number):
        work_dir.mkdir(parents=True, exist_ok=True)
        result = work_dir / "candidate.wav"
        result.write_bytes(b"candidate")
        return result

    monkeypatch.setattr(sr, "generate_region_take", fake_region_take)
    batch = sr.generate_section_candidate_batch(
        project,
        dna,
        directive,
        work_dir=project / "work" / "section",
        session=session,
    )
    staged = sr.stage_section_candidate_batch(session, batch, project=project)

    assert len(session.find_track("track-vocal").clips) == 1
    assert session.find_track("track-vocal").clips[0].metadata["committed"] is True
    assert len(staged.find_track("track-vocal").clips) == 2
    assert len(staged.find_track("track-guitar").clips) == 2
    assert staged.find_track("track-vocal").clips[0].metadata["committed"] is False
    assert staged.find_track("track-vocal").clips[-1].metadata["committed"] is True
    history = staged.generation_history[-1]
    assert history["candidate_count"] == 2
    assert history["persisted"] is False
    assert set(history["track_ids"]) == {"track-vocal", "track-guitar"}


def test_stage_validates_entire_batch_before_mutating(monkeypatch, tmp_path):
    project, session, dna, directive = _fixture_project(tmp_path)

    def fake_region_take(source, request, work_dir, take_number):
        work_dir.mkdir(parents=True, exist_ok=True)
        result = work_dir / "candidate.wav"
        result.write_bytes(b"candidate")
        return result

    monkeypatch.setattr(sr, "generate_region_take", fake_region_take)
    batch = sr.generate_section_candidate_batch(
        project,
        dna,
        directive,
        work_dir=project / "work" / "section",
        session=session,
    )
    batch.tracks[-1].base_clip_id = "stale-clip-id"

    with pytest.raises(RuntimeError, match="changed after candidate generation"):
        sr.stage_section_candidate_batch(session, batch, project=project)

    assert len(session.find_track("track-vocal").clips) == 1
    assert len(session.find_track("track-guitar").clips) == 1
    assert session.find_track("track-vocal").clips[0].metadata["committed"] is True


def test_candidate_paths_must_remain_inside_project(tmp_path):
    project, session, dna, directive = _fixture_project(tmp_path)
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"candidate")
    batch = sr.SectionCandidateBatch(
        directive_id=directive.id,
        section_id="sec-chorus",
        section_start_seconds=10.0,
        section_end_seconds=20.0,
        tracks=[
            sr.SectionTrackCandidate(
                layer_id="layer-vocal",
                track_id="track-vocal",
                base_clip_id="clip-vocal",
                candidate_path=str(outside),
                section_start_seconds=10.0,
                section_end_seconds=20.0,
                source_relative_start=10.0,
                source_relative_end=20.0,
            )
        ],
    )
    with pytest.raises(ValueError, match="escaped the project boundary"):
        sr.stage_section_candidate_batch(session, batch, project=project)
