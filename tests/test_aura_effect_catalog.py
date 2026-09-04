from __future__ import annotations

import sqlite3

import pytest

from aura_music_studio.aura_effect_catalog import AuraEffectCatalog, CatalogMetadata
from aura_music_studio.aura_effect_graph import (
    AuraEffectGraphComposer,
    EffectGraph,
    GraphDomain,
    GraphEdge,
    GraphNode,
    GraphProvenance,
    ParameterSpec,
    PortSpec,
    PrimitiveRegistry,
    PrimitiveSpec,
    ResourceCost,
    RuntimeContext,
    graph_digest,
)


def _registry() -> PrimitiveRegistry:
    return PrimitiveRegistry(
        [
            PrimitiveSpec(
                id="music.source",
                name="Source",
                domains=frozenset({GraphDomain.MUSIC}),
                execution_kind="adapter",
                outputs={"audio": PortSpec("audio.buffer")},
            ),
            PrimitiveSpec(
                id="music.warmth",
                name="Warmth",
                domains=frozenset({GraphDomain.MUSIC}),
                execution_kind="transform",
                inputs={"audio": PortSpec("audio.buffer")},
                outputs={"audio": PortSpec("audio.buffer")},
                parameters={"amount": ParameterSpec("number", default=0.5, minimum=0.0, maximum=1.0)},
                required_entitlements=frozenset({"effects.standard"}),
                required_renderers=frozenset({"audio.local"}),
                effect_sku_id="effects.music_warmth",
                resource_cost=ResourceCost(cpu_units=2, memory_mb=32, estimated_ms=5),
            ),
            PrimitiveSpec(
                id="video.glow",
                name="Glow",
                domains=frozenset({GraphDomain.VIDEO}),
                execution_kind="transform",
                implementation_state="contract_ready",
            ),
        ]
    )


def _composer() -> AuraEffectGraphComposer:
    return AuraEffectGraphComposer(_registry())


def _context() -> RuntimeContext:
    return RuntimeContext(
        entitlements=frozenset({"effects.standard"}),
        renderers=frozenset({"audio.local"}),
    )


def _graph(*, amount: float = 0.5, title: str = "Warm Vocal", tags: tuple[str, ...] = ("warm", "vocal")) -> EffectGraph:
    return EffectGraph(
        id="user.warm_vocal",
        domain=GraphDomain.MUSIC,
        title=title,
        description="Reusable warmth chain",
        tags=tags,
        nodes=(
            GraphNode("source", "music.source"),
            GraphNode("warmth", "music.warmth", {"amount": amount}),
        ),
        edges=(GraphEdge("source", "audio", "warmth", "audio"),),
        provenance=GraphProvenance(
            author_id="user-1",
            source="aura",
            licence="user_original",
            rights_state="cleared",
            source_assets=("asset:vocal-1",),
            source_prompt="Make this warmer",
        ),
    )


def test_valid_draft_roundtrips_with_provenance_requirements_and_metadata(tmp_path):
    catalog = AuraEffectCatalog(tmp_path / "catalog.sqlite3")
    draft = catalog.save_draft(
        _graph(),
        composer=_composer(),
        context=_context(),
        scope="user",
        scope_id="user-1",
        owner_user_id="user-1",
        metadata=CatalogMetadata(
            category="audio_fx",
            commercial_use_state="allowed",
            accessibility={"reduced_motion": "not_applicable"},
            safety={"voice_consent": "not_required"},
        ),
    )

    assert draft.version == 1
    assert draft.state == "draft"
    assert draft.validation_valid is True
    assert draft.validation["requirements"]["effect_skus"] == ["effects.music_warmth"]
    assert draft.validation["requirements"]["entitlements"] == ["effects.standard"]
    assert draft.commercial_use_state == "allowed"
    assert draft.accessibility["reduced_motion"] == "not_applicable"

    loaded = catalog.load_graph(draft.catalog_id, 1)
    assert loaded.provenance.author_id == "user-1"
    assert loaded.provenance.source_prompt == "Make this warmer"
    assert loaded.provenance.source_assets == ("asset:vocal-1",)
    assert graph_digest(loaded) == draft.graph_digest


