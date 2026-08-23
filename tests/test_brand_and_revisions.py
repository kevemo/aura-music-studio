from __future__ import annotations

from pathlib import Path

from aura_music_studio.brand_ui import ESP_THEME_CSS, LOGO_PATH
from aura_music_studio.plans import DEEP_REVISION_HISTORY, REVISION_HISTORY, get_plan
from aura_music_studio.revisions import create_revision, list_revisions, restore_revision


def test_official_esp_logo_asset_is_packaged():
    assert LOGO_PATH.is_file()
    assert LOGO_PATH.stat().st_size > 1_000
    assert "/brand/esp-logo.webp" in ESP_THEME_CSS


def test_revision_entitlements_are_progressive():
    assert not get_plan("free").has(REVISION_HISTORY)
    assert get_plan("base").has(REVISION_HISTORY)
    assert not get_plan("base").has(DEEP_REVISION_HISTORY)
    assert get_plan("pro").has(REVISION_HISTORY)
    assert get_plan("pro").has(DEEP_REVISION_HISTORY)


def test_revision_snapshots_metadata_not_audio(tmp_path: Path):
    project = tmp_path / "song"
    project.mkdir()
    (project / "work").mkdir()
    (project / "input").mkdir()
    (project / "project.yaml").write_text("project_name: song\ntitle: Test\n", encoding="utf-8")
    (project / "aura_session.json").write_text('{"name":"Before"}', encoding="utf-8")
    huge_audio = project / "input" / "vocal.wav"
    huge_audio.write_bytes(b"RIFF" + b"x" * 4096)

    rev = create_revision(project, label="Before edit", keep=20)
    assert rev["audio_copied"] is False
    assert {x["path"] for x in rev["files"]} == {"project.yaml", "aura_session.json"}
    rev_dir = project / "work" / "revisions" / rev["id"]
    assert not (rev_dir / "input" / "vocal.wav").exists()

    (project / "aura_session.json").write_text('{"name":"After"}', encoding="utf-8")
    restored = restore_revision(project, rev["id"], create_backup=True, keep=20)
    assert "aura_session.json" in restored["restored_files"]
    assert '"Before"' in (project / "aura_session.json").read_text(encoding="utf-8")
    assert len(list_revisions(project)) >= 2
