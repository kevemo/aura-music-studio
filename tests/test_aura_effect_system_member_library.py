from __future__ import annotations

import json
from pathlib import Path

import pytest

from aura_music_studio.aura_effect_system_creator import EffectNodeSpec, compile_effect_system, make_effect_system
from aura_music_studio.aura_effect_system_member_library import (
    import_reusable_effect_system,
    list_reusable_effect_systems,
    load_reusable_effect_system,
    publish_project_effect_system,
    remove_reusable_effect_system,
)
from aura_music_studio.aura_effect_system_project import load_effect_system, save_effect_system


def _project(root: Path, name: str) -> Path:
    project = root / name
    project.mkdir(parents=True)
    return project


def _system(*, version: int = 1, gain_db: float = 2.0):
    return make_effect_system(
        "vocal-polish",
        "Vocal Polish",
        [
            EffectNodeSpec(
                id="gain",
                catalogue_item_id="music.fx.gain",
                parameters={"db": gain_db},
                mix=1.0,
            ),
            EffectNodeSpec(
                id="space",
                catalogue_item_id="music.fx.reverb",
                parameters={"predelay_ms": 30.0, "mix": 0.35},
                mix=0.25,
            ),
        ],
        description="Reusable vocal polish chain",
        version=version,
    )


def test_publish_list_load_and_truth_flags(tmp_path: Path):
    source = _project(tmp_path, "source")
    library = _project(tmp_path, "member-root")
    spec = _system()
    save_effect_system(source, spec)

    published = publish_project_effect_system(
        source,
        spec.id,
        tags=["Vocals", "warm", "vocals"],
        library_root=library,
    )

    assert published["published"] is True
    assert published["visibility"] == "private"
    assert published["marketplace_published"] is False
    assert published["sale_enabled"] is False
    assert published["backend_executable"] is True
    assert published["source_media_mutated"] is False
    assert published["tags"] == ["Vocals", "warm"]

    rows = list_reusable_effect_systems(library_root=library)
    assert len(rows) == 1
    assert rows[0]["item_id"] == "effect-system.vocal-polish"
    loaded = load_reusable_effect_system(rows[0]["item_id"], library_root=library)
    assert compile_effect_system(loaded).fingerprint == compile_effect_system(spec).fingerprint


def test_identical_publish_is_idempotent(tmp_path: Path):
    source = _project(tmp_path, "source")
    library = _project(tmp_path, "member-root")
    spec = _system()
    save_effect_system(source, spec)

    first = publish_project_effect_system(source, spec.id, tags=["mix"], library_root=library)
    second = publish_project_effect_system(source, spec.id, tags=["mix"], library_root=library)

    assert first["published"] is True
    assert second["published"] is False
    assert second["already_current"] is True
    assert second["fingerprint"] == first["fingerprint"]


def test_changed_same_version_cannot_replace_library_item(tmp_path: Path):
    library = _project(tmp_path, "member-root")
    source_a = _project(tmp_path, "source-a")
    source_b = _project(tmp_path, "source-b")
    original = _system(version=1, gain_db=2.0)
    changed = _system(version=1, gain_db=7.0)
    save_effect_system(source_a, original)
    save_effect_system(source_b, changed)
    publish_project_effect_system(source_a, original.id, library_root=library)

    with pytest.raises(ValueError, match="version increment"):
        publish_project_effect_system(source_b, changed.id, library_root=library)


def test_newer_version_can_replace_same_library_item(tmp_path: Path):
    library = _project(tmp_path, "member-root")
    source_a = _project(tmp_path, "source-a")
    source_b = _project(tmp_path, "source-b")
    v1 = _system(version=1, gain_db=2.0)
    v2 = _system(version=2, gain_db=7.0)
    save_effect_system(source_a, v1)
    save_effect_system(source_b, v2)
    first = publish_project_effect_system(source_a, v1.id, library_root=library)
    second = publish_project_effect_system(source_b, v2.id, library_root=library)

    assert second["published"] is True
    assert second["system"]["version"] == 2
    assert second["fingerprint"] != first["fingerprint"]
    loaded = load_reusable_effect_system(second["item_id"], library_root=library)
    assert loaded.version == 2


def test_older_version_cannot_replace_newer_library_item(tmp_path: Path):
    library = _project(tmp_path, "member-root")
    new_source = _project(tmp_path, "new-source")
    old_source = _project(tmp_path, "old-source")
    newer = _system(version=2, gain_db=7.0)
    older = _system(version=1, gain_db=2.0)
    save_effect_system(new_source, newer)
    save_effect_system(old_source, older)
    publish_project_effect_system(new_source, newer.id, library_root=library)

    with pytest.raises(ValueError, match="older"):
        publish_project_effect_system(old_source, older.id, library_root=library)


def test_import_reuses_normal_project_save_gate(tmp_path: Path):
    source = _project(tmp_path, "source")
    target = _project(tmp_path, "target")
    library = _project(tmp_path, "member-root")
    spec = _system()
    save_effect_system(source, spec)
    published = publish_project_effect_system(source, spec.id, library_root=library)

    imported = import_reusable_effect_system(target, published["item_id"], library_root=library)

    assert imported["imported_from_reusable_library"] is True
    assert imported["library_item_id"] == published["item_id"]
    assert imported["source_media_mutated"] is False
    target_spec = load_effect_system(target, spec.id)
    assert compile_effect_system(target_spec).fingerprint == published["fingerprint"]


def test_tampered_fingerprint_fails_load_and_is_hidden_from_list(tmp_path: Path):
    source = _project(tmp_path, "source")
    library = _project(tmp_path, "member-root")
    spec = _system()
    save_effect_system(source, spec)
    published = publish_project_effect_system(source, spec.id, library_root=library)

    path = library / ".aura_effect_system_library.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["items"][published["item_id"]]["fingerprint"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert list_reusable_effect_systems(library_root=library) == []
    with pytest.raises(ValueError, match="integrity"):
        load_reusable_effect_system(published["item_id"], library_root=library)


def test_remove_is_idempotent(tmp_path: Path):
    source = _project(tmp_path, "source")
    library = _project(tmp_path, "member-root")
    spec = _system()
    save_effect_system(source, spec)
    published = publish_project_effect_system(source, spec.id, library_root=library)

    removed = remove_reusable_effect_system(published["item_id"], library_root=library)
    absent = remove_reusable_effect_system(published["item_id"], library_root=library)

    assert removed == {"removed": True, "already_absent": False, "item_id": published["item_id"]}
    assert absent == {"removed": False, "already_absent": True, "item_id": published["item_id"]}
    assert list_reusable_effect_systems(library_root=library) == []


@pytest.mark.parametrize("item_id", ["../escape", "bad/name", "", ".", ".."])
def test_library_item_id_is_path_safe(tmp_path: Path, item_id: str):
    source = _project(tmp_path, "source")
    library = _project(tmp_path, "member-root")
    spec = _system()
    save_effect_system(source, spec)

    with pytest.raises(ValueError, match="item id"):
        publish_project_effect_system(source, spec.id, item_id=item_id, library_root=library)


def test_invalid_tags_fail_before_library_write(tmp_path: Path):
    source = _project(tmp_path, "source")
    library = _project(tmp_path, "member-root")
    spec = _system()
    save_effect_system(source, spec)

    with pytest.raises(ValueError, match="tag"):
        publish_project_effect_system(source, spec.id, tags=["bad/tag"], library_root=library)
    assert not (library / ".aura_effect_system_library.json").exists()
