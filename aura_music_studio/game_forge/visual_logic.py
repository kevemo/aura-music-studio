from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Mapping

from pydantic import BaseModel, Field, model_validator


class VisualLogicError(ValueError):
    """Base error for closed Game Forge Visual Logic failures."""


class GraphValidationError(VisualLogicError):
    """Raised when a graph is structurally invalid or requests unavailable authority."""


class GraphExecutionError(VisualLogicError):
    """Raised when a validated graph fails during bounded execution."""


class ValueType(str, Enum):
    NUMBER = "number"
    BOOLEAN = "boolean"
    TEXT = "text"


class PortSpec(BaseModel):
    value_type: ValueType
    required: bool = True


Handler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class OperationSpec:
    name: str
    inputs: Mapping[str, PortSpec]
    outputs: Mapping[str, PortSpec]
    handler: Handler


class VisualNode(BaseModel):
    id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9_.-]+$")
    operation: str = Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9_.-]+$")
    inputs: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bound_literal_inputs(self):
        if len(self.inputs) > 32:
            raise ValueError("A Visual Logic node may define at most 32 literal inputs")
        return self


class VisualEdge(BaseModel):
    source_node: str = Field(min_length=1, max_length=96)
    source_port: str = Field(min_length=1, max_length=96)
    target_node: str = Field(min_length=1, max_length=96)
    target_port: str = Field(min_length=1, max_length=96)


class GraphOutputRef(BaseModel):
    node_id: str = Field(min_length=1, max_length=96)
    port: str = Field(min_length=1, max_length=96)


class VisualGraph(BaseModel):
    schema_version: int = 1
    nodes: list[VisualNode] = Field(default_factory=list)
    edges: list[VisualEdge] = Field(default_factory=list)
    outputs: dict[str, GraphOutputRef] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_shape(self):
        if self.schema_version != 1:
            raise ValueError("Unsupported Visual Logic schema_version")
        if len(self.outputs) > 64:
            raise ValueError("A Visual Logic graph may expose at most 64 outputs")
        return self


class TraceEvent(BaseModel):
    step: int
    node_id: str
    operation: str
    inputs: dict[str, Any]
    outputs: dict[str, Any]


class ExecutionResult(BaseModel):
    outputs: dict[str, Any]
    trace: list[TraceEvent]
    provenance_hash: str
    execution_order: list[str]


@dataclass(frozen=True)
class _InboundEdge:
    source_node: str
    source_port: str


@dataclass(frozen=True)
class _ExecutionPlan:
    order: tuple[str, ...]
    inbound: Mapping[tuple[str, str], _InboundEdge]
    nodes: Mapping[str, VisualNode]


def _value_type(value: Any) -> ValueType:
    if isinstance(value, bool):
        return ValueType.BOOLEAN
    if isinstance(value, (int, float)):
        return ValueType.NUMBER
    if isinstance(value, str):
        return ValueType.TEXT
    raise GraphValidationError(
        "Visual Logic values are restricted to number, boolean, and text; "
        f"got {type(value).__name__}"
    )


def _require_type(value: Any, expected: ValueType, label: str) -> None:
    actual = _value_type(value)
    if actual != expected:
        raise GraphValidationError(
            f"{label} requires {expected.value}, received {actual.value}"
        )


class OperationRegistry:
    """Immutable allowlist of repository-backed operations."""

    _RESERVED_PREFIXES = (
        "auth.",
        "coin.",
        "eval.",
        "exec.",
        "filesystem.",
        "gift.",
        "http.",
        "live.",
        "network.",
        "process.",
        "provider.",
        "role.",
        "shell.",
        "subprocess.",
    )

    def __init__(self, operations: Mapping[str, OperationSpec]):
        checked: dict[str, OperationSpec] = {}
        for name, spec in operations.items():
            if name != spec.name:
                raise ValueError(f"Operation registry key mismatch: {name!r} != {spec.name!r}")
            if self.is_reserved(name):
                raise ValueError(f"Reserved operation namespace cannot be registered: {name}")
            if name in checked:
                raise ValueError(f"Duplicate operation: {name}")
            checked[name] = spec
        self._operations = MappingProxyType(checked)

    @classmethod
    def is_reserved(cls, name: str) -> bool:
        return name.lower().startswith(cls._RESERVED_PREFIXES)

    def get(self, name: str) -> OperationSpec:
        if self.is_reserved(name):
            raise GraphValidationError(
                f"Visual Logic operation is outside Game Forge authority: {name}"
            )
        try:
            return self._operations[name]
        except KeyError as exc:
            raise GraphValidationError(f"Unknown Visual Logic operation: {name}") from exc

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._operations))


def _ports(**ports: ValueType) -> Mapping[str, PortSpec]:
    return MappingProxyType(
        {name: PortSpec(value_type=value_type) for name, value_type in ports.items()}
    )


def _single(value: Any) -> dict[str, Any]:
    return {"value": value}


