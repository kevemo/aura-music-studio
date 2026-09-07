from __future__ import annotations

import pytest

from aura_music_studio.chord_intelligence import (
    ChordEvent,
    _persist_progression,
    describe_chord,
    normalize_chord_symbol,
    reharmonize_progression,
    router,
)
from aura_music_studio.revisions import get_revision, restore_revision
from aura_music_studio.song_dna import SongDNAStore, create_song_dna


def test_chord_symbol_normalization_and_bounded_parser():
    assert normalize_chord_symbol("cmin7") == "Cm7"
    assert normalize_chord_symbol("F#maj7/A#") == "F#maj7/A#"
    assert normalize_chord_symbol("Bbadd9") == "Bbadd9"

    with pytest.raises(ValueError, match="Unsupported chord symbol"):
        normalize_chord_symbol("C<script>")
    with pytest.raises(ValueError, match="Invalid chord symbol"):
        normalize_chord_symbol("C\nG")


def test_chord_analysis_projects_roman_and_nashville_labels():
    tonic = describe_chord("C", "C major")
    assert tonic.roman_numeral == "I"
    assert tonic.nashville_number == "1"
    assert tonic.in_key is True

    minor_two = describe_chord("Dm7", "C major")
    assert minor_two.roman_numeral == "ii"
    assert minor_two.nashville_number == "2"
    assert minor_two.in_key is True

    flat_seven = describe_chord("Bb", "C major")
    assert flat_seven.roman_numeral == "bVII"
    assert flat_seven.nashville_number == "b7"
    assert flat_seven.in_key is False


def test_reharmonisation_is_deterministic_and_preserves_timing():
    source = [
        ChordEvent(id="one", symbol="Cmaj7", start_seconds=0, end_seconds=4),
        ChordEvent(id="two", symbol="Dm9", start_seconds=4, end_seconds=8),
        ChordEvent(id="three", symbol="G7", start_seconds=8, end_seconds=12),
    ]

    simple = reharmonize_progression(source, mode="simplify", style="pop")
    assert [item.symbol for item in simple] == ["C", "Dm", "G"]
    assert [(item.start_seconds, item.end_seconds) for item in simple] == [(0, 4), (4, 8), (8, 12)]

    jazz = reharmonize_progression(simple, mode="sophisticate", style="jazz")
    assert [item.symbol for item in jazz] == ["Cmaj9", "Dm9", "Gmaj9"]
    assert all(item.metadata["deterministic"] is True for item in jazz)


def test_chord_mutation_checkpoints_song_dna_and_can_restore(tmp_path):
    project = tmp_path / "song"
    project.mkdir()
    create_song_dna(project, project_name="song", title="Song", key="C", structure="Verse")
    store = SongDNAStore(project)

    first = _persist_progression(
        store,
        [ChordEvent(id="ch1", symbol="C", start_seconds=0, end_seconds=4)],
        key="C",
        operation="test_initial",
    )
    assert first["audio_rendered"] is False
    assert first["midi_rewritten"] is False

    second = _persist_progression(
        store,
        [ChordEvent(id="ch1", symbol="G7", start_seconds=0, end_seconds=4)],
        key="C",
        operation="test_replace",
    )
    _folder, revision = get_revision(project, second["revision_id"])
    assert revision["song_dna_included"] is True
    assert any(item["path"] == "song_dna.json" for item in revision["files"])
    assert store.load().metadata["chord_progression"][0]["symbol"] == "G7"

    restored = restore_revision(project, second["revision_id"], create_backup=False)
    assert "song_dna.json" in restored["restored_files"]
    assert store.load().metadata["chord_progression"][0]["symbol"] == "C"


def test_chord_studio_routes_are_reachable_from_mounted_router():
    paths = {(getattr(route, "path", None), tuple(sorted(getattr(route, "methods", set()) or set()))) for route in router.routes}
    assert ("/projects/{project_name}/song-dna/chords", ("GET",)) in paths
    assert ("/projects/{project_name}/song-dna/chords", ("PUT",)) in paths
    assert ("/projects/{project_name}/song-dna/chords", ("POST",)) in paths
    assert ("/projects/{project_name}/song-dna/chords/{chord_id}", ("PATCH",)) in paths
    assert ("/projects/{project_name}/song-dna/chords/{chord_id}", ("DELETE",)) in paths
    assert ("/projects/{project_name}/song-dna/chords/reharmonize", ("POST",)) in paths
    assert any(path == "/song-editor/{project_name}/chords" for path, _methods in paths)
