from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .creative_catalogue import get_catalogue_item
from .effects import compile_ffmpeg_chain
from .session import Effect

MAX_EFFECT_NODES = 32
MAX_CANONICAL_GRAPH_BYTES = 64 * 1024
MAX_FFMPEG_FILTER_CHAIN_CHARS = 16 * 1024


@dataclass(frozen=True, slots=True)
class EffectNodeSpec:
    id: str
    catalogue_item_id: str
    parameters: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    mix: float = 1.0

    def public(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EffectSystemSpec:
    id: str
    name: str
    nodes: tuple[EffectNodeSpec, ...]
    version: int = 1
    description: str = ""

    def public(self) -> dict:
        row = asdict(self)
        row["nodes"] = [node.public() for node in self.nodes]
        return row


@dataclass(frozen=True, slots=True)
class CompiledEffectSystem:
    spec: EffectSystemSpec
    effects: tuple[Effect, ...]
    ffmpeg_filter_chain: str
    fingerprint: str
    canonical_graph_bytes: int
    filter_chain_chars: int

    def public(self) -> dict:
        return {
            "system": self.spec.public(),
            "effects": [effect.model_dump(mode="json") for effect in self.effects],
            "ffmpeg_filter_chain": self.ffmpeg_filter_chain,
            "fingerprint": self.fingerprint,
            "runtime": "ffmpeg_audio",
            "backend_executable": True,
            "source_media_mutated": False,
            "node_count": len(self.effects),
            "resource_budget": {
                "node_count": len(self.effects),
                "max_node_count": MAX_EFFECT_NODES,
                "canonical_graph_bytes": self.canonical_graph_bytes,
                "max_canonical_graph_bytes": MAX_CANONICAL_GRAPH_BYTES,
                "filter_chain_chars": self.filter_chain_chars,
                "max_filter_chain_chars": MAX_FFMPEG_FILTER_CHAIN_CHARS,
            },
        }


def _normalize_id(value: str, *, label: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    if len(normalized) > 120:
        raise ValueError(f"{label} is too long")
    if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in normalized):
        raise ValueError(f"{label} contains unsupported characters")
    return normalized


def _normalize_mix(value: float) -> float:
    mix = float(value)
    if mix < 0.0 or mix > 1.0:
        raise ValueError("Effect node mix must be between 0 and 1")
    return mix


def make_effect_system(
    system_id: str,
    name: str,
    nodes: Iterable[EffectNodeSpec],
    *,
    description: str = "",
    version: int = 1,
) -> EffectSystemSpec:
    normalized_nodes = tuple(nodes)
    if not normalized_nodes:
        raise ValueError("Effect system requires at least one node")
    if len(normalized_nodes) > MAX_EFFECT_NODES:
        raise ValueError(f"Effect system exceeds maximum node count of {MAX_EFFECT_NODES}")

    ids: set[str] = set()
    cleaned: list[EffectNodeSpec] = []
    for node in normalized_nodes:
        node_id = _normalize_id(node.id, label="Effect node id")
        if node_id in ids:
            raise ValueError(f"Duplicate effect node id: {node_id}")
        ids.add(node_id)
        item_id = _normalize_id(node.catalogue_item_id, label="Catalogue item id")
        cleaned.append(
            EffectNodeSpec(
                id=node_id,
                catalogue_item_id=item_id,
                parameters=dict(node.parameters or {}),
                enabled=bool(node.enabled),
                mix=_normalize_mix(node.mix),
            )
        )

    system_name = (name or "").strip()
    if not system_name:
        raise ValueError("Effect system name is required")
    if len(system_name) > 160:
        raise ValueError("Effect system name is too long")
    if int(version) < 1:
        raise ValueError("Effect system version must be at least 1")

    return EffectSystemSpec(
        id=_normalize_id(system_id, label="Effect system id"),
        name=system_name,
        description=(description or "").strip()[:500],
        version=int(version),
        nodes=tuple(cleaned),
    )


def compile_effect_system(spec: EffectSystemSpec) -> CompiledEffectSystem:
    if not spec.nodes:
        raise ValueError("Effect system requires at least one node")
    if len(spec.nodes) > MAX_EFFECT_NODES:
        raise ValueError(f"Effect system exceeds maximum node count of {MAX_EFFECT_NODES}")

    effects: list[Effect] = []
    seen: set[str] = set()
    canonical_nodes: list[dict[str, Any]] = []

    for node in spec.nodes:
        node_id = _normalize_id(node.id, label="Effect node id")
        if node_id in seen:
            raise ValueError(f"Duplicate effect node id: {node_id}")
        seen.add(node_id)

        try:
            item = get_catalogue_item(node.catalogue_item_id)
        except KeyError as exc:
            raise ValueError(f"Unknown executable catalogue item: {node.catalogue_item_id}") from exc

        if item.runtime != "ffmpeg_audio":
            raise ValueError(f"Catalogue item is not supported by the audio system runtime: {item.id}")
        if item.status not in {
            "BACKEND_FUNCTIONAL",
            "UI_FUNCTIONAL",
            "WORKFLOW_FUNCTIONAL",
            "INTEGRATED",
            "TESTED",
            "RELEASE_CANDIDATE",
            "PRODUCTION_VERIFIED",
        }:
            raise ValueError(f"Catalogue item is not executable: {item.id}")

        effect = item.build_effect(
            dict(node.parameters or {}),
            enabled=bool(node.enabled),
            mix=_normalize_mix(node.mix),
        )
        effects.append(effect)
        canonical_nodes.append(
            {
                "id": node_id,
                "catalogue_item_id": item.id,
                "enabled": effect.enabled,
                "mix": effect.mix,
                "parameters": effect.parameters,
            }
        )

    chain = compile_ffmpeg_chain(effects)
    filter_chain_chars = len(chain)
    if filter_chain_chars > MAX_FFMPEG_FILTER_CHAIN_CHARS:
        raise ValueError(
            f"Effect system exceeds maximum compiled filter-chain size of {MAX_FFMPEG_FILTER_CHAIN_CHARS} characters"
        )

    canonical = {
        "id": spec.id,
        "name": spec.name,
        "description": spec.description,
        "version": spec.version,
        "nodes": canonical_nodes,
    }
    canonical_blob = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    canonical_graph_bytes = len(canonical_blob)
    if canonical_graph_bytes > MAX_CANONICAL_GRAPH_BYTES:
        raise ValueError(
            f"Effect system exceeds maximum canonical graph size of {MAX_CANONICAL_GRAPH_BYTES} bytes"
        )
    fingerprint = hashlib.sha256(canonical_blob).hexdigest()

    return CompiledEffectSystem(
        spec=spec,
        effects=tuple(effects),
        ffmpeg_filter_chain=chain,
        fingerprint=fingerprint,
        canonical_graph_bytes=canonical_graph_bytes,
        filter_chain_chars=filter_chain_chars,
    )


def compile_effect_system_payload(payload: dict[str, Any]) -> dict:
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list):
        raise ValueError("Effect system nodes must be a list")
    nodes = []
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            raise ValueError("Each effect node must be an object")
        nodes.append(
            EffectNodeSpec(
                id=str(raw.get("id") or ""),
                catalogue_item_id=str(raw.get("catalogue_item_id") or ""),
                parameters=dict(raw.get("parameters") or {}),
                enabled=bool(raw.get("enabled", True)),
                mix=float(raw.get("mix", 1.0)),
            )
        )
    spec = make_effect_system(
        str(payload.get("id") or ""),
        str(payload.get("name") or ""),
        nodes,
        description=str(payload.get("description") or ""),
        version=int(payload.get("version") or 1),
    )
    return compile_effect_system(spec).public()


__all__ = [
    "CompiledEffectSystem",
    "EffectNodeSpec",
    "EffectSystemSpec",
    "MAX_CANONICAL_GRAPH_BYTES",
    "MAX_EFFECT_NODES",
    "MAX_FFMPEG_FILTER_CHAIN_CHARS",
    "compile_effect_system",
    "compile_effect_system_payload",
    "make_effect_system",
]
