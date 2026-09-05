from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_KEY_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,160}$")
_STATUS = {"staged", "active", "overlap", "retired"}
_MIN_OVERLAP_SECONDS = 300
_MAX_OVERLAP_SECONDS = 14 * 24 * 60 * 60


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _clean_reason(reason: str) -> str:
    clean = " ".join(str(reason or "").split())
    if not 3 <= len(clean) <= 240:
        raise ValueError("Aura Sec release signing-trust transition reason must be 3-240 characters")
    return clean


def _parse_public_key(value: bytes | Ed25519PublicKey) -> tuple[bytes, str]:
    if isinstance(value, Ed25519PublicKey):
        key = value
    elif isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        if len(raw) != 32:
            raise ValueError("Aura Sec trusted release Ed25519 public key must be 32 raw bytes")
        try:
            key = Ed25519PublicKey.from_public_bytes(raw)
        except Exception as exc:
            raise ValueError("Aura Sec trusted release Ed25519 public key is invalid") from exc
    else:
        raise TypeError("Aura Sec trusted release signing key must be raw bytes or Ed25519PublicKey")
    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return raw, hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class ReleaseSigningTrustKey:
    key_id: str
    public_key_raw: bytes
    public_key_fingerprint: str
    status: str
    introduced_generation: int
    activated_generation: int | None
    overlap_until: datetime | None
    retired_generation: int | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ReleaseSigningTrustEvent:
    generation: int
    event_type: str
    key_id: str
    predecessor_key_id: str | None
    reason: str
    occurred_at: datetime


@dataclass(frozen=True)
class ReleaseSigningTrustSnapshot:
    generation: int
    captured_at: datetime
    public_keys: Mapping[str, bytes]


