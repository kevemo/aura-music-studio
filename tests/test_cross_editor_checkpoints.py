from __future__ import annotations

import json

from aura_music_studio.revisions import compare_revisions, create_revision, restore_revision


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def test_checkpoint_captures_and_restores_daw_and_professional_editor_together(tmp_path):
    project = tmp_path / "Song"
    project.mkdir()
    session = project / "aura_session.json"
    manifest = project / "creative_manifest.json"
    editor = project / "work" / "pro_editor.json"
    media = project / "output" / "video" / "master.mp4"

    _write_json(session, {"name": "Song", "tracks": [{"name": "Lead", "volume_db": 0}]})
    _write_json(manifest, {"elements": [{"id": "vid_1", "kind": "video"}], "active_element_ids": ["vid_1"]})
    _write_json(editor, {"active_branch_id": "main", "items": [{"id": "clip_1", "start": 0, "duration": 5}]})
    media.parent.mkdir(parents=True, exist_ok=True)
    original_media = b"not-real-video-but-immutable-media-sentinel"
    media.write_bytes(original_media)

    first = create_revision(project, label="Version A", actor="Tester", keep=20)
    assert first["daw_included"] is True
    assert first["creative_manifest_included"] is True
    assert first["professional_editor_included"] is True
    assert first["media_copied"] is False
    assert {item["path"] for item in first["files"]} >= {
        "aura_session.json",
        "creative_manifest.json",
        "work/pro_editor.json",
    }

    _write_json(session, {"name": "Song", "tracks": [{"name": "Lead", "volume_db": -9}]})
    _write_json(editor, {"active_branch_id": "main", "items": [{"id": "clip_1", "start": 2, "duration": 3}]})
    second = create_revision(project, label="Version B", actor="Tester", keep=20)

    comparison = compare_revisions(project, first["id"], second["id"])
    assert comparison["daw_changed"] is True
    assert comparison["professional_editor_changed"] is True
    assert comparison["creative_manifest_changed"] is False
    assert comparison["media_files_compared"] is False
    assert comparison["media_files_duplicated"] is False

    result = restore_revision(project, first["id"], create_backup=False, keep=20)
    assert "music_daw" in result["restored_domains"]
    assert "professional_image_video_editor" in result["restored_domains"]
    assert result["source_media_mutated"] is False
    assert json.loads(session.read_text(encoding="utf-8"))["tracks"][0]["volume_db"] == 0
    assert json.loads(editor.read_text(encoding="utf-8"))["items"][0]["start"] == 0
    assert media.read_bytes() == original_media


def test_older_revision_without_domain_metadata_remains_readable(tmp_path):
    project = tmp_path / "Legacy"
    project.mkdir()
    _write_json(project / "aura_session.json", {"name": "Legacy"})
    revision = create_revision(project, label="Current format", keep=5)
    meta = project / "work" / "revisions" / revision["id"] / "revision.json"
    value = json.loads(meta.read_text(encoding="utf-8"))
    value.pop("domains", None)
    value.pop("cross_editor_checkpoint", None)
    meta.write_text(json.dumps(value), encoding="utf-8")

    # A later comparison/get automatically derives domains from legacy file entries.
    same = compare_revisions(project, revision["id"], revision["id"])
    assert same["daw_changed"] is False
    assert same["files"]["changed"] == []
