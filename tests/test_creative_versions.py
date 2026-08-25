from __future__ import annotations

from aura_music_studio.creative_media_preview import MEDIA_PREVIEW_SCRIPT, router
from aura_music_studio.creative_project import CreativeElement, CreativeProjectStore


def test_non_destructive_version_promotion_keeps_unrelated_elements_active(tmp_path):
    store = CreativeProjectStore(tmp_path / "version-project")
    store.initialize(project_name="version-project", title="Version Project")

    original = CreativeElement(
        kind="image",
        label="Cover v1",
        status="ready",
        source_ref="output/cover-v1.png",
    )
    unrelated = CreativeElement(
        kind="audio",
        label="Master audio",
        status="ready",
        source_ref="output/master.wav",
    )
    store.add_element(original)
    store.add_element(unrelated)
    revision = CreativeElement(
        kind="image",
        label="Cover v2",
        status="ready",
        source_ref="output/cover-v2.png",
        parent_ids=[original.id],
    )
    store.add_element(revision)

    before = store.load()
    assert {original.id, revision.id, unrelated.id}.issubset(set(before.active_element_ids))

    promoted = store.activate_element_version(revision.id)
    assert revision.id in promoted.active_element_ids
    assert original.id not in promoted.active_element_ids
    assert unrelated.id in promoted.active_element_ids

    by_id = {item.id: item for item in promoted.elements}
    assert by_id[original.id].metadata["version_root_id"] == original.id
    assert by_id[revision.id].metadata["version_root_id"] == original.id
    assert by_id[original.id].metadata["is_current_version"] is False
    assert by_id[revision.id].metadata["is_current_version"] is True
    assert by_id[original.id].source_ref == "output/cover-v1.png"
    assert by_id[revision.id].source_ref == "output/cover-v2.png"

    family = store.version_family(revision.id)
    assert family["version_root_id"] == original.id
    assert family["current_ids"] == [revision.id]
    assert {item["id"] for item in family["elements"]} == {original.id, revision.id}

    restored = store.activate_element_version(original.id)
    assert original.id in restored.active_element_ids
    assert revision.id not in restored.active_element_ids
    assert unrelated.id in restored.active_element_ids
    restored_by_id = {item.id: item for item in restored.elements}
    assert restored_by_id[original.id].metadata["is_current_version"] is True
    assert restored_by_id[revision.id].metadata["is_current_version"] is False


def test_archived_version_cannot_be_promoted(tmp_path):
    store = CreativeProjectStore(tmp_path / "archive-version")
    store.initialize(project_name="archive-version", title="Archive Version")
    element = CreativeElement(kind="video", label="Archived cut", status="ready")
    store.add_element(element)
    store.update_element(element.id, status="archived")

    try:
        store.activate_element_version(element.id)
    except ValueError as exc:
        assert "Archived Creative Elements" in str(exc)
    else:
        raise AssertionError("Archived version promotion should be rejected")


def test_media_gallery_exposes_current_history_and_manual_version_switch_without_deleting_media():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/creative/projects/{project_name}/elements/{element_id}/activate-version" in paths
    assert "CURRENT" in MEDIA_PREVIEW_SCRIPT
    assert "HISTORY" in MEDIA_PREVIEW_SCRIPT
    assert "Make current" in MEDIA_PREVIEW_SCRIPT
    assert "activate-version" in MEDIA_PREVIEW_SCRIPT
    assert "Previous media remains available in History" in MEDIA_PREVIEW_SCRIPT
    assert "does not delete retained media" in MEDIA_PREVIEW_SCRIPT
    assert "Revise with Aura" in MEDIA_PREVIEW_SCRIPT
