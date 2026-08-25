from __future__ import annotations

from aura_music_studio.creative_project import CreativeElement, CreativeProjectStore


def test_historical_version_metadata_edits_do_not_reactivate_it(tmp_path):
    store = CreativeProjectStore(tmp_path / "history-guard")
    store.initialize(project_name="history-guard", title="History Guard")
    first = CreativeElement(kind="image", label="Cover v1", status="ready", source_ref="output/v1.png")
    store.add_element(first)
    second = CreativeElement(
        kind="image",
        label="Cover v2",
        status="ready",
        source_ref="output/v2.png",
        parent_ids=[first.id],
    )
    store.add_element(second)
    promoted = store.activate_element_version(second.id)
    assert second.id in promoted.active_element_ids
    assert first.id not in promoted.active_element_ids

    edited = store.update_element(first.id, label="Cover v1 — notes updated", metadata={"note": "retain for comparison"})
    assert second.id in edited.active_element_ids
    assert first.id not in edited.active_element_ids
    old = next(item for item in edited.elements if item.id == first.id)
    assert old.label == "Cover v1 — notes updated"
    assert old.metadata["note"] == "retain for comparison"


def test_explicit_archived_to_ready_transition_can_restore_element(tmp_path):
    store = CreativeProjectStore(tmp_path / "archive-restore")
    store.initialize(project_name="archive-restore", title="Archive Restore")
    element = CreativeElement(kind="audio", label="Take", status="ready", source_ref="output/take.wav")
    store.add_element(element)
    archived = store.update_element(element.id, status="archived")
    assert element.id not in archived.active_element_ids

    metadata_only = store.update_element(element.id, metadata={"note": "still archived"})
    assert element.id not in metadata_only.active_element_ids

    restored = store.update_element(element.id, status="ready")
    assert element.id in restored.active_element_ids