def test_draft_versions_are_immutable_and_optimistic_conflicts_fail_closed(tmp_path):
    catalog = AuraEffectCatalog(tmp_path / "catalog.sqlite3")
    composer = _composer()
    first = catalog.save_draft(
        _graph(amount=0.25, title="Warm Vocal"),
        composer=composer,
        context=_context(),
        scope="user",
        scope_id="user-1",
        owner_user_id="user-1",
        expected_latest_version=0,
    )
    second = catalog.save_draft(
        _graph(amount=0.75, title="Warmer Vocal"),
        composer=composer,
        context=_context(),
        scope="user",
        scope_id="user-1",
        owner_user_id="user-1",
        expected_latest_version=1,
        metadata=CatalogMetadata(migration_from_version=1),
    )

    assert first.version == 1
    assert second.version == 2
    assert catalog.load_graph(first.catalog_id, 1).nodes[1].parameters["amount"] == 0.25
    assert catalog.load_graph(first.catalog_id, 2).nodes[1].parameters["amount"] == 0.75
    history = catalog.history(first.catalog_id)
    assert [item.version for item in history] == [2, 1]
    assert history[0].title == "Warmer Vocal"
    assert history[1].title == "Warm Vocal"

    with pytest.raises(ValueError, match="version conflict"):
        catalog.save_draft(
            _graph(amount=0.9),
            composer=composer,
            context=_context(),
            scope="user",
            scope_id="user-1",
            owner_user_id="user-1",
            expected_latest_version=1,
        )


def test_migration_source_must_reference_real_earlier_version(tmp_path):
    catalog = AuraEffectCatalog(tmp_path / "catalog.sqlite3")
    with pytest.raises(ValueError, match="Migration source version does not exist"):
        catalog.save_draft(
            _graph(),
            composer=_composer(),
            context=_context(),
            scope="user",
            scope_id="user-1",
            owner_user_id="user-1",
            metadata=CatalogMetadata(migration_from_version=99),
        )


def test_category_scope_domain_and_owner_are_stable_for_existing_catalog_id(tmp_path):
    catalog = AuraEffectCatalog(tmp_path / "catalog.sqlite3")
    catalog.save_draft(
        _graph(),
        composer=_composer(),
        context=_context(),
        scope="user",
        scope_id="user-1",
        owner_user_id="user-1",
        metadata=CatalogMetadata(category="audio_fx"),
    )
    with pytest.raises(ValueError, match="category cannot change"):
        catalog.save_draft(
            _graph(title="Reclassified"),
            composer=_composer(),
            context=_context(),
            scope="user",
            scope_id="user-1",
            owner_user_id="user-1",
            metadata=CatalogMetadata(category="mastering"),
        )


def test_non_finite_parameter_values_cannot_enter_persistent_catalogue(tmp_path):
    catalog = AuraEffectCatalog(tmp_path / "catalog.sqlite3")
    with pytest.raises(ValueError, match="strict finite JSON"):
        catalog.save_draft(
            _graph(amount=float("nan")),
            composer=_composer(),
            context=_context(),
            scope="user",
            scope_id="user-1",
            owner_user_id="user-1",
        )


def test_invalid_drafts_can_be_saved_for_editing_but_cannot_publish(tmp_path):
    catalog = AuraEffectCatalog(tmp_path / "catalog.sqlite3")
    composer = _composer()
    invalid = EffectGraph(
        id="user.unwired",
        domain=GraphDomain.MUSIC,
        nodes=(GraphNode("warmth", "music.warmth"),),
        edges=(),
        provenance=_graph().provenance,
    )
    draft = catalog.save_draft(
        invalid,
        composer=composer,
        context=_context(),
        scope="user",
        scope_id="user-1",
        owner_user_id="user-1",
    )

    assert draft.validation_valid is False
    assert any(issue["code"] == "required_input_missing" for issue in draft.validation["issues"])
    with pytest.raises(ValueError, match="required_input_missing"):
        catalog.publish(draft.catalog_id, draft.version, composer=composer, context=_context())


def test_publish_revalidates_current_dependencies_and_content_then_deprecates(tmp_path):
    catalog = AuraEffectCatalog(tmp_path / "catalog.sqlite3")
    composer = _composer()
    draft = catalog.save_draft(
        _graph(),
        composer=composer,
        context=_context(),
        scope="esp",
        scope_id="esp",
        owner_user_id=None,
    )

    with pytest.raises(ValueError, match="missing_entitlement"):
        catalog.publish(
            draft.catalog_id,
            draft.version,
            composer=composer,
            context=RuntimeContext(renderers=frozenset({"audio.local"})),
        )

    published = catalog.publish(draft.catalog_id, draft.version, composer=composer, context=_context())
    assert published.state == "published"
    assert published.published_at is not None
    with pytest.raises(ValueError, match="Only draft"):
        catalog.publish(draft.catalog_id, draft.version, composer=composer, context=_context())

    deprecated = catalog.deprecate(draft.catalog_id, draft.version, "Superseded by a safer chain")
    assert deprecated.state == "deprecated"
    assert deprecated.deprecated_reason == "Superseded by a safer chain"