def _operation(
    name: str,
    *,
    inputs: Mapping[str, PortSpec],
    outputs: Mapping[str, PortSpec],
    handler: Handler,
) -> OperationSpec:
    return OperationSpec(name=name, inputs=inputs, outputs=outputs, handler=handler)


def _build_default_registry() -> OperationRegistry:
    number_out = _ports(value=ValueType.NUMBER)
    boolean_out = _ports(value=ValueType.BOOLEAN)
    text_out = _ports(value=ValueType.TEXT)
    operations = [
        _operation(
            "core.number",
            inputs=_ports(value=ValueType.NUMBER),
            outputs=number_out,
            handler=lambda values: _single(values["value"]),
        ),
        _operation(
            "core.boolean",
            inputs=_ports(value=ValueType.BOOLEAN),
            outputs=boolean_out,
            handler=lambda values: _single(values["value"]),
        ),
        _operation(
            "core.text",
            inputs=_ports(value=ValueType.TEXT),
            outputs=text_out,
            handler=lambda values: _single(values["value"]),
        ),
        _operation(
            "math.add",
            inputs=_ports(a=ValueType.NUMBER, b=ValueType.NUMBER),
            outputs=number_out,
            handler=lambda values: _single(values["a"] + values["b"]),
        ),
        _operation(
            "math.subtract",
            inputs=_ports(a=ValueType.NUMBER, b=ValueType.NUMBER),
            outputs=number_out,
            handler=lambda values: _single(values["a"] - values["b"]),
        ),
        _operation(
            "math.multiply",
            inputs=_ports(a=ValueType.NUMBER, b=ValueType.NUMBER),
            outputs=number_out,
            handler=lambda values: _single(values["a"] * values["b"]),
        ),
        _operation(
            "math.divide",
            inputs=_ports(a=ValueType.NUMBER, b=ValueType.NUMBER),
            outputs=number_out,
            handler=lambda values: _single(values["a"] / values["b"]),
        ),
        _operation(
            "logic.and",
            inputs=_ports(a=ValueType.BOOLEAN, b=ValueType.BOOLEAN),
            outputs=boolean_out,
            handler=lambda values: _single(values["a"] and values["b"]),
        ),
        _operation(
            "logic.or",
            inputs=_ports(a=ValueType.BOOLEAN, b=ValueType.BOOLEAN),
            outputs=boolean_out,
            handler=lambda values: _single(values["a"] or values["b"]),
        ),
        _operation(
            "logic.not",
            inputs=_ports(value=ValueType.BOOLEAN),
            outputs=boolean_out,
            handler=lambda values: _single(not values["value"]),
        ),
        _operation(
            "compare.equal_number",
            inputs=_ports(a=ValueType.NUMBER, b=ValueType.NUMBER),
            outputs=boolean_out,
            handler=lambda values: _single(values["a"] == values["b"]),
        ),
        _operation(
            "compare.greater_than",
            inputs=_ports(a=ValueType.NUMBER, b=ValueType.NUMBER),
            outputs=boolean_out,
            handler=lambda values: _single(values["a"] > values["b"]),
        ),
    ]
    return OperationRegistry({item.name: item for item in operations})


DEFAULT_OPERATION_REGISTRY = _build_default_registry()


