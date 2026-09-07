from __future__ import annotations

import pytest
from pydantic import ValidationError

from aura_music_studio.game_forge import (
    GraphExecutionError,
    GraphOutputRef,
    GraphValidationError,
    VisualEdge,
    VisualGraph,
    VisualLogicRuntime,
    VisualNode,
)


def number_node(node_id: str, value: float) -> VisualNode:
    return VisualNode(id=node_id, operation="core.number", inputs={"value": value})


def edge(source: str, target: str, target_port: str) -> VisualEdge:
    return VisualEdge(
        source_node=source,
        source_port="value",
        target_node=target,
        target_port=target_port,
    )


def arithmetic_graph() -> VisualGraph:
    return VisualGraph(
        nodes=[
            number_node("left", 2),
            number_node("right", 3),
            VisualNode(id="sum", operation="math.add"),
            number_node("factor", 4),
            VisualNode(id="result", operation="math.multiply"),
        ],
        edges=[
            edge("left", "sum", "a"),
            edge("right", "sum", "b"),
            edge("sum", "result", "a"),
            edge("factor", "result", "b"),
        ],
        outputs={"value": GraphOutputRef(node_id="result", port="value")},
    )


def test_executes_typed_arithmetic_and_captures_trace():
    result = VisualLogicRuntime().execute(arithmetic_graph())

    assert result.outputs == {"value": 20}
    assert result.execution_order == ["factor", "left", "right", "sum", "result"]
    assert [item.node_id for item in result.trace] == result.execution_order
    assert result.trace[-1].inputs == {"a": 5, "b": 4}
    assert result.trace[-1].outputs == {"value": 20}


def test_provenance_hash_is_stable_for_same_graph():
    runtime = VisualLogicRuntime()
    graph = arithmetic_graph()

    first = runtime.provenance_hash(graph)
    second = runtime.provenance_hash(VisualGraph.model_validate(graph.model_dump()))

    assert first == second
    assert len(first) == 64


def test_execution_order_is_deterministic_for_independent_nodes():
    graph = VisualGraph(
        nodes=[number_node("z", 1), number_node("a", 2), number_node("m", 3)]
    )

    result = VisualLogicRuntime().execute(graph)

    assert result.execution_order == ["a", "m", "z"]


@pytest.mark.parametrize(
    "operation",
    [
        "shell.run",
        "http.request",
        "network.fetch",
        "auth.grant",
        "role.assign",
        "coin.credit",
        "gift.send",
        "live.start",
        "provider.invoke",
        "eval.python",
    ],
)
def test_privileged_or_arbitrary_authority_names_are_rejected(operation: str):
    graph = VisualGraph(nodes=[VisualNode(id="unsafe", operation=operation)])

    with pytest.raises(GraphValidationError, match="outside Game Forge authority"):
        VisualLogicRuntime().validate(graph)


def test_unknown_operation_is_rejected_closed():
    graph = VisualGraph(nodes=[VisualNode(id="unknown", operation="game.unregistered")])

    with pytest.raises(GraphValidationError, match="Unknown Visual Logic operation"):
        VisualLogicRuntime().validate(graph)


def test_duplicate_node_id_is_rejected():
    graph = VisualGraph(nodes=[number_node("same", 1), number_node("same", 2)])

    with pytest.raises(GraphValidationError, match="Duplicate node id"):
        VisualLogicRuntime().validate(graph)


def test_missing_required_input_is_rejected():
    graph = VisualGraph(nodes=[VisualNode(id="sum", operation="math.add", inputs={"a": 1})])

    with pytest.raises(GraphValidationError, match="Missing required input: sum.b"):
        VisualLogicRuntime().validate(graph)


def test_literal_input_type_is_strict_and_bool_is_not_number():
    graph = VisualGraph(
        nodes=[VisualNode(id="number", operation="core.number", inputs={"value": True})]
    )

    with pytest.raises(GraphValidationError, match="requires number, received boolean"):
        VisualLogicRuntime().validate(graph)


