from __future__ import annotations

import aura_music_studio.creative_version_autopromotion as creative_overlay
import aura_music_studio.professional_editor_lifecycle_api as lifecycle
from aura_music_studio.professional_editor import EditorEffect, EditorMask, ProfessionalEditorStore


def _project(tmp_path):
    project = tmp_path / "EditorLifecycle"
    source = project / "input" / "source.bin"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"immutable-source-media")
    store = ProfessionalEditorStore(project)
    store.initialize("EditorLifecycle")
    sequence = store.create_sequence(kind="image", name="Artwork", width=400, height=400)
    track = store.create_track(sequence.id, kind="image", name="Layers")
    item = store.create_item(track.id, kind="image_layer", name="Layer", source_ref="input/source.bin", duration=1.0)
    return project, source, store, sequence, track, item


def _item(store: ProfessionalEditorStore, item_id: str):
    return store.load_item(item_id)


def _track(store: ProfessionalEditorStore, track_id: str):
    branch = store.public_state()["branch"]
    return next(value for value in branch["tracks"] if value["id"] == track_id)


def test_mask_patch_delete_and_undo_are_non_destructive(tmp_path):
    _project_dir, source, store, _sequence, _track_row, item = _project(tmp_path)
    before_media = source.read_bytes()
    mask = store.add_mask(
        item.id,
        EditorMask(name="Face", shape="ellipse", points=[(0.2, 0.2), (0.8, 0.8)], feather=4.0),
    )

    updated = lifecycle.patch_mask_graph(
        store,
        item.id,
        mask.id,
        {"name": "Face Soft", "feather": 18.0, "inverted": True, "opacity": 0.75},
        actor="Test",
    )
    assert updated.name == "Face Soft"
    assert updated.feather == 18.0
    assert updated.inverted is True
    assert updated.opacity == 0.75

    store.undo()
    restored = next(value for value in _item(store, item.id).masks if value.id == mask.id)
    assert restored.name == "Face"
    assert restored.feather == 4.0
    assert restored.inverted is False

    lifecycle.delete_mask_graph(store, item.id, mask.id, actor="Test")
    assert not _item(store, item.id).masks
    store.undo()
    assert any(value.id == mask.id for value in _item(store, item.id).masks)
    assert source.read_bytes() == before_media


def test_effect_patch_reorder_delete_and_undo(tmp_path):
    _project_dir, source, store, _sequence, track, item = _project(tmp_path)
    before_media = source.read_bytes()
    first = store.add_effect("item", item.id, EditorEffect(type="brightness", mix=0.4, parameters={"amount": 1.1}))
    second = store.add_effect("item", item.id, EditorEffect(type="contrast", mix=0.8, parameters={"amount": 1.2}))
    third = store.add_effect("item", item.id, EditorEffect(type="vignette", mix=1.0, parameters={"strength": 0.5}))

    updated = lifecycle.patch_effect_graph(
        store,
        "item",
        item.id,
        second.id,
        {"mix": 0.25, "parameters": {"amount": 1.5}},
        actor="Test",
    )
    assert updated.mix == 0.25
    assert updated.parameters["amount"] == 1.5
    store.undo()
    restored_second = next(value for value in _item(store, item.id).effects if value.id == second.id)
    assert restored_second.mix == 0.8
    assert restored_second.parameters["amount"] == 1.2

    reordered = lifecycle.reorder_effect_graph(store, "item", item.id, third.id, 0, actor="Test")
    assert [value.id for value in reordered] == [third.id, first.id, second.id]
    store.undo()
    assert [value.id for value in _item(store, item.id).effects] == [first.id, second.id, third.id]

    lifecycle.delete_effect_graph(store, "item", item.id, first.id, actor="Test")
    assert first.id not in [value.id for value in _item(store, item.id).effects]
    store.undo()
    assert first.id in [value.id for value in _item(store, item.id).effects]

    track_fx = store.add_effect("track", track.id, EditorEffect(type="grayscale", mix=0.5))
    lifecycle.patch_effect_graph(store, "track", track.id, track_fx.id, {"enabled": False}, actor="Test")
    track_state = _track(store, track.id)
    assert next(value for value in track_state["effects"] if value["id"] == track_fx.id)["enabled"] is False
    assert source.read_bytes() == before_media


def test_keyframe_lane_delete_and_undo(tmp_path):
    _project_dir, _source, store, _sequence, _track_row, item = _project(tmp_path)
    store.set_item_keyframes(
        item.id,
        "transform.x",
        [
            {"time": 0.0, "value": -10.0, "interpolation": "linear"},
            {"time": 1.0, "value": 10.0, "interpolation": "smooth"},
        ],
    )
    removed = lifecycle.delete_keyframe_lane_graph(store, item.id, "transform.x", actor="Test")
    assert removed == 2
    assert "transform.x" not in _item(store, item.id).keyframes
    store.undo()
    assert len(_item(store, item.id).keyframes["transform.x"]) == 2


def test_lifecycle_router_paths_and_shared_mount_are_explicit():
    paths = [route.path for route in lifecycle.router.routes]
    assert "/creative/projects/{project_name}/editor/items/{item_id}/masks/{mask_id}" in paths
    assert "/creative/projects/{project_name}/editor/{target_type}/{target_id}/effects/{effect_id}" in paths
    assert "/creative/projects/{project_name}/editor/{target_type}/{target_id}/effects/{effect_id}/reorder" in paths
    assert "/creative/projects/{project_name}/editor/items/{item_id}/keyframes/{parameter}" in paths
    assert creative_overlay.professional_editor_lifecycle_router is lifecycle.router
