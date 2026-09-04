from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .accounts import AccountStore
from .aura_effect_system_creator import compile_effect_system_payload


class EffectSystemConflict(ValueError):
    """Raised when a save would overwrite a newer effect-system version."""


class EffectSystemNotFound(KeyError):
    """Raised when an effect system is not visible to the requesting account."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canonical_spec(compiled: dict[str, Any]) -> dict[str, Any]:
    """Persist the normalized executable graph rather than untrusted raw parameters."""

    system = dict(compiled["system"])
    raw_nodes = list(system.get("nodes") or [])
    effects = list(compiled.get("effects") or [])
    if len(raw_nodes) != len(effects):
        raise ValueError("Compiled effect-system node/effect cardinality mismatch")

    nodes: list[dict[str, Any]] = []
    for node, effect in zip(raw_nodes, effects, strict=True):
        nodes.append(
            {
                "id": str(node["id"]),
                "catalogue_item_id": str(node["catalogue_item_id"]),
                "parameters": dict(effect.get("parameters") or {}),
                "enabled": bool(effect.get("enabled", True)),
                "mix": float(effect.get("mix", 1.0)),
            }
        )
    return {
        "id": str(system["id"]),
        "name": str(system["name"]),
        "description": str(system.get("description") or ""),
        "version": int(system["version"]),
        "nodes": nodes,
    }


class EffectSystemStore:
    """Durable private save/version boundary for Aura-created executable effect systems.

    The store deliberately owns version numbers and account scope. A client may submit a graph, but
    it cannot choose a persisted version, another member's owner id, visibility, publication state,
    runtime, compiled filter chain, or fingerprint. Every saved revision is compiled through the
    existing executable catalogue kernel before it is committed.
    """

    def __init__(self, accounts: AccountStore | None = None):
        self.accounts = accounts or AccountStore()
        self.db_path = self.accounts.db_path
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
                CREATE TABLE IF NOT EXISTS aura_effect_systems (
                    owner_user_id TEXT NOT NULL,
                    system_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    latest_version INTEGER NOT NULL,
                    latest_fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(owner_user_id, system_id),
                    FOREIGN KEY(owner_user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS aura_effect_system_versions (
                    owner_user_id TEXT NOT NULL,
                    system_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    spec_json TEXT NOT NULL,
                    compiled_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(owner_user_id, system_id, version),
                    FOREIGN KEY(owner_user_id, system_id)
                        REFERENCES aura_effect_systems(owner_user_id, system_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_aura_effect_systems_owner_updated
                    ON aura_effect_systems(owner_user_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_aura_effect_system_versions_owner_system
                    ON aura_effect_system_versions(owner_user_id, system_id, version DESC);
                """
            )

    def _require_owner(self, owner_user_id: str) -> dict:
        user = self.accounts.get_user(owner_user_id)
        if not user:
            raise ValueError("Effect-system owner account was not found")
        return user

    @staticmethod
    def _record(head: sqlite3.Row, revision: sqlite3.Row) -> dict[str, Any]:
        spec = json.loads(revision["spec_json"])
        compiled = json.loads(revision["compiled_json"])
        return {
            "owner_user_id": head["owner_user_id"],
            "system_id": head["system_id"],
            "name": spec["name"],
            "description": spec.get("description") or "",
            "version": int(revision["version"]),
            "latest_version": int(head["latest_version"]),
            "fingerprint": revision["fingerprint"],
            "created_at": revision["created_at"],
            "updated_at": head["updated_at"],
            "system": spec,
            "compiled": compiled,
            "visibility": "private",
            "shareable": False,
            "published": False,
            "backend_executable": bool(compiled.get("backend_executable")),
            "source_media_mutated": bool(compiled.get("source_media_mutated")),
        }

    def save(
        self,
        owner_user_id: str,
        payload: dict[str, Any],
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """Compile and save one immutable revision with optimistic concurrency.

        New systems accept ``expected_version`` of ``None`` or ``0``. Updating an existing system
        requires the caller to name the latest version it observed, preventing silent last-write
        wins. The submitted ``version`` field, if any, is ignored; persisted version is server-owned.
        """

        self._require_owner(owner_user_id)
        if not isinstance(payload, dict):
            raise ValueError("Effect-system payload must be an object")

        # Validate the identifier/name/nodes before opening the write transaction. Version is
        # intentionally provisional here; the authoritative value is assigned under BEGIN IMMEDIATE.
        provisional = dict(payload)
        provisional["version"] = 1
        initial = compile_effect_system_payload(provisional)
        system_id = str(initial["system"]["id"])

        now = _now_iso()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            head = con.execute(
                "SELECT * FROM aura_effect_systems WHERE owner_user_id=? AND system_id=?",
                (owner_user_id, system_id),
            ).fetchone()

            if head is None:
                if expected_version not in {None, 0}:
                    raise EffectSystemConflict("Effect system does not yet have the expected version")
                next_version = 1
            else:
                latest = int(head["latest_version"])
                if expected_version is None:
                    raise EffectSystemConflict("expected_version is required when updating an effect system")
                if int(expected_version) != latest:
                    raise EffectSystemConflict(
                        f"Effect system version conflict: expected {expected_version}, latest is {latest}"
                    )
                next_version = latest + 1

            authoritative_payload = dict(payload)
            authoritative_payload["version"] = next_version
            compiled = compile_effect_system_payload(authoritative_payload)
            canonical_spec = _canonical_spec(compiled)
            # Recompile the normalized payload before persistence. This guarantees the saved graph,
            # fingerprint and executable filter chain all describe the same catalogue-normalized state.
            compiled = compile_effect_system_payload(canonical_spec)
            canonical_spec = _canonical_spec(compiled)
            fingerprint = str(compiled["fingerprint"])
            name = str(canonical_spec["name"])
            description = str(canonical_spec.get("description") or "")

            if head is None:
                con.execute(
                    """INSERT INTO aura_effect_systems
                       (owner_user_id,system_id,name,description,latest_version,latest_fingerprint,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        owner_user_id,
                        system_id,
                        name,
                        description,
                        next_version,
                        fingerprint,
                        now,
                        now,
                    ),
                )
            else:
                con.execute(
                    """UPDATE aura_effect_systems
                       SET name=?,description=?,latest_version=?,latest_fingerprint=?,updated_at=?
                       WHERE owner_user_id=? AND system_id=?""",
                    (
                        name,
                        description,
                        next_version,
                        fingerprint,
                        now,
                        owner_user_id,
                        system_id,
                    ),
                )

            con.execute(
                """INSERT INTO aura_effect_system_versions
                   (owner_user_id,system_id,version,spec_json,compiled_json,fingerprint,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    owner_user_id,
                    system_id,
                    next_version,
                    _json(canonical_spec),
                    _json(compiled),
                    fingerprint,
                    now,
                ),
            )

        return self.load(owner_user_id, system_id, version=next_version)

    def load(
        self,
        owner_user_id: str,
        system_id: str,
        *,
        version: int | None = None,
    ) -> dict[str, Any]:
        with self._connect() as con:
            head = con.execute(
                "SELECT * FROM aura_effect_systems WHERE owner_user_id=? AND system_id=?",
                (owner_user_id, system_id),
            ).fetchone()
            if head is None:
                raise EffectSystemNotFound(system_id)
            requested = int(version) if version is not None else int(head["latest_version"])
            revision = con.execute(
                """SELECT * FROM aura_effect_system_versions
                   WHERE owner_user_id=? AND system_id=? AND version=?""",
                (owner_user_id, system_id, requested),
            ).fetchone()
            if revision is None:
                raise EffectSystemNotFound(f"{system_id}@{requested}")
        return self._record(head, revision)

    def list_systems(self, owner_user_id: str) -> list[dict[str, Any]]:
        self._require_owner(owner_user_id)
        with self._connect() as con:
            rows = con.execute(
                """SELECT owner_user_id,system_id,name,description,latest_version,
                          latest_fingerprint,created_at,updated_at
                   FROM aura_effect_systems WHERE owner_user_id=?
                   ORDER BY updated_at DESC, system_id ASC""",
                (owner_user_id,),
            ).fetchall()
        return [
            {
                **dict(row),
                "latest_version": int(row["latest_version"]),
                "visibility": "private",
                "shareable": False,
                "published": False,
            }
            for row in rows
        ]

    def history(self, owner_user_id: str, system_id: str) -> list[dict[str, Any]]:
        # The owner-scoped head lookup prevents cross-account enumeration of version existence.
        self.load(owner_user_id, system_id)
        with self._connect() as con:
            rows = con.execute(
                """SELECT version,fingerprint,created_at
                   FROM aura_effect_system_versions
                   WHERE owner_user_id=? AND system_id=? ORDER BY version ASC""",
                (owner_user_id, system_id),
            ).fetchall()
        return [
            {
                "version": int(row["version"]),
                "fingerprint": row["fingerprint"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def verify_integrity(
        self,
        owner_user_id: str,
        system_id: str,
        *,
        version: int | None = None,
    ) -> dict[str, Any]:
        record = self.load(owner_user_id, system_id, version=version)
        try:
            recompiled = compile_effect_system_payload(record["system"])
            valid = (
                recompiled.get("fingerprint") == record["fingerprint"]
                and recompiled.get("ffmpeg_filter_chain")
                == record["compiled"].get("ffmpeg_filter_chain")
                and recompiled.get("runtime") == record["compiled"].get("runtime")
                and bool(recompiled.get("backend_executable"))
            )
        except Exception:
            valid = False
        return {
            "system_id": record["system_id"],
            "version": record["version"],
            "fingerprint": record["fingerprint"],
            "integrity_valid": bool(valid),
            "catalogue_revalidated": True,
            "arbitrary_command_surface": False,
            "source_media_mutated": False,
        }


__all__ = [
    "EffectSystemConflict",
    "EffectSystemNotFound",
    "EffectSystemStore",
]