def test_unsupported_literal_type_is_rejected():
    graph = VisualGraph(
        nodes=[VisualNode(id="text", operation="core.text", inputs={"value": ["unsafe"]})]
    )

    with pytest.raises(GraphValidationError, match="restricted to number, boolean, and text"):
        VisualLogicRuntime().validate(graph)


def test_edge_type_mismatch_is_rejected():
    graph = VisualGraph(
        nodes=[
            VisualNode(id="flag", operation="core.boolean", inputs={"value": True}),
            number_node("right", 1),
            VisualNode(id="sum", operation="math.add"),
        ],
        edges=[edge("flag", "sum", "a"), edge("right", "sum", "b")],
    )

    with pytest.raises(GraphValidationError, match="Type mismatch"):
        VisualLogicRuntime().validate(graph)


def test_unknown_edge_port_is_rejected():
    graph = VisualGraph(
        nodes=[number_node("source", 1), VisualNode(id="sum", operation="math.add")],
        edges=[
            VisualEdge(
                source_node="source",
                source_port="missing",
                target_node="sum",
                target_port="a",
            )
        ],
    )

    with pytest.raises(GraphValidationError, match="Unknown source port"):
        VisualLogicRuntime().validate(graph)


def test_duplicate_inbound_binding_is_rejected():
    graph = VisualGraph(
        nodes=[
            number_node("one", 1),
            number_node("two", 2),
            number_node("other", 3),
            VisualNode(id="sum", operation="math.add"),
        ],
        edges=[
            edge("one", "sum", "a"),
            edge("two", "sum", "a"),
            edge("other", "sum", "b"),
        ],
    )

    with pytest.raises(GraphValidationError, match="Duplicate inbound binding"):
        VisualLogicRuntime().validate(graph)


def test_literal_and_edge_binding_conflict_is_rejected():
    graph = VisualGraph(
        nodes=[
            number_node("source", 1),
            VisualNode(id="sum", operation="math.add", inputs={"a": 2, "b": 3}),
        ],
        edges=[edge("source", "sum", "a")],
    )

    with pytest.raises(GraphValidationError, match="both literal and edge binding"):
        VisualLogicRuntime().validate(graph)


def test_cycles_are_rejected():
    graph = VisualGraph(
        nodes=[
            VisualNode(id="a", operation="core.number"),
            VisualNode(id="b", operation="core.number"),
        ],
        edges=[edge("a", "b", "value"), edge("b", "a", "value")],
    )

    with pytest.raises(GraphValidationError, match="contains a cycle"):
        VisualLogicRuntime().validate(graph)


def test_graph_bounds_are_enforced():
    graph = VisualGraph(nodes=[number_node("one", 1), number_node("two", 2)])

    with pytest.raises(GraphValidationError, match="max_nodes=1"):
        VisualLogicRuntime(max_nodes=1).validate(graph)


def test_execution_step_limit_is_enforced():
    graph = VisualGraph(nodes=[number_node("one", 1), number_node("two", 2)])

    with pytest.raises(GraphExecutionError, match="max_steps=1"):
        VisualLogicRuntime(max_steps=1).execute(graph)


def test_runtime_errors_fail_closed_with_node_context():
    graph = VisualGraph(
        nodes=[
            number_node("numerator", 10),
            number_node("zero", 0),
            VisualNode(id="divide", operation="math.divide"),
        ],
        edges=[edge("numerator", "divide", "a"), edge("zero", "divide", "b")],
    )

    with pytest.raises(GraphExecutionError, match=r"divide .*ZeroDivisionError"):
        VisualLogicRuntime().execute(graph)


def test_graph_model_rejects_unsupported_schema_version():
    with pytest.raises(ValidationError, match="Unsupported Visual Logic schema_version"):
        VisualGraph(schema_version=2)
