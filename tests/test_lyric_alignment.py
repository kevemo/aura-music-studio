from __future__ import annotations

from aura_music_studio.lyric_alignment import estimate_alignment, line_is_surgically_aligned, verify_line
from aura_music_studio.song_dna import create_song_dna


def _song(tmp_path):
    project = tmp_path / "song"
    project.mkdir()
    create_song_dna(
        project,
        project_name="song",
        title="Alignment Test",
        target_duration_seconds=20,
        structure="Verse, Chorus",
        lyrics="[Verse]\nFirst line here\nSecond line here\n\n[Chorus]\nThis is the chorus",
        instruments=["vocals", "piano"],
    )
    return project


def test_estimated_alignment_is_not_surgical_authority(tmp_path):
    project = _song(tmp_path)
    dna = estimate_alignment(project, start_seconds=1.0, end_seconds=19.0)

    assert all(line.start_seconds is not None and line.end_seconds is not None for line in dna.lyric_lines)
    assert all(line.metadata["alignment_state"] == "estimated" for line in dna.lyric_lines)
    assert all(line.metadata["alignment_confidence"] == 0.25 for line in dna.lyric_lines)
    assert not any(line_is_surgically_aligned(line) for line in dna.lyric_lines)


def test_manual_verification_unlocks_only_the_verified_line(tmp_path):
    project = _song(tmp_path)
    dna = estimate_alignment(project, start_seconds=0.0, end_seconds=20.0)
    target = dna.lyric_lines[0]

    updated = verify_line(project, target.id, start_seconds=1.2, end_seconds=3.8, actor="test-user")
    verified = next(line for line in updated.lyric_lines if line.id == target.id)
    others = [line for line in updated.lyric_lines if line.id != target.id]

    assert verified.metadata["alignment_state"] == "verified"
    assert verified.metadata["alignment_confidence"] == 1.0
    assert line_is_surgically_aligned(verified) is True
    assert all(line_is_surgically_aligned(line) is False for line in others)


def test_invalid_manual_range_is_rejected(tmp_path):
    project = _song(tmp_path)
    line_id = create_song_dna(project, project_name="ignored", title="ignored").lyric_lines[0].id if False else None
    dna = estimate_alignment(project, start_seconds=0.0, end_seconds=20.0)
    line_id = dna.lyric_lines[0].id

    try:
        verify_line(project, line_id, start_seconds=5.0, end_seconds=4.0)
    except ValueError as exc:
        assert "end after the start" in str(exc)
    else:
        raise AssertionError("Invalid lyric range should fail")
