from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .aura_effect_graph import (
    AuraEffectGraphComposer,
    EffectGraph,
    GraphDomain,
    GraphEdge,
    GraphNode,
    GraphProvenance,
    GraphValidationReport,
    RuntimeContext,
    graph_canonical_json,
    graph_digest,
)


_SCOPE_VALUES = frozenset({"user", "esp", "project", "marketplace"})
_STATE_VALUES = frozenset({"draft", "published", "deprecated"})
_PREVIEW_VALUES = frozenset({"none", "pending", "ready", "failed"})
_IMPLEMENTATION_VALUES = frozenset({"executable", "contract_ready", "planned_original", "disabled"})
_COMMERCIAL_USE_VALUES = frozenset({"allowed", "restricted", "unknown", "not_applicable"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: str | None, *, label: str, maximum: int, required: bool = False) -> str:
    cleaned = (value or "").strip()
    if required and not cleaned:
        raise ValueError(f"{label} is required")
    if len(cleaned) > maximum:
        raise ValueError(f"{label} is too long")
    return cleaned


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Catalogue metadata must be strict JSON serializable") from exc


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number is not allowed: {value}")


def _validate_strict_json(payload: str) -> None:
    try:
        json.loads(payload, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("Effect graph must serialize as strict finite JSON") from exc


def _json_object(value: Mapping[str, Any] | None, label: str) -> str:
    if value is None:
        return "{}"
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return _canonical_json(dict(value))


def _report_dict(report: GraphValidationReport) -> dict[str, Any]:
    return {
        "valid": report.valid,
        "issues": [
            {
                "code": issue.code,
                "message": issue.message,
                "node_id": issue.node_id,
                "edge_index": issue.edge_index,
            }
            for issue in report.issues
        ],
        "requirements": {
            "entitlements": list(report.requirements.entitlements),
            "renderers": list(report.requirements.renderers),
            "providers": list(report.requirements.providers),
            "capabilities": list(report.requirements.capabilities),
            "effect_skus": list(report.requirements.effect_skus),
        },
        "resource_cost": {
            "cpu_units": report.resource_cost.cpu_units,
            "memory_mb": report.resource_cost.memory_mb,
            "provider_cost_units": report.resource_cost.provider_cost_units,
            "estimated_ms": report.resource_cost.estimated_ms,
        },
        "depth": report.depth,
        "graph_digest": report.graph_digest,
    }


@dataclass(frozen=True)
class CatalogMetadata:
    category: str = "general"
    implementation_state: str = "executable"
    commercial_use_state: str = "unknown"
    accessibility: Mapping[str, Any] | None = None
    safety: Mapping[str, Any] | None = None
    migration_from_version: int | None = None

    def __post_init__(self) -> None:
        _clean_text(self.category, label="Category", maximum=80, required=True)
        if self.implementation_state not in _IMPLEMENTATION_VALUES:
            raise ValueError(f"Unsupported implementation state: {self.implementation_state}")
        if self.commercial_use_state not in _COMMERCIAL_USE_VALUES:
            raise ValueError(f"Unsupported commercial use state: {self.commercial_use_state}")
        if self.migration_from_version is not None and self.migration_from_version < 1:
            raise ValueError("Migration source version must be positive")
        _json_object(self.accessibility, "Accessibility metadata")
        _json_object(self.safety, "Safety metadata")


@dataclass(frozen=True)
class CatalogVersion:
    catalog_id: str
    version: int
    scope: str
    scope_id: str
    owner_user_id: str | None
    domain: str
    category: str
    title: str
    description: str
    tags: tuple[str, ...]
    state: str
    implementation_state: str
    commercial_use_state: str
    graph_digest: str
    validation_valid: bool
    validation: Mapping[str, Any]
    preview_state: str
    preview_ref: str | None
    migration_from_version: int | None
    accessibility: Mapping[str, Any]
    safety: Mapping[str, Any]
    created_at: str
    published_at: str | None
    deprecated_at: str | None
    deprecated_reason: str | None


class AuraEffectCatalog:
    """Versioned persistence/search for shared Aura Effect/System graphs.

    Graph content versions are immutable once inserted. Draft edits create a new version.
    Publishing and deprecation only transition lifecycle metadata; they never rewrite graph JSON.
    The catalogue stores entitlement/SKU requirements from validation but never debits Creation
    Coins or creates a second entitlement source.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        configured = db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3"
        self.db_path = Path(configured)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS aura_effect_catalog_entries (
                    catalog_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    owner_user_id TEXT,
                    domain TEXT NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS aura_effect_catalog_versions (
                    catalog_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    implementation_state TEXT NOT NULL,
                    commercial_use_state TEXT NOT NULL,
                    graph_json TEXT NOT NULL,
                    graph_digest TEXT NOT NULL,
                    validation_valid INTEGER NOT NULL,
                    validation_json TEXT NOT NULL,
                    requirements_json TEXT NOT NULL,
                    preview_state TEXT NOT NULL DEFAULT 'none',
                    preview_ref TEXT,
                    migration_from_version INTEGER,
                    accessibility_json TEXT NOT NULL DEFAULT '{}',
                    safety_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    published_at TEXT,
                    deprecated_at TEXT,
                    deprecated_reason TEXT,
                    PRIMARY KEY(catalog_id, version),
                    FOREIGN KEY(catalog_id) REFERENCES aura_effect_catalog_entries(catalog_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_aura_effect_catalog_scope
                    ON aura_effect_catalog_entries(scope, scope_id, domain);
                CREATE INDEX IF NOT EXISTS idx_aura_effect_catalog_versions_state
                    ON aura_effect_catalog_versions(state, implementation_state, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_aura_effect_catalog_digest
                    ON aura_effect_catalog_versions(graph_digest);
                """
            )

    @staticmethod
    def _validate_scope(scope: str, scope_id: str, owner_user_id: str | None) -> tuple[str, str, str | None]:
        scope_value = _clean_text(scope, label="Catalogue scope", maximum=32, required=True).lower()
        if scope_value not in _SCOPE_VALUES:
            raise ValueError(f"Unsupported catalogue scope: {scope_value}")
        scope_key = _clean_text(scope_id, label="Scope id", maximum=200, required=True)
        owner = _clean_text(owner_user_id, label="Owner user id", maximum=200) or None
        if scope_value == "user":
            if owner is None:
                raise ValueError("User catalogue scope requires an owner user id")
            if scope_key != owner:
                raise ValueError("User catalogue scope id must match the owner user id")
        return scope_value, scope_key, owner

    def save_draft(
        self,
        graph: EffectGraph,
        *,
        composer: AuraEffectGraphComposer,
        context: RuntimeContext | None,
        scope: str,
        scope_id: str,
        owner_user_id: str | None,
        metadata: CatalogMetadata | None = None,
        expected_latest_version: int | None = None,
    ) -> CatalogVersion:
        metadata = metadata or CatalogMetadata()
        scope_value, scope_key, owner = self._validate_scope(scope, scope_id, owner_user_id)
        materialized = composer.materialize_defaults(graph)
        category = _clean_text(metadata.category, label="Category", maximum=80, required=True)
        now = _now()

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            entry = con.execute(
                "SELECT * FROM aura_effect_catalog_entries WHERE catalog_id=?",
                (materialized.id,),
            ).fetchone()
            current = con.execute(
                "SELECT COALESCE(MAX(version),0) AS version FROM aura_effect_catalog_versions WHERE catalog_id=?",
                (materialized.id,),
            ).fetchone()
            latest_version = int(current["version"] if current else 0)
            if expected_latest_version is not None and latest_version != int(expected_latest_version):
                raise ValueError(
                    f"Catalogue version conflict: expected {expected_latest_version}, current {latest_version}"
                )

            if metadata.migration_from_version is not None:
                source_version = int(metadata.migration_from_version)
                if source_version > latest_version:
                    raise ValueError("Migration source version does not exist")
                source = con.execute(
                    "SELECT 1 FROM aura_effect_catalog_versions WHERE catalog_id=? AND version=?",
                    (materialized.id, source_version),
                ).fetchone()
                if not source:
                    raise ValueError("Migration source version does not exist")

            if entry:
                if entry["scope"] != scope_value or entry["scope_id"] != scope_key:
                    raise ValueError("Catalogue scope cannot change for an existing graph id")
                if (entry["owner_user_id"] or None) != owner:
                    raise ValueError("Catalogue ownership cannot change for an existing graph id")
                if entry["domain"] != materialized.domain.value:
                    raise ValueError("Catalogue domain cannot change for an existing graph id")
                if entry["category"] != category:
                    raise ValueError("Catalogue category cannot change for an existing graph id")
            else:
                con.execute(
                    """INSERT INTO aura_effect_catalog_entries
                       (catalog_id,scope,scope_id,owner_user_id,domain,category,title,description,tags_json,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        materialized.id,
                        scope_value,
                        scope_key,
                        owner,
                        materialized.domain.value,
                        category,
                        materialized.title,
                        materialized.description,
                        _canonical_json(list(materialized.tags)),
                        now,
                        now,
                    ),
                )

            version = latest_version + 1
            stored_graph = replace(materialized, version=version)
            validation = _report_dict(composer.validate(stored_graph, context))
            requirements = validation["requirements"]
            graph_json = graph_canonical_json(stored_graph)
            _validate_strict_json(graph_json)
            digest = graph_digest(stored_graph)
            con.execute(
                """INSERT INTO aura_effect_catalog_versions
                   (catalog_id,version,state,implementation_state,commercial_use_state,graph_json,graph_digest,
                    validation_valid,validation_json,requirements_json,preview_state,preview_ref,
                    migration_from_version,accessibility_json,safety_json,created_at,published_at,deprecated_at,deprecated_reason)
                   VALUES (?,?, 'draft', ?, ?, ?, ?, ?, ?, ?, 'none', NULL, ?, ?, ?, ?, NULL, NULL, NULL)""",
                (
                    stored_graph.id,
                    version,
                    metadata.implementation_state,
                    metadata.commercial_use_state,
                    graph_json,
                    digest,
                    1 if validation["valid"] else 0,
                    _canonical_json(validation),
                    _canonical_json(requirements),
                    metadata.migration_from_version,
                    _json_object(metadata.accessibility, "Accessibility metadata"),
                    _json_object(metadata.safety, "Safety metadata"),
                    now,
                ),
            )
            con.execute(
                """UPDATE aura_effect_catalog_entries
                   SET title=?,description=?,tags_json=?,updated_at=? WHERE catalog_id=?""",
                (
                    stored_graph.title,
                    stored_graph.description,
                    _canonical_json(list(stored_graph.tags)),
                    now,
                    stored_graph.id,
                ),
            )
        return self.get(stored_graph.id, version)

    def publish(
        self,
        catalog_id: str,
        version: int,
        *,
        composer: AuraEffectGraphComposer,
        context: RuntimeContext | None,
    ) -> CatalogVersion:
        graph = self.load_graph(catalog_id, version)
        report = composer.validate(graph, context)
        report.require_valid()
        now = _now()
        validation = _report_dict(report)
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT state,implementation_state,graph_digest FROM aura_effect_catalog_versions WHERE catalog_id=? AND version=?",
                (catalog_id, int(version)),
            ).fetchone()
            if not row:
                raise KeyError("Catalogue version not found")
            if row["state"] != "draft":
                raise ValueError("Only draft catalogue versions can be published")
            if row["implementation_state"] != "executable":
                raise ValueError("Only executable catalogue versions can be published")
            if row["graph_digest"] != graph_digest(graph):
                raise ValueError("Stored graph digest does not match immutable graph content")
            con.execute(
                """UPDATE aura_effect_catalog_versions
                   SET state='published',validation_valid=1,validation_json=?,requirements_json=?,published_at=?
                   WHERE catalog_id=? AND version=? AND state='draft'""",
                (
                    _canonical_json(validation),
                    _canonical_json(validation["requirements"]),
                    now,
                    catalog_id,
                    int(version),
                ),
            )
        return self.get(catalog_id, version)

    def deprecate(self, catalog_id: str, version: int, reason: str) -> CatalogVersion:
        reason_value = _clean_text(reason, label="Deprecation reason", maximum=500, required=True)
        now = _now()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT state FROM aura_effect_catalog_versions WHERE catalog_id=? AND version=?",
                (catalog_id, int(version)),
            ).fetchone()
            if not row:
                raise KeyError("Catalogue version not found")
            if row["state"] != "published":
                raise ValueError("Only published catalogue versions can be deprecated")
            con.execute(
                """UPDATE aura_effect_catalog_versions
                   SET state='deprecated',deprecated_at=?,deprecated_reason=?
                   WHERE catalog_id=? AND version=?""",
                (now, reason_value, catalog_id, int(version)),
            )
        return self.get(catalog_id, version)

    def set_preview(
        self,
        catalog_id: str,
        version: int,
        *,
        preview_state: str,
        preview_ref: str | None = None,
    ) -> CatalogVersion:
        state = _clean_text(preview_state, label="Preview state", maximum=32, required=True).lower()
        if state not in _PREVIEW_VALUES:
            raise ValueError(f"Unsupported preview state: {state}")
        ref = _clean_text(preview_ref, label="Preview reference", maximum=2048) or None
        if state == "ready" and not ref:
            raise ValueError("Ready previews require an artifact reference")
        if state in {"none", "pending"} and ref:
            raise ValueError(f"Preview state {state} cannot carry a completed artifact reference")
        with self._connect() as con:
            cursor = con.execute(
                """UPDATE aura_effect_catalog_versions SET preview_state=?,preview_ref=?
                   WHERE catalog_id=? AND version=?""",
                (state, ref, catalog_id, int(version)),
            )
            if cursor.rowcount != 1:
                raise KeyError("Catalogue version not found")
        return self.get(catalog_id, version)

    def get(self, catalog_id: str, version: int | None = None) -> CatalogVersion:
        with self._connect() as con:
            if version is None:
                row = con.execute(
                    """SELECT e.*,v.* FROM aura_effect_catalog_entries e
                       JOIN aura_effect_catalog_versions v ON v.catalog_id=e.catalog_id
                       WHERE e.catalog_id=? ORDER BY v.version DESC LIMIT 1""",
                    (catalog_id,),
                ).fetchone()
            else:
                row = con.execute(
                    """SELECT e.*,v.* FROM aura_effect_catalog_entries e
                       JOIN aura_effect_catalog_versions v ON v.catalog_id=e.catalog_id
                       WHERE e.catalog_id=? AND v.version=?""",
                    (catalog_id, int(version)),
                ).fetchone()
        if not row:
            raise KeyError("Catalogue version not found")
        return self._record(row)

    def load_graph(self, catalog_id: str, version: int | None = None) -> EffectGraph:
        with self._connect() as con:
            if version is None:
                row = con.execute(
                    """SELECT graph_json FROM aura_effect_catalog_versions
                       WHERE catalog_id=? ORDER BY version DESC LIMIT 1""",
                    (catalog_id,),
                ).fetchone()
            else:
                row = con.execute(
                    "SELECT graph_json FROM aura_effect_catalog_versions WHERE catalog_id=? AND version=?",
                    (catalog_id, int(version)),
                ).fetchone()
        if not row:
            raise KeyError("Catalogue version not found")
        return _graph_from_json(row["graph_json"])

    def history(self, catalog_id: str) -> tuple[CatalogVersion, ...]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT e.*,v.* FROM aura_effect_catalog_entries e
                   JOIN aura_effect_catalog_versions v ON v.catalog_id=e.catalog_id
                   WHERE e.catalog_id=? ORDER BY v.version DESC""",
                (catalog_id,),
            ).fetchall()
        return tuple(self._record(row) for row in rows)

    def search(
        self,
        *,
        scope: str | None = None,
        scope_id: str | None = None,
        domain: GraphDomain | str | None = None,
        state: str | None = None,
        tags: tuple[str, ...] = (),
        text: str | None = None,
        limit: int = 50,
    ) -> tuple[CatalogVersion, ...]:
        clauses: list[str] = []
        values: list[Any] = []
        if scope is not None:
            scope_value = _clean_text(scope, label="Catalogue scope", maximum=32, required=True).lower()
            if scope_value not in _SCOPE_VALUES:
                raise ValueError(f"Unsupported catalogue scope: {scope_value}")
            clauses.append("e.scope=?")
            values.append(scope_value)
        if scope_id is not None:
            clauses.append("e.scope_id=?")
            values.append(_clean_text(scope_id, label="Scope id", maximum=200, required=True))
        if domain is not None:
            domain_value = GraphDomain(domain).value
            clauses.append("e.domain=?")
            values.append(domain_value)
        if state is not None:
            state_value = _clean_text(state, label="Catalogue state", maximum=32, required=True).lower()
            if state_value not in _STATE_VALUES:
                raise ValueError(f"Unsupported catalogue state: {state_value}")
            clauses.append("v.state=?")
            values.append(state_value)
        query = _clean_text(text, label="Search text", maximum=200).lower()
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        max_results = max(1, min(int(limit), 200))
        scan_limit = min(2000, max_results * 10) if (tags or query) else max_results
        sql = (
            "SELECT e.*,v.* FROM aura_effect_catalog_entries e "
            "JOIN aura_effect_catalog_versions v ON v.catalog_id=e.catalog_id"
            + where
            + " ORDER BY v.created_at DESC, e.catalog_id ASC, v.version DESC LIMIT ?"
        )
        values.append(scan_limit)
        with self._connect() as con:
            rows = con.execute(sql, values).fetchall()
        required_tags = {tag.strip().lower() for tag in tags if tag.strip()}
        records: list[CatalogVersion] = []
        for row in rows:
            record = self._record(row)
            record_tags = {tag.lower() for tag in record.tags}
            if required_tags and not required_tags.issubset(record_tags):
                continue
            if query:
                haystack = " ".join((record.title, record.description, record.category, " ".join(record.tags))).lower()
                if query not in haystack:
                    continue
            records.append(record)
            if len(records) >= max_results:
                break
        return tuple(records)

    @staticmethod
    def _record(row: sqlite3.Row) -> CatalogVersion:
        validation = json.loads(row["validation_json"])
        accessibility = json.loads(row["accessibility_json"])
        safety = json.loads(row["safety_json"])
        graph = _graph_from_json(row["graph_json"])
        return CatalogVersion(
            catalog_id=row["catalog_id"],
            version=int(row["version"]),
            scope=row["scope"],
            scope_id=row["scope_id"],
            owner_user_id=row["owner_user_id"],
            domain=row["domain"],
            category=row["category"],
            title=graph.title,
            description=graph.description,
            tags=graph.tags,
            state=row["state"],
            implementation_state=row["implementation_state"],
            commercial_use_state=row["commercial_use_state"],
            graph_digest=row["graph_digest"],
            validation_valid=bool(row["validation_valid"]),
            validation=validation,
            preview_state=row["preview_state"],
            preview_ref=row["preview_ref"],
            migration_from_version=row["migration_from_version"],
            accessibility=accessibility,
            safety=safety,
            created_at=row["created_at"],
            published_at=row["published_at"],
            deprecated_at=row["deprecated_at"],
            deprecated_reason=row["deprecated_reason"],
        )


def _graph_from_json(payload: str) -> EffectGraph:
    try:
        data = json.loads(payload, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("Stored effect graph JSON is invalid") from exc
    if not isinstance(data, dict):
        raise ValueError("Stored effect graph must be an object")
    try:
        nodes_raw = data.get("nodes") or []
        edges_raw = data.get("edges") or []
        provenance_raw = data.get("provenance") or {}
        if not isinstance(nodes_raw, list) or not isinstance(edges_raw, list) or not isinstance(provenance_raw, dict):
            raise ValueError("Stored effect graph has invalid node, edge or provenance structure")
        nodes = tuple(
            GraphNode(
                id=str(item["id"]),
                primitive_id=str(item["primitive_id"]),
                parameters=dict(item.get("parameters") or {}),
            )
            for item in nodes_raw
            if isinstance(item, dict)
        )
        if len(nodes) != len(nodes_raw):
            raise ValueError("Stored effect graph contains an invalid node")
        edges = tuple(
            GraphEdge(
                source_node=str(item["source_node"]),
                source_port=str(item["source_port"]),
                target_node=str(item["target_node"]),
                target_port=str(item["target_port"]),
            )
            for item in edges_raw
            if isinstance(item, dict)
        )
        if len(edges) != len(edges_raw):
            raise ValueError("Stored effect graph contains an invalid edge")
        provenance = GraphProvenance(
            author_id=str(provenance_raw["author_id"]),
            source=str(provenance_raw["source"]),
            licence=str(provenance_raw["licence"]),
            rights_state=str(provenance_raw["rights_state"]),
            source_assets=tuple(str(value) for value in provenance_raw.get("source_assets") or []),
            consent_requirements=tuple(str(value) for value in provenance_raw.get("consent_requirements") or []),
            source_prompt=(
                str(provenance_raw["source_prompt"])
                if provenance_raw.get("source_prompt") is not None
                else None
            ),
        )
        return EffectGraph(
            id=str(data["id"]),
            domain=GraphDomain(str(data["domain"])),
            nodes=nodes,
            edges=edges,
            provenance=provenance,
            version=int(data.get("version") or 1),
            title=str(data.get("title") or ""),
            description=str(data.get("description") or ""),
            tags=tuple(str(value) for value in data.get("tags") or []),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("Stored effect graph"):
            raise
        raise ValueError("Stored effect graph does not match the shared graph contract") from exc


__all__ = [
    "AuraEffectCatalog",
    "CatalogMetadata",
    "CatalogVersion",
]
