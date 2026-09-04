from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Iterable, Mapping


_NAMESPACE_ID = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$")
_NODE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,95}$")
_ALLOWED_EXECUTION_KINDS = frozenset(
    {"transform", "analyzer", "generator", "renderer", "adapter", "control", "system"}
)
_FORBIDDEN_EXECUTION_KINDS = frozenset({"shell", "process", "device", "exec", "command"})


class GraphDomain(StrEnum):
    MUSIC = "music"
    VIDEO = "video"
    IMAGE = "image"
    GAME = "game"
    LIVE = "live"
    VOICE = "voice"
    SOCIAL = "social"
    SHARED = "shared"


@dataclass(frozen=True)
class PortSpec:
    data_type: str
    required: bool = True
    multiple: bool = False

    def __post_init__(self) -> None:
        if not _NAMESPACE_ID.fullmatch(self.data_type):
            raise ValueError(f"Port data type must be namespaced: {self.data_type}")


@dataclass(frozen=True)
class ParameterSpec:
    kind: str
    default: Any = None
    required: bool = False
    minimum: float | int | None = None
    maximum: float | int | None = None
    choices: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in {"number", "integer", "boolean", "string", "enum"}:
            raise ValueError(f"Unsupported parameter kind: {self.kind}")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Parameter minimum cannot exceed maximum")
        if self.kind == "enum" and not self.choices:
            raise ValueError("Enum parameters require choices")
        if self.default is not None:
            error = _parameter_error(self, self.default)
            if error:
                raise ValueError(f"Invalid parameter default: {error}")


@dataclass(frozen=True)
class ResourceCost:
    cpu_units: int = 0
    memory_mb: int = 0
    provider_cost_units: int = 0
    estimated_ms: int = 0

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if int(value) < 0:
                raise ValueError(f"Resource cost {name} cannot be negative")

    def plus(self, other: "ResourceCost") -> "ResourceCost":
        return ResourceCost(
            cpu_units=self.cpu_units + other.cpu_units,
            memory_mb=self.memory_mb + other.memory_mb,
            provider_cost_units=self.provider_cost_units + other.provider_cost_units,
            estimated_ms=self.estimated_ms + other.estimated_ms,
        )


@dataclass(frozen=True)
class PrimitiveSpec:
    id: str
    name: str
    domains: frozenset[GraphDomain]
    execution_kind: str
    inputs: Mapping[str, PortSpec] = field(default_factory=dict)
    outputs: Mapping[str, PortSpec] = field(default_factory=dict)
    parameters: Mapping[str, ParameterSpec] = field(default_factory=dict)
    required_entitlements: frozenset[str] = frozenset()
    required_renderers: frozenset[str] = frozenset()
    required_providers: frozenset[str] = frozenset()
    required_capabilities: frozenset[str] = frozenset()
    resource_cost: ResourceCost = ResourceCost()
    implementation_state: str = "executable"
    effect_sku_id: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        _require_namespace(self.id, "Primitive id")
        if not self.name.strip():
            raise ValueError("Primitive name is required")
        if not self.domains:
            raise ValueError("Primitive must declare at least one domain")
        if self.execution_kind in _FORBIDDEN_EXECUTION_KINDS:
            raise ValueError("Arbitrary shell/process/device execution is not a valid graph primitive")
        if self.execution_kind not in _ALLOWED_EXECUTION_KINDS:
            raise ValueError(f"Unsupported primitive execution kind: {self.execution_kind}")
        if self.implementation_state not in {"executable", "contract_ready", "planned_original", "disabled"}:
            raise ValueError(f"Unsupported implementation state: {self.implementation_state}")
        if self.version < 1:
            raise ValueError("Primitive version must be positive")
        if self.effect_sku_id is not None:
            _require_namespace(self.effect_sku_id, "Effect SKU id")
        for label, ports in (("input", self.inputs), ("output", self.outputs)):
            for port_name in ports:
                if not _NODE_ID.fullmatch(port_name):
                    raise ValueError(f"Invalid {label} port name: {port_name}")
        for parameter_name in self.parameters:
            if not _NODE_ID.fullmatch(parameter_name):
                raise ValueError(f"Invalid parameter name: {parameter_name}")