class VisualLogicRuntime:
    """Deterministic, closed and bounded Game Forge graph executor."""

    def __init__(
        self,
        *,
        registry: OperationRegistry = DEFAULT_OPERATION_REGISTRY,
        max_nodes: int = 256,
        max_edges: int = 1024,
        max_steps: int = 1024,
    ):
        if min(max_nodes, max_edges, max_steps) < 1:
            raise ValueError("Visual Logic runtime limits must all be positive")
        self.registry = registry
        self.max_nodes = max_nodes
        self.max_edges = max_edges
        self.max_steps = max_steps

    def validate(self, graph: VisualGraph) -> _ExecutionPlan:
        if len(graph.nodes) > self.max_nodes:
            raise GraphValidationError(f"Graph exceeds max_nodes={self.max_nodes}")
        if len(graph.edges) > self.max_edges:
            raise GraphValidationError(f"Graph exceeds max_edges={self.max_edges}")

        nodes: dict[str, VisualNode] = {}
        for node in graph.nodes:
            if node.id in nodes:
                raise GraphValidationError(f"Duplicate node id: {node.id}")
            nodes[node.id] = node

        inbound: dict[tuple[str, str], _InboundEdge] = {}
        outgoing: dict[str, set[str]] = defaultdict(set)
        indegree = {node_id: 0 for node_id in nodes}

        for node in graph.nodes:
            operation = self.registry.get(node.operation)
            unknown_literals = set(node.inputs) - set(operation.inputs)
            if unknown_literals:
                unknown = sorted(unknown_literals)[0]
                raise GraphValidationError(
                    f"Node {node.id} supplies unknown input port {unknown!r}"
                )
            for name, value in node.inputs.items():
                _require_type(value, operation.inputs[name].value_type, f"{node.id}.{name}")

        for edge in graph.edges:
            if edge.source_node not in nodes:
                raise GraphValidationError(f"Unknown source node: {edge.source_node}")
            if edge.target_node not in nodes:
                raise GraphValidationError(f"Unknown target node: {edge.target_node}")
            source = self.registry.get(nodes[edge.source_node].operation)
            target = self.registry.get(nodes[edge.target_node].operation)
            if edge.source_port not in source.outputs:
                raise GraphValidationError(
                    f"Unknown source port: {edge.source_node}.{edge.source_port}"
                )
            if edge.target_port not in target.inputs:
                raise GraphValidationError(
                    f"Unknown target port: {edge.target_node}.{edge.target_port}"
                )
            source_type = source.outputs[edge.source_port].value_type
            target_type = target.inputs[edge.target_port].value_type
            if source_type != target_type:
                raise GraphValidationError(
                    "Type mismatch: "
                    f"{edge.source_node}.{edge.source_port} ({source_type.value}) -> "
                    f"{edge.target_node}.{edge.target_port} ({target_type.value})"
                )
            binding = (edge.target_node, edge.target_port)
            if binding in inbound:
                raise GraphValidationError(
                    f"Duplicate inbound binding: {edge.target_node}.{edge.target_port}"
                )
            if edge.target_port in nodes[edge.target_node].inputs:
                raise GraphValidationError(
                    "Input has both literal and edge binding: "
                    f"{edge.target_node}.{edge.target_port}"
                )
            inbound[binding] = _InboundEdge(edge.source_node, edge.source_port)
            if edge.target_node not in outgoing[edge.source_node]:
                outgoing[edge.source_node].add(edge.target_node)
                indegree[edge.target_node] += 1

        for node in graph.nodes:
            operation = self.registry.get(node.operation)
            for name, port in operation.inputs.items():
                if port.required and name not in node.inputs and (node.id, name) not in inbound:
                    raise GraphValidationError(f"Missing required input: {node.id}.{name}")

        for label, output in graph.outputs.items():
            if not label or len(label) > 96:
                raise GraphValidationError("Graph output labels must be 1-96 characters")
            if output.node_id not in nodes:
                raise GraphValidationError(f"Unknown graph output node: {output.node_id}")
            operation = self.registry.get(nodes[output.node_id].operation)
            if output.port not in operation.outputs:
                raise GraphValidationError(
                    f"Unknown graph output port: {output.node_id}.{output.port}"
                )

        ready = sorted(node_id for node_id, degree in indegree.items() if degree == 0)
        order: list[str] = []
        while ready:
            node_id = ready.pop(0)
            order.append(node_id)
            for target_id in sorted(outgoing.get(node_id, ())):
                indegree[target_id] -= 1
                if indegree[target_id] == 0:
                    ready.append(target_id)
                    ready.sort()

        if len(order) != len(nodes):
            raise GraphValidationError("Visual Logic graph contains a cycle")

        return _ExecutionPlan(
            order=tuple(order),
            inbound=MappingProxyType(inbound),
            nodes=MappingProxyType(nodes),
        )

    @staticmethod
    def provenance_hash(graph: VisualGraph) -> str:
        canonical = json.dumps(
            graph.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def execute(self, graph: VisualGraph) -> ExecutionResult:
        plan = self.validate(graph)
        node_outputs: dict[str, dict[str, Any]] = {}
        trace: list[TraceEvent] = []

        for step, node_id in enumerate(plan.order, start=1):
            if step > self.max_steps:
                raise GraphExecutionError(f"Graph exceeds max_steps={self.max_steps}")
            node = plan.nodes[node_id]
            operation = self.registry.get(node.operation)
            values = dict(node.inputs)
            for input_name in operation.inputs:
                source = plan.inbound.get((node_id, input_name))
                if source is not None:
                    values[input_name] = node_outputs[source.source_node][source.source_port]
            try:
                produced = operation.handler(values)
            except VisualLogicError:
                raise
            except Exception as exc:
                raise GraphExecutionError(
                    f"Operation failed at node {node_id} ({node.operation}): "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            if set(produced) != set(operation.outputs):
                raise GraphExecutionError(
                    f"Operation {node.operation} returned an invalid output shape"
                )
            for name, value in produced.items():
                try:
                    _require_type(
                        value,
                        operation.outputs[name].value_type,
                        f"{node.id}.{name}",
                    )
                except GraphValidationError as exc:
                    raise GraphExecutionError(str(exc)) from exc
            node_outputs[node_id] = dict(produced)
            trace.append(
                TraceEvent(
                    step=step,
                    node_id=node_id,
                    operation=node.operation,
                    inputs=dict(values),
                    outputs=dict(produced),
                )
            )

        outputs = {
            label: node_outputs[ref.node_id][ref.port]
            for label, ref in sorted(graph.outputs.items())
        }
        return ExecutionResult(
            outputs=outputs,
            trace=trace,
            provenance_hash=self.provenance_hash(graph),
            execution_order=list(plan.order),
        )
