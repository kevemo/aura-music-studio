from pathlib import Path

import pytest

from aura_music_studio.creative_project import (
    CreativeDirective,
    CreativeElement,
    CreativeProjectStore,
    CreativeReference,
    public_capabilities,
)


def test_creative_manifest_round_trip_and_element_lineage(tmp_path: Path):
    project = tmp_path / "cosmic-single"
    store = CreativeProjectStore(project)
    manifest = store.initialize(
        project_name="cosmic-single",
        title="Cosmic Single",
        project_intent="Create the song, cover and music video as one connected release.",
    )
    assert manifest.schema_version == 1
    assert store.exists()

    vocal = CreativeElement(
        kind="voice",
        label="Lead vocal",
        role="timing and phrasing anchor",
        source_type="recorded",
        source_ref="asset_vocal_1",
        status="ready",
    )
    store.add_element(vocal)
    chorus = CreativeElement(
        kind="music",
        label="Final chorus",
        role="largest arrangement section",
        parent_ids=[vocal.id],
    )
    store.add_element(chorus)

    loaded = store.load()
    assert [item.label for item in loaded.elements] == ["Lead vocal", "Final chorus"]
    assert loaded.elements[1].parent_ids == [vocal.id]
    assert set(loaded.active_element_ids) == {vocal.id, chorus.id}


def test_authorised_references_are_required(tmp_path: Path):
    store = CreativeProjectStore(tmp_path / "project")
    store.initialize(project_name="project", title="Project")

    with pytest.raises(ValueError):
        CreativeReference(
            kind="image",
            label="Cover reference",
            source_ref="asset_cover_ref",
            rights_confirmed=False,
        )

    reference = CreativeReference(
        kind="image",
        label="Authorised visual reference",
        source_ref="asset_visual_1",
        rights_confirmed=True,
    )
    manifest = store.add_reference(reference)
    assert manifest.references[0].rights_confirmed is True


def test_connected_audio_directive_becomes_renderer_ready(tmp_path: Path):
    store = CreativeProjectStore(tmp_path / "song")
    store.initialize(project_name="song", title="Song")
    chorus = CreativeElement(kind="music", label="Final chorus")
    store.add_element(chorus)

    directive = CreativeDirective(
        instruction="Make only the final chorus bigger and keep every other section unchanged.",
        operation="revise",
        target_element_ids=[chorus.id],
    )
    manifest = store.add_directive(directive)
    saved = manifest.directives[-1]
    assert saved.target_kind == "music"
    assert saved.capability_state == "connected"
    assert saved.renderer_route == "music_audio_stack"
    assert saved.status == "ready_for_renderer"


def test_video_and_image_directives_do_not_pretend_renderers_are_live(tmp_path: Path):
    store = CreativeProjectStore(tmp_path / "visual")
    store.initialize(project_name="visual", title="Visual")

    video = CreativeDirective(
        instruction="Create a scene-by-scene music video plan.",
        operation="storyboard",
        target_kind="video",
    )
    image = CreativeDirective(
        instruction="Replace only the background of the cover artwork.",
        operation="replace",
        target_kind="image",
    )
    store.add_directive(video)
    manifest = store.add_directive(image)

    assert manifest.directives[-2].status == "planned"
    assert manifest.directives[-2].capability_state == "integration_slot"
    assert manifest.directives[-1].status == "planned"
    assert manifest.directives[-1].capability_state == "integration_slot"


def test_directive_scope_must_reference_real_project_elements(tmp_path: Path):
    store = CreativeProjectStore(tmp_path / "scoped")
    store.initialize(project_name="scoped", title="Scoped")
    directive = CreativeDirective(
        instruction="Change this element.",
        target_kind="music",
        target_element_ids=["el_missing"],
    )
    with pytest.raises(ValueError, match="Unknown target element"):
        store.add_directive(directive)


def test_archiving_removes_element_from_active_selection(tmp_path: Path):
    store = CreativeProjectStore(tmp_path / "archive")
    store.initialize(project_name="archive", title="Archive")
    element = CreativeElement(kind="image", label="Old cover")
    store.add_element(element)
    manifest = store.update_element(element.id, status="archived", metadata={"reason": "superseded"})
    updated = manifest.elements[0]
    assert updated.status == "archived"
    assert updated.metadata["reason"] == "superseded"
    assert element.id not in manifest.active_element_ids


def test_capability_registry_exposes_connected_and_staged_media_truthfully():
    capabilities = public_capabilities()
    assert capabilities["music"]["state"] == "connected"
    assert capabilities["audio"]["state"] == "connected"
    assert capabilities["video"]["state"] == "integration_slot"
    assert capabilities["image"]["state"] == "integration_slot"