@dataclass(frozen=True)
class GraphNode:
    id: str
    primitive_id: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _NODE_ID.fullmatch(self.id):
            raise ValueError(f"Invalid graph node id: {self.id}")
        _require_namespace(self.primitive_id, "Primitive id")


@dataclass(frozen=True)
class GraphEdge:
    source_node: str
    source_port: str
    target_node: str
    target_port: str


@dataclass(frozen=True)
class GraphProvenance:
    author_id: str
    source: str
    licence: str
    rights_state: str
    source_assets: tuple[str, ...] = ()
    consent_requirements: tuple[str, ...] = ()
    source_prompt: str | None = None

    def __post_init__(self) -> None:
        if not self.author_id.strip():
            raise ValueError("Graph provenance requires an author")
        if self.source not in {"user", "aura", "esp", "marketplace", "import"}:
            raise ValueError("Unsupported graph provenance source")
        if not self.licence.strip():
            raise ValueError("Graph provenance requires licence state")
        if self.rights_state not in {"cleared", "restricted", "unknown", "not_applicable"}:
            raise ValueError("Unsupported rights state")


@dataclass(frozen=True)
class EffectGraph:
    id: str
    domain: GraphDomain
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    provenance: GraphProvenance
    version: int = 1
    title: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_namespace(self.id, "Graph id")
        if self.version < 1:
            raise ValueError("Graph version must be positive")


@dataclass(frozen=True)
class GraphLimits:
    max_nodes: int = 64
    max_edges: int = 128
    max_depth: int = 24
    max_cpu_units: int = 256
    max_memory_mb: int = 8192
    max_provider_cost_units: int = 1000
    max_estimated_ms: int = 300_000

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if int(value) <= 0:
                raise ValueError(f"Graph limit {name} must be positive")


@dataclass(frozen=True)
class RuntimeContext:
    entitlements: frozenset[str] = frozenset()
    renderers: frozenset[str] = frozenset()
    providers: frozenset[str] = frozenset()
    capabilities: frozenset[str] = frozenset()
    limits: GraphLimits = GraphLimits()
    executable_states: frozenset[str] = frozenset({"executable"})


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    node_id: str | None = None
    edge_index: int | None = None


@dataclass(frozen=True)
class GraphRequirements:
    entitlements: tuple[str, ...]
    renderers: tuple[str, ...]
    providers: tuple[str, ...]
    capabilities: tuple[str, ...]
    effect_skus: tuple[str, ...]


@dataclass(frozen=True)
class GraphValidationReport:
    valid: bool
    issues: tuple[ValidationIssue, ...]
    requirements: GraphRequirements
    resource_cost: ResourceCost
    depth: int
    graph_digest: str

    def require_valid(self) -> None:
        if not self.valid:
            joined = "; ".join(f"{issue.code}: {issue.message}" for issue in self.issues)
            raise ValueError(joined or "Graph validation failed")


class PrimitiveRegistry:
    """Allowlisted, namespaced primitive registry shared by every creative domain."""

    def __init__(self, primitives: Iterable[PrimitiveSpec] = ()) -> None:
        self._items: dict[str, PrimitiveSpec] = {}
        for primitive in primitives:
            self.register(primitive)

    def register(self, primitive: PrimitiveSpec) -> PrimitiveSpec:
        existing = self._items.get(primitive.id)
        if existing is not None:
            if existing == primitive:
                return existing
            raise ValueError(f"Primitive id is already registered: {primitive.id}")
        self._items[primitive.id] = primitive
        return primitive

    def get(self, primitive_id: str) -> PrimitiveSpec | None:
        return self._items.get(primitive_id)

    def require(self, primitive_id: str) -> PrimitiveSpec:
        primitive = self.get(primitive_id)
        if primitive is None:
            raise KeyError(f"Unknown graph primitive: {primitive_id}")
        return primitive

    def list(self, domain: GraphDomain | None = None) -> tuple[PrimitiveSpec, ...]:
        values = self._items.values()
        if domain is not None:
            values = (item for item in values if domain in item.domains or GraphDomain.SHARED in item.domains)
        return tuple(sorted(values, key=lambda item: item.id))


