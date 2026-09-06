from __future__ import annotations

import json

from aura_music_studio.revisions import create_revision, restore_revision


def test_shared_revision_captures_and_restores_song_dna(tmp_path):
    project = tmp_path / "song-project"
    project.mkdir()
    song_dna = project / "song_dna.json"
    original = {"version": 7, "metadata": {"chord_progression": [{"id": "a", "symbol": "C"}]}}
    song_dna.write_text(json.dumps(original), encoding="utf-8")

    revision = create_revision(
        project,
        label="Before chord edit",
        reason="test_song_dna_revision",
        actor="Test",
        keep=5,
    )

    assert revision["song_dna_included"] is True
    assert revision["domains"]["music_song_dna"]["paths"] == ["song_dna.json"]
    assert any(item["path"] == "song_dna.json" for item in revision["files"])

    song_dna.write_text(json.dumps({"version": 8, "metadata": {"chord_progression": []}}), encoding="utf-8")
    restored = restore_revision(project, revision["id"], create_backup=False, keep=5)

    assert restored["song_dna_restored"] is True
    assert "song_dna.json" in restored["restored_files"]
    assert "music_song_dna" in restored["restored_domains"]
    assert json.loads(song_dna.read_text(encoding="utf-8")) == original