def test_non_executable_catalog_item_cannot_publish_even_if_graph_shape_is_valid(tmp_path):
    registry = PrimitiveRegistry(
        [
            PrimitiveSpec(
                id="video.future_glow",
                name="Future glow",
                domains=frozenset({GraphDomain.VIDEO}),
                execution_kind="transform",
                implementation_state="contract_ready",
            )
        ]
    )
    composer = AuraEffectGraphComposer(registry)
    graph = EffectGraph(
        id="esp.future_glow",
        domain=GraphDomain.VIDEO,
        nodes=(GraphNode("glow", "video.future_glow"),),
        edges=(),
        provenance=GraphProvenance(
            author_id="esp",
            source="esp",
            licence="esp_original",
            rights_state="cleared",
        ),
    )
    catalog = AuraEffectCatalog(tmp_path / "catalog.sqlite3")
    draft = catalog.save_draft(
        graph,
        composer=composer,
        context=RuntimeContext(executable_states=frozenset({"contract_ready"})),
        scope="esp",
        scope_id="esp",
        owner_user_id=None,
        metadata=CatalogMetadata(implementation_state="contract_ready"),
    )
    assert draft.validation_valid is True
    with pytest.raises(ValueError, match="Only executable"):
        catalog.publish(
            draft.catalog_id,
            draft.version,
            composer=composer,
            context=RuntimeContext(executable_states=frozenset({"contract_ready"})),
        )


def test_preview_lifecycle_requires_artifact_for_ready_state(tmp_path):
    catalog = AuraEffectCatalog(tmp_path / "catalog.sqlite3")
    draft = catalog.save_draft(
        _graph(),
        composer=_composer(),
        context=_context(),
        scope="project",
        scope_id="project-42",
        owner_user_id="user-1",
    )

    pending = catalog.set_preview(draft.catalog_id, draft.version, preview_state="pending")
    assert pending.preview_state == "pending"
    with pytest.raises(ValueError, match="artifact reference"):
        catalog.set_preview(draft.catalog_id, draft.version, preview_state="ready")
    ready = catalog.set_preview(
        draft.catalog_id,
        draft.version,
        preview_state="ready",
        preview_ref="artifact:preview-123",
    )
    assert ready.preview_state == "ready"
    assert ready.preview_ref == "artifact:preview-123"


def test_search_filters_scope_domain_state_tags_and_version_specific_text(tmp_path):
    catalog = AuraEffectCatalog(tmp_path / "catalog.sqlite3")
    composer = _composer()
    first = catalog.save_draft(
        _graph(title="Warm Vocal"),
        composer=composer,
        context=_context(),
        scope="user",
        scope_id="user-1",
        owner_user_id="user-1",
        metadata=CatalogMetadata(category="audio_fx"),
    )
    catalog.publish(first.catalog_id, first.version, composer=composer, context=_context())
    catalog.save_draft(
        _graph(title="Completely Renamed"),
        composer=composer,
        context=_context(),
        scope="user",
        scope_id="user-1",
        owner_user_id="user-1",
        metadata=CatalogMetadata(category="audio_fx", migration_from_version=1),
    )

    other = EffectGraph(
        id="user.other_effect",
        domain=GraphDomain.MUSIC,
        title="Clean Presence",
        description="Different processor",
        tags=("clean",),
        nodes=_graph().nodes,
        edges=_graph().edges,
        provenance=_graph().provenance,
    )
    catalog.save_draft(
        other,
        composer=composer,
        context=_context(),
        scope="user",
        scope_id="user-1",
        owner_user_id="user-1",
        metadata=CatalogMetadata(category="audio_fx"),
    )

    found = catalog.search(
        scope="user",
        scope_id="user-1",
        domain="music",
        state="published",
        tags=("warm",),
        text="warm vocal",
    )
    assert [(item.catalog_id, item.version, item.title) for item in found] == [
        ("user.warm_vocal", 1, "Warm Vocal")
    ]


def test_user_scope_cannot_impersonate_another_owner(tmp_path):
    catalog = AuraEffectCatalog(tmp_path / "catalog.sqlite3")
    with pytest.raises(ValueError, match="must match the owner"):
        catalog.save_draft(
            _graph(),
            composer=_composer(),
            context=_context(),
            scope="user",
            scope_id="user-2",
            owner_user_id="user-1",
        )


def test_catalogue_does_not_create_or_mutate_creation_coin_wallet_tables(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    catalog = AuraEffectCatalog(db)
    catalog.save_draft(
        _graph(),
        composer=_composer(),
        context=_context(),
        scope="marketplace",
        scope_id="marketplace",
        owner_user_id="user-1",
        metadata=CatalogMetadata(commercial_use_state="unknown"),
    )

    with sqlite3.connect(db) as con:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "credit_wallet" not in tables
    assert "credit_transactions" not in tables
    assert "creation_coin_transactions" not in tables