class AuraEffectGraphComposer:
    """Shared bounded composer/validator used by Aura and every studio surface.

    Domain chats provide namespaced executable primitives. This core never executes arbitrary
    code and never invents provider availability, entitlement or commercial state.
    """

    def __init__(self, registry: PrimitiveRegistry) -> None:
        self.registry = registry

    def materialize_defaults(self, graph: EffectGraph) -> EffectGraph:
        nodes: list[GraphNode] = []
        for node in graph.nodes:
            primitive = self.registry.get(node.primitive_id)
            if primitive is None:
                nodes.append(node)
                continue
            parameters = dict(node.parameters)
            for name, spec in primitive.parameters.items():
                if name not in parameters and spec.default is not None:
                    parameters[name] = spec.default
            nodes.append(replace(node, parameters=parameters))
        return replace(graph, nodes=tuple(nodes))

    def validate(self, graph: EffectGraph, context: RuntimeContext | None = None) -> GraphValidationReport:
        context = context or RuntimeContext()
        graph = self.materialize_defaults(graph)
        issues: list[ValidationIssue] = []
        limits = context.limits

        if len(graph.nodes) > limits.max_nodes:
            issues.append(ValidationIssue("node_limit", f"Graph has {len(graph.nodes)} nodes; limit is {limits.max_nodes}"))
        if len(graph.edges) > limits.max_edges:
            issues.append(ValidationIssue("edge_limit", f"Graph has {len(graph.edges)} edges; limit is {limits.max_edges}"))

        nodes: dict[str, GraphNode] = {}
        primitives: dict[str, PrimitiveSpec] = {}
        total_cost = ResourceCost()
        entitlements: set[str] = set()
        renderers: set[str] = set()
        providers: set[str] = set()
        capabilities: set[str] = set()
        effect_skus: set[str] = set()

        for node in graph.nodes:
            if node.id in nodes:
                issues.append(ValidationIssue("duplicate_node", f"Duplicate node id: {node.id}", node_id=node.id))
                continue
            nodes[node.id] = node
            primitive = self.registry.get(node.primitive_id)
            if primitive is None:
                issues.append(ValidationIssue("unknown_primitive", f"Primitive is not allowlisted: {node.primitive_id}", node_id=node.id))
                continue
            primitives[node.id] = primitive
            if graph.domain not in primitive.domains and GraphDomain.SHARED not in primitive.domains:
                issues.append(ValidationIssue("domain_mismatch", f"{primitive.id} is not compatible with {graph.domain.value}", node_id=node.id))
            if primitive.implementation_state not in context.executable_states:
                issues.append(ValidationIssue("primitive_unavailable", f"{primitive.id} is {primitive.implementation_state}, not executable", node_id=node.id))

            entitlements.update(primitive.required_entitlements)
            renderers.update(primitive.required_renderers)
            providers.update(primitive.required_providers)
            capabilities.update(primitive.required_capabilities)
            if primitive.effect_sku_id:
                effect_skus.add(primitive.effect_sku_id)
            total_cost = total_cost.plus(primitive.resource_cost)
            self._validate_parameters(node, primitive, issues)

        self._dependency_issues("entitlement", entitlements, context.entitlements, issues)
        self._dependency_issues("renderer", renderers, context.renderers, issues)
        self._dependency_issues("provider", providers, context.providers, issues)
        self._dependency_issues("capability", capabilities, context.capabilities, issues)

        adjacency: dict[str, list[str]] = {node_id: [] for node_id in nodes}
        indegree: dict[str, int] = {node_id: 0 for node_id in nodes}
        incoming: dict[tuple[str, str], int] = {}

        for index, edge in enumerate(graph.edges):
            source = nodes.get(edge.source_node)
            target = nodes.get(edge.target_node)
            if source is None or target is None:
                missing = edge.source_node if source is None else edge.target_node
                issues.append(ValidationIssue("missing_edge_node", f"Edge references missing node: {missing}", edge_index=index))
                continue
            source_primitive = primitives.get(source.id)
            target_primitive = primitives.get(target.id)
            if source_primitive is None or target_primitive is None:
                continue
            source_port = source_primitive.outputs.get(edge.source_port)
            target_port = target_primitive.inputs.get(edge.target_port)
            if source_port is None:
                issues.append(ValidationIssue("missing_source_port", f"Unknown output port: {edge.source_port}", node_id=source.id, edge_index=index))
                continue
            if target_port is None:
                issues.append(ValidationIssue("missing_target_port", f"Unknown input port: {edge.target_port}", node_id=target.id, edge_index=index))
                continue
            if source_port.data_type != target_port.data_type:
                issues.append(
                    ValidationIssue(
                        "port_type_mismatch",
                        f"Cannot connect {source_port.data_type} to {target_port.data_type}",
                        node_id=target.id,
                        edge_index=index,
                    )
                )
                continue
            key = (target.id, edge.target_port)
            incoming[key] = incoming.get(key, 0) + 1
            if incoming[key] > 1 and not target_port.multiple:
                issues.append(ValidationIssue("input_cardinality", f"Input {edge.target_port} accepts one connection", node_id=target.id, edge_index=index))
            adjacency[source.id].append(target.id)
            indegree[target.id] += 1

        for node_id, primitive in primitives.items():
            for port_name, port in primitive.inputs.items():
                if port.required and incoming.get((node_id, port_name), 0) == 0:
                    issues.append(ValidationIssue("required_input_missing", f"Required input is not connected: {port_name}", node_id=node_id))

        depth, cyclic = _graph_depth(adjacency, indegree)
        if cyclic:
            issues.append(ValidationIssue("cycle_detected", "Effect/system graphs must be acyclic; use an explicit bounded feedback primitive instead"))
        if depth > limits.max_depth:
            issues.append(ValidationIssue("depth_limit", f"Graph depth {depth} exceeds limit {limits.max_depth}"))

        self._resource_limit_issues(total_cost, limits, issues)
        requirements = GraphRequirements(
            entitlements=tuple(sorted(entitlements)),
            renderers=tuple(sorted(renderers)),
            providers=tuple(sorted(providers)),
            capabilities=tuple(sorted(capabilities)),
            effect_skus=tuple(sorted(effect_skus)),
        )
        return GraphValidationReport(
            valid=not issues,
            issues=tuple(issues),
            requirements=requirements,
            resource_cost=total_cost,
            depth=depth,
            graph_digest=graph_digest(graph),
        )

    @staticmethod
    def _dependency_issues(kind: str, required: set[str], available: frozenset[str], issues: list[ValidationIssue]) -> None:
        for dependency in sorted(required - set(available)):
            issues.append(ValidationIssue(f"missing_{kind}", f"Required {kind} is unavailable: {dependency}"))

    @staticmethod
    def _validate_parameters(node: GraphNode, primitive: PrimitiveSpec, issues: list[ValidationIssue]) -> None:
        for name in node.parameters:
            if name not in primitive.parameters:
                issues.append(ValidationIssue("unknown_parameter", f"Unknown parameter: {name}", node_id=node.id))
        for name, spec in primitive.parameters.items():
            if name not in node.parameters:
                if spec.required and spec.default is None:
                    issues.append(ValidationIssue("required_parameter_missing", f"Required parameter is missing: {name}", node_id=node.id))
                continue
            error = _parameter_error(spec, node.parameters[name])
            if error:
                issues.append(ValidationIssue("invalid_parameter", f"{name}: {error}", node_id=node.id))

    @staticmethod
    def _resource_limit_issues(cost: ResourceCost, limits: GraphLimits, issues: list[ValidationIssue]) -> None:
        pairs = (
            ("cpu_units", cost.cpu_units, limits.max_cpu_units),
            ("memory_mb", cost.memory_mb, limits.max_memory_mb),
            ("provider_cost_units", cost.provider_cost_units, limits.max_provider_cost_units),
            ("estimated_ms", cost.estimated_ms, limits.max_estimated_ms),
        )
        for name, actual, maximum in pairs:
            if actual > maximum:
                issues.append(ValidationIssue("resource_limit", f"{name} {actual} exceeds limit {maximum}"))


