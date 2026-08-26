from __future__ import annotations

from aura_music_studio.creative_project import CreativeDirective, CreativeElement, CreativeProjectStore
from aura_music_studio.creative_version_autopromotion import auto_promote_single_target_revision, router


def _store(tmp_path):
    store = CreativeProjectStore(tmp_path / "creative")
    store.initialize(project_name="creative", title="Creative")
    original = CreativeElement(kind="image", label="Cover v1", status="ready", source_ref="output/v1.png")
    store.add_element(original)
    return store, original


def test_single_target_single_revision_auto_promotes_without_deleting_history(tmp_path):
    store, original = _store(tmp_path)
    revision = CreativeElement(
        kind="image",
        label="Cover v2",
        status="ready",
        source_ref="output/v2.png",
        parent_ids=[original.id],
    )
    store.add_element(revision)
    directive = CreativeDirective(
        instruction="Make the title brighter and preserve everything else",
        operation="revise",
        target_kind="image",
        target_element_ids=[original.id],
    )

    result = auto_promote_single_target_revision(
        store,
        directive,
        [revision.model_dump(mode="json")],
        target_was_current=True,
    )

    manifest = store.load()
    assert result["promoted"] is True
    assert revision.id in manifest.active_element_ids
    assert original.id not in manifest.active_element_ids
    assert {item.id for item in manifest.elements} == {original.id, revision.id}
    family = store.version_family(revision.id)
    assert {row["id"] for row in family["elements"]} == {original.id, revision.id}
    assert family["current_ids"] == [revision.id]


def test_multiple_alternatives_never_auto_select(tmp_path):
    store, original = _store(tmp_path)
    first = CreativeElement(kind="image", label="A", status="ready", parent_ids=[original.id])
    second = CreativeElement(kind="image", label="B", status="ready", parent_ids=[original.id])
    store.add_element(first)
    store.add_element(second)
    directive = CreativeDirective(
        instruction="Try two alternatives",
        operation="revise",
        target_kind="image",
        target_element_ids=[original.id],
    )

    result = auto_promote_single_target_revision(
        store,
        directive,
        [first.model_dump(mode="json"), second.model_dump(mode="json")],
        target_was_current=True,
    )

    assert result["promoted"] is False
    assert result["reason"] == "multiple_or_missing_outputs"
    assert original.id in store.load().active_element_ids


def test_create_operation_and_historical_target_remain_manual(tmp_path):
    store, original = _store(tmp_path)
    candidate = CreativeElement(kind="image", label="Candidate", status="ready", parent_ids=[original.id])
    store.add_element(candidate)
    create_directive = CreativeDirective(
        instruction="Create a new image",
        operation="create",
        target_kind="image",
        target_element_ids=[original.id],
    )
    assert auto_promote_single_target_revision(
        store, create_directive, [candidate.model_dump(mode="json")], target_was_current=True
    )["reason"] == "operation_not_revision_like"

    revise_directive = CreativeDirective(
        instruction="Revise old version",
        operation="revise",
        target_kind="image",
        target_element_ids=[original.id],
    )
    assert auto_promote_single_target_revision(
        store, revise_directive, [candidate.model_dump(mode="json")], target_was_current=False
    )["reason"] == "target_was_not_current"


def test_safe_version_promotion_router_overrides_sync_route():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/creative/projects/{project_name}/directives/{directive_id}/sync-outputs" in paths
