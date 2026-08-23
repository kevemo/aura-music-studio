from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Iterable
from uuid import uuid4

from .accounts import AccountStore


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_list(values: Iterable[str]) -> str:
    clean = sorted({str(x).strip().lower() for x in values if str(x).strip()})
    return json.dumps(clean)


class ComputeNodeRegistry:
    """ESP-owned compute-node identity, enrollment and capability registry.

    Nodes authenticate with unique high-entropy credentials. The coordinator stores only SHA-256
    hashes of node/enrollment secrets; plaintext credentials are returned only at creation/enrollment.
    """

    def __init__(self, store: AccountStore | None = None):
        self.store = store or AccountStore()
        self.db_path = self.store.db_path
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS compute_node_enrollments (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    label TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT,
                    revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS compute_nodes (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    secret_hash TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL DEFAULT '[]',
                    hardware_json TEXT NOT NULL DEFAULT '{}',
                    software_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT,
                    last_ip TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    revoked_at TEXT,
                    notes TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_compute_nodes_seen ON compute_nodes(last_seen_at);
                """
            )

    def create_enrollment(self, *, label: str = "ESP compute node", ttl_minutes: int | None = None) -> dict:
        ttl = max(5, min(int(ttl_minutes or os.getenv("LSS_NODE_ENROLLMENT_TTL_MINUTES", "30")), 1440))
        token = secrets.token_urlsafe(32)
        enrollment_id = uuid4().hex
        created = _now_dt()
        expires = created + timedelta(minutes=ttl)
        with self._connect() as con:
            con.execute(
                "INSERT INTO compute_node_enrollments (id,token_hash,label,created_at,expires_at) VALUES (?,?,?,?,?)",
                (enrollment_id, _hash_secret(token), (label or "ESP compute node")[:120], created.isoformat(), expires.isoformat()),
            )
        return {
            "enrollment_id": enrollment_id,
            "token": token,
            "expires_at": expires.isoformat(),
            "label": (label or "ESP compute node")[:120],
        }

    def enroll(
        self,
        token: str,
        *,
        name: str,
        capabilities: Iterable[str],
        hardware: dict | None = None,
        software: dict | None = None,
        remote_ip: str | None = None,
    ) -> dict:
        token_hash = _hash_secret(token)
        now = _now_dt()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM compute_node_enrollments WHERE token_hash=?",
                (token_hash,),
            ).fetchone()
            if not row:
                con.rollback()
                raise PermissionError("Invalid compute-node enrollment token")
            if row["used_at"] or row["revoked_at"]:
                con.rollback()
                raise PermissionError("This compute-node enrollment token is no longer valid")
            if datetime.fromisoformat(row["expires_at"]) <= now:
                con.rollback()
                raise PermissionError("This compute-node enrollment token has expired")

            node_id = uuid4().hex
            node_secret = secrets.token_urlsafe(48)
            con.execute(
                """INSERT INTO compute_nodes
                   (id,name,secret_hash,capabilities_json,hardware_json,software_json,created_at,last_seen_at,last_ip)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    node_id,
                    (name or "ESP Compute Node")[:120],
                    _hash_secret(node_secret),
                    _json_list(capabilities),
                    json.dumps(hardware or {}, default=str)[:65536],
                    json.dumps(software or {}, default=str)[:65536],
                    now.isoformat(),
                    now.isoformat(),
                    (remote_ip or "")[:128] or None,
                ),
            )
            con.execute(
                "UPDATE compute_node_enrollments SET used_at=? WHERE id=?",
                (now.isoformat(), row["id"]),
            )
            con.commit()
        return {
            "node_id": node_id,
            "node_secret": node_secret,
            "name": (name or "ESP Compute Node")[:120],
            "capabilities": json.loads(_json_list(capabilities)),
        }

    def authenticate(self, node_id: str, secret: str) -> dict:
        if not node_id or not secret:
            raise PermissionError("Node authentication required")
        with self._connect() as con:
            row = con.execute("SELECT * FROM compute_nodes WHERE id=?", (node_id,)).fetchone()
        if not row or row["revoked_at"] or row["status"] == "revoked":
            raise PermissionError("Unknown or revoked compute node")
        supplied = _hash_secret(secret)
        if not hmac.compare_digest(row["secret_hash"], supplied):
            raise PermissionError("Invalid compute-node credential")
        return self._public_node(dict(row), include_private_runtime=True)

    def heartbeat(
        self,
        node_id: str,
        *,
        capabilities: Iterable[str] | None = None,
        hardware: dict | None = None,
        software: dict | None = None,
        remote_ip: str | None = None,
    ) -> dict:
        now = _now()
        updates = ["last_seen_at=?", "status='active'"]
        values: list[object] = [now]
        if capabilities is not None:
            updates.append("capabilities_json=?")
            values.append(_json_list(capabilities))
        if hardware is not None:
            updates.append("hardware_json=?")
            values.append(json.dumps(hardware, default=str)[:65536])
        if software is not None:
            updates.append("software_json=?")
            values.append(json.dumps(software, default=str)[:65536])
        if remote_ip:
            updates.append("last_ip=?")
            values.append(remote_ip[:128])
        values.append(node_id)
        with self._connect() as con:
            con.execute(f"UPDATE compute_nodes SET {', '.join(updates)} WHERE id=?", tuple(values))
            row = con.execute("SELECT * FROM compute_nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            raise KeyError(node_id)
        return self._public_node(dict(row), include_private_runtime=True)

    def revoke(self, node_id: str) -> bool:
        now = _now()
        with self._connect() as con:
            cur = con.execute(
                "UPDATE compute_nodes SET status='revoked', revoked_at=? WHERE id=? AND revoked_at IS NULL",
                (now, node_id),
            )
        return cur.rowcount > 0

    def list_nodes(self, *, stale_after_seconds: int = 180) -> list[dict]:
        cutoff = _now_dt() - timedelta(seconds=max(30, stale_after_seconds))
        with self._connect() as con:
            rows = con.execute("SELECT * FROM compute_nodes ORDER BY created_at ASC").fetchall()
        result = []
        for row in rows:
            item = self._public_node(dict(row), include_private_runtime=False)
            last_seen = item.get("last_seen_at")
            if item.get("status") != "revoked" and last_seen:
                try:
                    item["online"] = datetime.fromisoformat(last_seen) >= cutoff
                except Exception:
                    item["online"] = False
            else:
                item["online"] = False
            result.append(item)
        return result

    def summary(self) -> dict:
        nodes = self.list_nodes(stale_after_seconds=max(60, int(os.getenv("LSS_NODE_HEARTBEAT_SECONDS", "30")) * 4))
        active = [n for n in nodes if n.get("status") != "revoked"]
        online = [n for n in active if n.get("online")]
        capabilities = sorted({cap for node in online for cap in node.get("capabilities", [])})
        return {
            "enabled": (os.getenv("LSS_NODE_COORDINATOR_ENABLED", "true").lower() == "true"),
            "registered": len(active),
            "online": len(online),
            "available_capabilities": capabilities,
            "outbound_worker_connections": True,
            "worker_public_ports_required": False,
        }

    @staticmethod
    def _public_node(row: dict, *, include_private_runtime: bool) -> dict:
        def load(value: str, fallback):
            try:
                parsed = json.loads(value or "")
                return parsed
            except Exception:
                return fallback

        item = {
            "id": row["id"],
            "name": row["name"],
            "capabilities": load(row.get("capabilities_json"), []),
            "hardware": load(row.get("hardware_json"), {}),
            "software": load(row.get("software_json"), {}),
            "created_at": row.get("created_at"),
            "last_seen_at": row.get("last_seen_at"),
            "status": row.get("status"),
            "revoked_at": row.get("revoked_at"),
            "notes": row.get("notes") or "",
            "credential_exposed": False,
        }
        if include_private_runtime:
            item["last_ip"] = row.get("last_ip")
        return item
