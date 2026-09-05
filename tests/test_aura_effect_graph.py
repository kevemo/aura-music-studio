from __future__ import annotations

import pytest

from aura_music_studio.aura_effect_graph import (
    AuraEffectGraphComposer,
    EffectGraph,
    GraphDomain,
    GraphEdge,
    GraphLimits,
    GraphNode,
    GraphProvenance,
    ParameterSpec,
    PortSpec,
    PrimitiveRegistry,
    PrimitiveSpec,
    ResourceCost,
    RuntimeContext,
    graph_canonical_json,
    graph_digest,
)


def _provenance(prompt: str = "Make it warmer") -> GraphProvenance:
    return GraphProvenance(
        author_id="user_test",
        source="aura",
        licence="user_original",
        rights_state="cleared",
        source_assets=("asset:track-1",),
        consent_requirements=(),
        source_prompt=prompt,
    )


def _registry() -> PrimitiveRegistry:
    return PrimitiveRegistry(
        [
            PrimitiveSpec(
                id="music.source",
                name="Audio source",
                domains=frozenset({GraphDomain.MUSIC, GraphDomain.VOICE}),
                execution_kind="adapter",
                outputs={"audio": PortSpec("audio.buffer")},
                resource_cost=ResourceCost(cpu_units=1, memory_mb=16, estimated_ms=2),
            ),
            PrimitiveSpec(
                id="music.gain",
                name="Gain",
                domains=frozenset({GraphDomain.MUSIC, GraphDomain.VOICE}),
                execution_kind="transform",
                inputs={"audio": PortSpec("audio.buffer")},
                outputs={"audio": PortSpec("audio.buffer")},
                parameters={
                    "gain_db": ParameterSpec("number", default=0.0, minimum=-60.0, maximum=24.0),
                },
                required_entitlements=frozenset({"effects.standard"}),
                required_renderers=frozenset({"audio.local"}),
                required_capabilities=frozenset({"audio.float32"}),
                resource_cost=ResourceCost(cpu_units=2, memory_mb=32, estimated_ms=5),
                effect_sku_id="effects.music_gain",
            ),
            PrimitiveSpec(
                id="video.glow",
                name="Glow",
                domains=frozenset({GraphDomain.VIDEO, GraphDomain.IMAGE}),
                execution_kind="transform",
                inputs={"frame": PortSpec("video.frame")},
                outputs={"frame": PortSpec("video.frame")},
                parameters={
                    "radius": ParameterSpec("number", default=8.0, minimum=0.0, maximum=128.0),
                },
                required_providers=frozenset({"video.local"}),
                resource_cost=ResourceCost(cpu_units=8, memory_mb=128, estimated_ms=30),
            ),
            PrimitiveSpec(
                id="shared.signal",
                name="Signal pass-through",
                domains=frozenset({GraphDomain.SHARED}),
                execution_kind="control",
                inputs={"signal": PortSpec("control.signal")},
                outputs={"signal": PortSpec("control.signal")},
            ),
        ]
    )


def _music_graph(*, gain: float | None = None) -> EffectGraph:
    parameters = {} if gain is None else {"gain_db": gain}
    return EffectGraph(
        id="user.warm_track",
        domain=GraphDomain.MUSIC,
        title="Warm track",
        nodes=(
            GraphNode("source", "music.source"),
            GraphNode("gain", "music.gain", parameters),
        ),
        edges=(GraphEdge("source", "audio", "gain", "audio"),),
        provenance=_provenance(),
    )


def _music_context(**kwargs) -> RuntimeContext:
    defaults = {
        "entitlements": frozenset({"effects.standard"}),
        "renderers": frozenset({"audio.local"}),
        "capabilities": frozenset({"audio.float32"}),
    }
    defaults.update(kwargs)
    return RuntimeContext(**defaults)


def test_valid_graph_is_deterministic_and_reports_requirements_without_charging():
    composer = AuraEffectGraphComposer(_registry())
    report = composer.validate(_music_graph(), _music_context())

    assert report.valid is True
    assert report.depth == 2
    assert report.requirements.entitlements == ("effects.standard",)
    assert report.requirements.renderers == ("audio.local",)
    assert report.requirements.effect_skus == ("effects.music_gain",)
    assert report.resource_cost.cpu_units == 3
    assert report.graph_digest == composer.validate(_music_graph(gain=0.0), _music_context()).graph_digest


def test_canonical_serialization_preserves_provenance_and_is_stable():
    graph = _music_graph(gain=3.5)
    payload = graph_canonical_json(graph)

    assert '"author_id":"user_test"' in payload
    assert '"source_prompt":"Make it warmer"' in payload
    assert '"source_assets":["asset:track-1"]' in payload
    assert graph_digest(graph) == graph_digest(graph)


def test_missing_entitlement_renderer_provider_or_capability_fails_closed():
    registry = _registry()
    composer = AuraEffectGraphComposer(registry)
    graph = EffectGraph(
        id="user.mixed_dependencies",
        domain=GraphDomain.MUSIC,
        nodes=(
            GraphNode("source", "music.source"),
            GraphNode("gain", "music.gain"),
        ),
        edges=(GraphEdge("source", "audio", "gain", "audio"),),
        provenance=_provenance(),
    )
    report = composer.validate(graph, RuntimeContext())
    codes = {issue.code for issue in report.issues}

    assert report.valid is False
    assert "missing_entitlement" in codes
    assert "missing_renderer" in codes
    assert "missing_capability" in codes


