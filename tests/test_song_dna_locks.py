from __future__ import annotations

import pytest

from aura_music_studio import tenant_storage
from aura_music_studio.brand_migration import inject_song_dna_lock_entry
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.song_dna import SongDNAStore, create_song_dna
from aura_music_studio.song_dna_locks import (
    lock_snapshot,
    router,
    set_all_song_locks,
    set_song_lock,
)


def _song(tmp_path, monkeypatch):
    root = (tmp_path / "projects").resolve()
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tenant_storage, "ROOT", root)
    token = set_current_user_id("song-lock-user")
    project = tenant_storage.project_path("locked-song", must_exist=False)
    project.mkdir(parents=True, exist_ok=True)
    dna = create_song_dna(
        project,
        project_name="locked-song",
        title="Locked Song",
        structure="Verse, Chorus",
        lyrics="[Verse]\nKeep this first line\n\n[Chorus]\nKeep this chorus line",
        instruments=["Piano", "Bass"],
    )
    return token, SongDNAStore(project), dna


def test_song_dna_locks_are_enforced_by_existing_exact_edit_methods(tmp_path, monkeypatch):
    token, store, dna = _song(tmp_path, monkeypatch)
    try:
        lyric = dna.lyric_lines[0]
        section = dna.sections[0]
        instrument = dna.instruments[0]

        set_song_lock("locked-song", "lyrics", lyric.id, True)
        with pytest.raises(ValueError, match="locked"):
            store.update_lyric_line(lyric.id, "Changed while locked")
        set_song_lock("locked-song", "lyrics", lyric.id, False)
        updated = store.update_lyric_line(lyric.id, "Allowed after unlock")
        assert next(item for item in updated.lyric_lines if item.id == lyric.id).text == "Allowed after unlock"

        set_song_lock("locked-song", "sections", section.id, True)
        with pytest.raises(ValueError, match="locked"):
            store.plan_section_regeneration(section.id, "Make this section louder")

        set_song_lock("locked-song", "instruments", instrument.id, True)
        with pytest.raises(ValueError, match="locked"):
            store.plan_instrument_replacement(instrument.id, "Electric piano")
    finally:
        reset_current_user_id(token)


def test_bulk_song_locks_change_metadata_only_and_are_reported(tmp_path, monkeypatch):
    token, store, _dna = _song(tmp_path, monkeypatch)
    try:
        before = store.load().version
        locked = set_all_song_locks("locked-song", "sections", True)
        assert locked.version == before + 1
        snapshot = lock_snapshot(locked)
        assert snapshot["sections_locked"] == len(snapshot["sections"])
        assert snapshot["audio_modified"] is False
        assert snapshot["non_destructive"] is True
        assert locked.metadata["lock_events"][-1]["bulk"] is True
        assert locked.metadata["lock_events"][-1]["target_id"] == "*"

        same = set_all_song_locks("locked-song", "sections", True)
        assert same.version == locked.version
    finally:
        reset_current_user_id(token)


def test_preserve_lock_routes_and_editor_entry_are_discoverable():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/projects/{project_name}/song-dna/locks" in paths
    assert "/projects/{project_name}/song-dna/locks/{kind}/{target_id}" in paths
    assert "/projects/{project_name}/song-dna/locks/{kind}" in paths
    assert "/song-editor/{project_name}/locks" in paths

    source = "<html><body><h1>Song editor</h1></body></html>"
    injected = inject_song_dna_lock_entry(source, "/song-editor/my-song")
    assert "id='songDnaLocksEntry'" in injected
    assert "href='/song-editor/my-song/locks'" in injected
    assert "🔒 Preserve Locks" in injected
    assert inject_song_dna_lock_entry(injected, "/song-editor/my-song").count("songDnaLocksEntry") == 1
    assert inject_song_dna_lock_entry(source, "/song-editor") == source
    assert inject_song_dna_lock_entry(source, "/song-editor/my-song/locks") == source