def graph_canonical_dict(graph: EffectGraph) -> dict[str, Any]:
    return {
        "id": graph.id,
        "domain": graph.domain.value,
        "version": graph.version,
        "title": graph.title,
        "description": graph.description,
        "tags": list(graph.tags),
        "nodes": [
            {
                "id": node.id,
                "primitive_id": node.primitive_id,
                "parameters": _canonical_value(dict(node.parameters)),
            }
            for node in graph.nodes
        ],
        "edges": [
            {
                "source_node": edge.source_node,
                "source_port": edge.source_port,
                "target_node": edge.target_node,
                "target_port": edge.target_port,
            }
            for edge in graph.edges
        ],
        "provenance": {
            "author_id": graph.provenance.author_id,
            "source": graph.provenance.source,
            "licence": graph.provenance.licence,
            "rights_state": graph.provenance.rights_state,
            "source_assets": list(graph.provenance.source_assets),
            "consent_requirements": list(graph.provenance.consent_requirements),
            "source_prompt": graph.provenance.source_prompt,
        },
    }


def graph_canonical_json(graph: EffectGraph) -> str:
    return json.dumps(graph_canonical_dict(graph), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def graph_digest(graph: EffectGraph) -> str:
    return hashlib.sha256(graph_canonical_json(graph).encode("utf-8")).hexdigest()


def _require_namespace(value: str, label: str) -> None:
    if not _NAMESPACE_ID.fullmatch(value):
        raise ValueError(f"{label} must be a stable namespaced id: {value}")


def _parameter_error(spec: ParameterSpec, value: Any) -> str | None:
    if spec.kind == "boolean":
        if type(value) is not bool:
            return "must be a boolean"
    elif spec.kind == "integer":
        if type(value) is not int:
            return "must be an integer"
    elif spec.kind == "number":
        if type(value) not in {int, float}:
            return "must be a number"
    elif spec.kind == "string":
        if not isinstance(value, str):
            return "must be a string"
    elif spec.kind == "enum" and value not in spec.choices:
        return f"must be one of {list(spec.choices)}"

    if spec.kind in {"integer", "number"} and type(value) in {int, float}:
        if spec.minimum is not None and value < spec.minimum:
            return f"must be >= {spec.minimum}"
        if spec.maximum is not None and value > spec.maximum:
            return f"must be <= {spec.maximum}"
    return None


def _graph_depth(adjacency: Mapping[str, list[str]], indegree: Mapping[str, int]) -> tuple[int, bool]:
    working = dict(indegree)
    depth = {node_id: 1 for node_id in adjacency}
    queue = sorted(node_id for node_id, degree in working.items() if degree == 0)
    visited = 0
    while queue:
        node_id = queue.pop(0)
        visited += 1
        for target in sorted(adjacency.get(node_id, [])):
            depth[target] = max(depth.get(target, 1), depth[node_id] + 1)
            working[target] -= 1
            if working[target] == 0:
                queue.append(target)
                queue.sort()
    cyclic = visited != len(adjacency)
    return (max(depth.values(), default=0), cyclic)


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_canonical_value(item) for item in value)
    if isinstance(value, StrEnum):
        return value.value
    return value


__all__ = [
    "AuraEffectGraphComposer",
    "EffectGraph",
    "GraphDomain",
    "GraphEdge",
    "GraphLimits",
    "GraphNode",
    "GraphProvenance",
    "GraphRequirements",
    "GraphValidationReport",
    "ParameterSpec",
    "PortSpec",
    "PrimitiveRegistry",
    "PrimitiveSpec",
    "ResourceCost",
    "RuntimeContext",
    "ValidationIssue",
    "graph_canonical_dict",
    "graph_canonical_json",
    "graph_digest",
]