def test_unknown_or_non_executable_primitive_cannot_be_counted_as_execution():
    registry = _registry()
    registry.register(
        PrimitiveSpec(
            id="music.future_fx",
            name="Future FX",
            domains=frozenset({GraphDomain.MUSIC}),
            execution_kind="transform",
            implementation_state="contract_ready",
        )
    )
    composer = AuraEffectGraphComposer(registry)
    graph = EffectGraph(
        id="user.future",
        domain=GraphDomain.MUSIC,
        nodes=(GraphNode("future", "music.future_fx"),),
        edges=(),
        provenance=_provenance(),
    )

    report = composer.validate(graph, RuntimeContext())
    assert any(issue.code == "primitive_unavailable" for issue in report.issues)

    unknown = EffectGraph(
        id="user.unknown",
        domain=GraphDomain.MUSIC,
        nodes=(GraphNode("mystery", "music.not_registered"),),
        edges=(),
        provenance=_provenance(),
    )
    assert any(issue.code == "unknown_primitive" for issue in composer.validate(unknown).issues)


def test_port_types_are_enforced_across_domains():
    registry = _registry()
    registry.register(
        PrimitiveSpec(
            id="music.video_source_for_test",
            name="Mismatched source",
            domains=frozenset({GraphDomain.MUSIC}),
            execution_kind="adapter",
            outputs={"frame": PortSpec("video.frame")},
        )
    )
    graph = EffectGraph(
        id="user.type_mismatch",
        domain=GraphDomain.MUSIC,
        nodes=(
            GraphNode("frame", "music.video_source_for_test"),
            GraphNode("gain", "music.gain"),
        ),
        edges=(GraphEdge("frame", "frame", "gain", "audio"),),
        provenance=_provenance(),
    )

    report = AuraEffectGraphComposer(registry).validate(graph, _music_context())
    codes = [issue.code for issue in report.issues]
    assert "port_type_mismatch" in codes
    assert "required_input_missing" in codes


def test_parameter_bounds_and_unknown_parameters_fail_closed():
    composer = AuraEffectGraphComposer(_registry())
    too_loud = composer.validate(_music_graph(gain=100.0), _music_context())
    assert any(issue.code == "invalid_parameter" for issue in too_loud.issues)

    graph = _music_graph()
    nodes = (graph.nodes[0], GraphNode("gain", "music.gain", {"made_up": 1}))
    unknown = composer.validate(
        EffectGraph(
            id=graph.id,
            domain=graph.domain,
            nodes=nodes,
            edges=graph.edges,
            provenance=graph.provenance,
        ),
        _music_context(),
    )
    assert any(issue.code == "unknown_parameter" for issue in unknown.issues)


def test_cycles_are_rejected_in_favour_of_explicit_bounded_feedback_primitives():
    graph = EffectGraph(
        id="user.feedback",
        domain=GraphDomain.MUSIC,
        nodes=(
            GraphNode("a", "shared.signal"),
            GraphNode("b", "shared.signal"),
        ),
        edges=(
            GraphEdge("a", "signal", "b", "signal"),
            GraphEdge("b", "signal", "a", "signal"),
        ),
        provenance=_provenance(),
    )

    report = AuraEffectGraphComposer(_registry()).validate(graph)
    assert any(issue.code == "cycle_detected" for issue in report.issues)


def test_resource_budgets_apply_to_composed_graph_not_client_claims():
    limits = GraphLimits(
        max_nodes=4,
        max_edges=4,
        max_depth=4,
        max_cpu_units=2,
        max_memory_mb=1024,
        max_provider_cost_units=10,
        max_estimated_ms=1000,
    )
    report = AuraEffectGraphComposer(_registry()).validate(
        _music_graph(),
        _music_context(limits=limits),
    )

    assert report.valid is False
    assert any(issue.code == "resource_limit" and "cpu_units" in issue.message for issue in report.issues)


def test_registry_rejects_generic_shell_process_or_device_execution():
    for execution_kind in ("shell", "process", "device"):
        with pytest.raises(ValueError, match="Arbitrary shell/process/device execution"):
            PrimitiveSpec(
                id=f"shared.{execution_kind}",
                name="Unsafe",
                domains=frozenset({GraphDomain.SHARED}),
                execution_kind=execution_kind,
            )


def test_same_shared_contract_can_register_music_video_image_game_live_voice_social_primitives():
    registry = _registry()
    for domain in (
        GraphDomain.MUSIC,
        GraphDomain.VIDEO,
        GraphDomain.IMAGE,
        GraphDomain.GAME,
        GraphDomain.LIVE,
        GraphDomain.VOICE,
        GraphDomain.SOCIAL,
    ):
        registry.register(
            PrimitiveSpec(
                id=f"{domain.value}.foundation_test",
                name=f"{domain.value.title()} foundation",
                domains=frozenset({domain}),
                execution_kind="system",
            )
        )
        assert registry.get(f"{domain.value}.foundation_test") is not None
