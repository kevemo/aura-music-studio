from __future__ import annotations

from pathlib import Path

from aura_music_studio.song_dna import create_song_dna
from aura_music_studio.song_dna_focus_locks import focus_song_locks


def _project(tmp_path: Path):
    project = tmp_path / "song"
    dna = create_song_dna(
        project,
        project_name="focus-song",
        title="Focus Song",
        structure="Verse, Chorus",
        lyrics="[Verse]\nLine one\nLine two\n\n[Chorus]\nHook one\nHook two",
        instruments=["drums", "bass", "guitar"],
    )
    return project, dna


def test_focus_lyric_leaves_only_selected_line_editable(tmp_path: Path, monkeypatch):
    project, dna = _project(tmp_path)
    monkeypatch.setenv("AURA_PROJECT_ROOT", str(tmp_path))
    # The focus helper uses the tenant project resolver in production; patch its store helper for a local unit project.
    import aura_music_studio.song_dna_focus_locks as focus
    from aura_music_studio.song_dna import SongDNAStore
    monkeypatch.setattr(focus, "_store", lambda _name: SongDNAStore(project))

    selected = dna.lyric_lines[1]
    result = focus_song_locks("focus-song", "lyrics", selected.id)
    assert next(x for x in result.lyric_lines if x.id == selected.id).locked is False
    assert all(x.locked for x in result.lyric_lines if x.id != selected.id)
    assert all(x.locked for x in result.sections)
    assert all(x.locked for x in result.instruments)
    assert result.metadata["focus_lock_events"][-1]["target_id"] == selected.id


def test_focus_section_keeps_its_lyrics_editable_but_protects_other_song_parts(tmp_path: Path, monkeypatch):
    project, dna = _project(tmp_path)
    import aura_music_studio.song_dna_focus_locks as focus
    from aura_music_studio.song_dna import SongDNAStore
    monkeypatch.setattr(focus, "_store", lambda _name: SongDNAStore(project))

    selected = dna.sections[0]
    result = focus_song_locks("focus-song", "sections", selected.id)
    assert next(x for x in result.sections if x.id == selected.id).locked is False
    assert all(x.locked for x in result.sections if x.id != selected.id)
    assert all((line.section_id == selected.id and not line.locked) or (line.section_id != selected.id and line.locked) for line in result.lyric_lines)
    assert all(x.locked for x in result.instruments)


def test_focus_instrument_leaves_one_layer_editable(tmp_path: Path, monkeypatch):
    project, dna = _project(tmp_path)
    import aura_music_studio.song_dna_focus_locks as focus
    from aura_music_studio.song_dna import SongDNAStore
    monkeypatch.setattr(focus, "_store", lambda _name: SongDNAStore(project))

    selected = dna.instruments[-1]
    result = focus_song_locks("focus-song", "instruments", selected.id)
    assert next(x for x in result.instruments if x.id == selected.id).locked is False
    assert all(x.locked for x in result.instruments if x.id != selected.id)
    assert all(x.locked for x in result.lyric_lines)
    assert all(x.locked for x in result.sections)