class AuraSecReleaseSigningTrustStore:
    """Durable public-key lifecycle for Aura Sec release-signature verification.

    This trust domain is intentionally separate from Aura Sec server-command signing.
    The database contains release *public* keys only: exactly one active key, one optional
    staged successor and one bounded overlap predecessor during rotation. Staged keys are
    not trusted for release verification. Retired key ids and public-key fingerprints are
    irreversible and cannot be reintroduced.

    The store does not contain release private keys, distribute trust bundles to devices,
    configure an HSM/KMS, download releases, execute installers or replace platform code
    signing/notarization. Fleet trust distribution remains a separate production adapter.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=10.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        return con

    def _initialize(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS aura_sec_release_signing_trust_meta (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    generation INTEGER NOT NULL CHECK(generation>=0)
                );
                INSERT OR IGNORE INTO aura_sec_release_signing_trust_meta(singleton,generation)
                VALUES(1,0);

                CREATE TABLE IF NOT EXISTS aura_sec_release_signing_trust_keys (
                    key_id TEXT PRIMARY KEY,
                    public_key_raw BLOB NOT NULL,
                    public_key_fingerprint TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK(status IN ('staged','active','overlap','retired')),
                    introduced_generation INTEGER NOT NULL CHECK(introduced_generation>=1),
                    activated_generation INTEGER,
                    overlap_until TEXT,
                    retired_generation INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS aura_sec_release_one_active_signing_key
                ON aura_sec_release_signing_trust_keys(status) WHERE status='active';

                CREATE UNIQUE INDEX IF NOT EXISTS aura_sec_release_one_staged_signing_key
                ON aura_sec_release_signing_trust_keys(status) WHERE status='staged';

                CREATE UNIQUE INDEX IF NOT EXISTS aura_sec_release_one_overlap_signing_key
                ON aura_sec_release_signing_trust_keys(status) WHERE status='overlap';

                CREATE TABLE IF NOT EXISTS aura_sec_release_signing_trust_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generation INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    key_id TEXT NOT NULL,
                    predecessor_key_id TEXT,
                    reason TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _generation(con: sqlite3.Connection) -> int:
        row = con.execute(
            "SELECT generation FROM aura_sec_release_signing_trust_meta WHERE singleton=1"
        ).fetchone()
        if row is None:
            raise RuntimeError("Aura Sec release signing trust generation state is missing")
        return int(row["generation"])

    @classmethod
    def _next_generation(cls, con: sqlite3.Connection) -> int:
        generation = cls._generation(con) + 1
        con.execute(
            "UPDATE aura_sec_release_signing_trust_meta SET generation=? WHERE singleton=1",
            (generation,),
        )
        return generation

    @staticmethod
    def _event(
        con: sqlite3.Connection,
        *,
        generation: int,
        event_type: str,
        key_id: str,
        predecessor_key_id: str | None,
        reason: str,
        occurred_at: datetime,
    ) -> None:
        con.execute(
            """INSERT INTO aura_sec_release_signing_trust_events(
                   generation,event_type,key_id,predecessor_key_id,reason,occurred_at
               ) VALUES(?,?,?,?,?,?)""",
            (
                generation,
                event_type,
                key_id,
                predecessor_key_id,
                reason,
                _iso(occurred_at),
            ),
        )

    @staticmethod
    def _key_from_row(row: sqlite3.Row) -> ReleaseSigningTrustKey:
        status = str(row["status"])
        if status not in _STATUS:
            raise RuntimeError("Aura Sec release signing trust contains an invalid key status")
        return ReleaseSigningTrustKey(
            key_id=str(row["key_id"]),
            public_key_raw=bytes(row["public_key_raw"]),
            public_key_fingerprint=str(row["public_key_fingerprint"]),
            status=status,
            introduced_generation=int(row["introduced_generation"]),
            activated_generation=(
                int(row["activated_generation"])
                if row["activated_generation"] is not None
                else None
            ),
            overlap_until=_from_iso(row["overlap_until"]),
            retired_generation=(
                int(row["retired_generation"])
                if row["retired_generation"] is not None
                else None
            ),
            created_at=_from_iso(str(row["created_at"]))
            or datetime.min.replace(tzinfo=timezone.utc),
            updated_at=_from_iso(str(row["updated_at"]))
            or datetime.min.replace(tzinfo=timezone.utc),
        )

    def current_generation(self) -> int:
        with self._connect() as con:
            return self._generation(con)

    def keys(self) -> list[ReleaseSigningTrustKey]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT * FROM aura_sec_release_signing_trust_keys
                   ORDER BY introduced_generation,key_id"""
            ).fetchall()
        return [self._key_from_row(row) for row in rows]

    def active_key(self) -> ReleaseSigningTrustKey:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM aura_sec_release_signing_trust_keys WHERE status='active'"
            ).fetchall()
        if len(rows) != 1:
            raise PermissionError("Aura Sec release signing trust requires exactly one active key")
        return self._key_from_row(rows[0])

    def audit_events(self) -> list[ReleaseSigningTrustEvent]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT generation,event_type,key_id,predecessor_key_id,reason,occurred_at
                   FROM aura_sec_release_signing_trust_events ORDER BY id"""
            ).fetchall()
        return [
            ReleaseSigningTrustEvent(
                generation=int(row["generation"]),
                event_type=str(row["event_type"]),
                key_id=str(row["key_id"]),
                predecessor_key_id=(
                    str(row["predecessor_key_id"])
                    if row["predecessor_key_id"] is not None
                    else None
                ),
                reason=str(row["reason"]),
                occurred_at=_from_iso(str(row["occurred_at"]))
                or datetime.min.replace(tzinfo=timezone.utc),
            )
            for row in rows
        ]

    def bootstrap(
        self,
        public_key: bytes | Ed25519PublicKey,
        *,
        key_id: str,
        reason: str = "initial Aura Sec release signing trust bootstrap",
        now: datetime | None = None,
    ) -> ReleaseSigningTrustKey:
        key_id = str(key_id or "").strip()
        if not _KEY_ID_RE.fullmatch(key_id):
            raise ValueError("Invalid Aura Sec release signing trust key id")
        raw, fingerprint = _parse_public_key(public_key)
        reason = _clean_reason(reason)
        current = _utc(now)
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if self._generation(con) != 0:
                raise PermissionError("Aura Sec release signing trust has already been bootstrapped")
            if con.execute("SELECT 1 FROM aura_sec_release_signing_trust_keys LIMIT 1").fetchone():
                raise PermissionError("Aura Sec release signing trust already contains key material")
            generation = self._next_generation(con)
            con.execute(
                """INSERT INTO aura_sec_release_signing_trust_keys(
                       key_id,public_key_raw,public_key_fingerprint,status,
                       introduced_generation,activated_generation,overlap_until,
                       retired_generation,created_at,updated_at
                   ) VALUES(?,?,?,'active',?,?,NULL,NULL,?,?)""",
                (
                    key_id,
                    raw,
                    fingerprint,
                    generation,
                    generation,
                    _iso(current),
                    _iso(current),
                ),
            )
            self._event(
                con,
                generation=generation,
                event_type="bootstrap",
                key_id=key_id,
                predecessor_key_id=None,
                reason=reason,
                occurred_at=current,
            )
        return self.active_key()

    def stage_successor(
        self,
        public_key: bytes | Ed25519PublicKey,
        *,
        key_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> ReleaseSigningTrustKey:
        key_id = str(key_id or "").strip()
        if not _KEY_ID_RE.fullmatch(key_id):
            raise ValueError("Invalid Aura Sec successor release signing key id")
        raw, fingerprint = _parse_public_key(public_key)
        reason = _clean_reason(reason)
        current = _utc(now)
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            active = con.execute(
                "SELECT key_id FROM aura_sec_release_signing_trust_keys WHERE status='active'"
            ).fetchall()
            if len(active) != 1:
                raise PermissionError("Aura Sec release key rotation requires exactly one active key")
            if con.execute(
                """SELECT 1 FROM aura_sec_release_signing_trust_keys
                   WHERE status IN ('staged','overlap') LIMIT 1"""
            ).fetchone():
                raise PermissionError(
                    "Aura Sec release key rotation must retire the prior transition before staging another"
                )
            if con.execute(
                """SELECT 1 FROM aura_sec_release_signing_trust_keys
                   WHERE key_id=? OR public_key_fingerprint=?""",
                (key_id, fingerprint),
            ).fetchone():
                raise PermissionError(
                    "Aura Sec release key rotation cannot reuse a prior key id or public key"
                )
            generation = self._next_generation(con)
            con.execute(
                """INSERT INTO aura_sec_release_signing_trust_keys(
                       key_id,public_key_raw,public_key_fingerprint,status,
                       introduced_generation,activated_generation,overlap_until,
                       retired_generation,created_at,updated_at
                   ) VALUES(?,?,?,'staged',?,NULL,NULL,NULL,?,?)""",
                (key_id, raw, fingerprint, generation, _iso(current), _iso(current)),
            )
            self._event(
                con,
                generation=generation,
                event_type="stage_successor",
                key_id=key_id,
                predecessor_key_id=str(active[0]["key_id"]),
                reason=reason,
                occurred_at=current,
            )
            row = con.execute(
                "SELECT * FROM aura_sec_release_signing_trust_keys WHERE key_id=?",
                (key_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Aura Sec staged release signing key was not persisted")
        return self._key_from_row(row)

    def activate_successor(
        self,
        key_id: str,
        *,
        overlap_seconds: int,
        reason: str,
        now: datetime | None = None,
    ) -> ReleaseSigningTrustKey:
        key_id = str(key_id or "").strip()
        if not _KEY_ID_RE.fullmatch(key_id):
            raise ValueError("Invalid Aura Sec successor release signing key id")
        overlap_seconds = int(overlap_seconds)
        if not _MIN_OVERLAP_SECONDS <= overlap_seconds <= _MAX_OVERLAP_SECONDS:
            raise ValueError("Aura Sec release signing overlap must be between 5 minutes and 14 days")
        reason = _clean_reason(reason)
        current = _utc(now)
        overlap_until = current + timedelta(seconds=overlap_seconds)
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            staged = con.execute(
                """SELECT * FROM aura_sec_release_signing_trust_keys
                   WHERE key_id=? AND status='staged'""",
                (key_id,),
            ).fetchone()
            if staged is None:
                raise PermissionError("Aura Sec successor release signing key is not staged")
            active = con.execute(
                "SELECT * FROM aura_sec_release_signing_trust_keys WHERE status='active'"
            ).fetchall()
            if len(active) != 1:
                raise PermissionError("Aura Sec release key rotation requires exactly one active key")
            if con.execute(
                "SELECT 1 FROM aura_sec_release_signing_trust_keys WHERE status='overlap' LIMIT 1"
            ).fetchone():
                raise PermissionError("Aura Sec release key rotation already has an overlap predecessor")
            predecessor = str(active[0]["key_id"])
            generation = self._next_generation(con)
            con.execute(
                """UPDATE aura_sec_release_signing_trust_keys
                   SET status='overlap',overlap_until=?,updated_at=?
                   WHERE key_id=? AND status='active'""",
                (_iso(overlap_until), _iso(current), predecessor),
            )
            con.execute(
                """UPDATE aura_sec_release_signing_trust_keys
                   SET status='active',activated_generation=?,updated_at=?
                   WHERE key_id=? AND status='staged'""",
                (generation, _iso(current), key_id),
            )
            self._event(
                con,
                generation=generation,
                event_type="activate_successor",
                key_id=key_id,
                predecessor_key_id=predecessor,
                reason=reason,
                occurred_at=current,
            )
            row = con.execute(
                "SELECT * FROM aura_sec_release_signing_trust_keys WHERE key_id=?",
                (key_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Aura Sec active release signing successor was not persisted")
        return self._key_from_row(row)

    def trusted_public_keys(self, *, now: datetime | None = None) -> Mapping[str, bytes]:
        current = _utc(now)
        with self._connect() as con:
            active_rows = con.execute(
                "SELECT key_id,public_key_raw FROM aura_sec_release_signing_trust_keys WHERE status='active'"
            ).fetchall()
            if len(active_rows) != 1:
                raise PermissionError("Aura Sec release signing trust requires exactly one active key")
            overlap_rows = con.execute(
                """SELECT key_id,public_key_raw,overlap_until
                   FROM aura_sec_release_signing_trust_keys WHERE status='overlap'"""
            ).fetchall()
        trusted = {str(active_rows[0]["key_id"]): bytes(active_rows[0]["public_key_raw"])}
        for row in overlap_rows:
            overlap_until = _from_iso(row["overlap_until"])
            if overlap_until is not None and current < overlap_until:
                trusted[str(row["key_id"])] = bytes(row["public_key_raw"])
        return MappingProxyType(trusted)

    def snapshot(self, *, now: datetime | None = None) -> ReleaseSigningTrustSnapshot:
        current = _utc(now)
        return ReleaseSigningTrustSnapshot(
            generation=self.current_generation(),
            captured_at=current,
            public_keys=self.trusted_public_keys(now=current),
        )

    def retire_expired(self, *, now: datetime | None = None) -> list[str]:
        current = _utc(now)
        retired: list[str] = []
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            rows = con.execute(
                """SELECT key_id,overlap_until FROM aura_sec_release_signing_trust_keys
                   WHERE status='overlap'"""
            ).fetchall()
            for row in rows:
                overlap_until = _from_iso(row["overlap_until"])
                if overlap_until is None or current < overlap_until:
                    continue
                key_id = str(row["key_id"])
                generation = self._next_generation(con)
                con.execute(
                    """UPDATE aura_sec_release_signing_trust_keys
                       SET status='retired',retired_generation=?,updated_at=?
                       WHERE key_id=? AND status='overlap'""",
                    (generation, _iso(current), key_id),
                )
                self._event(
                    con,
                    generation=generation,
                    event_type="retire_expired",
                    key_id=key_id,
                    predecessor_key_id=None,
                    reason="release signing overlap expired",
                    occurred_at=current,
                )
                retired.append(key_id)
        return retired


__all__ = [
    "AuraSecReleaseSigningTrustStore",
    "ReleaseSigningTrustEvent",
    "ReleaseSigningTrustKey",
    "ReleaseSigningTrustSnapshot",
]
