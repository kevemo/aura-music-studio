from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .audit import AuditLedger
from .esp_command_center import esp
from .esp_creator_data_import import ImportConfirm
from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["ESP Creator Import Governance"])

_SOURCE_RESERVATION_TTL = timedelta(minutes=15)
_SOURCE_FORMATS = {"csv", "json", "xlsx"}


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


def _clean(value: str | None, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return default


def source_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _require_creator_member(request: Request):
    member, membership = require_esp_hub_member(request)
    role = "owner" if membership.get("status") == "owner" else (membership.get("roles") or "").lower()
    if role not in {"creator", "both", "owner"}:
        raise HTTPException(403, "ESP Creator access is required for analytics import governance")
    return member


class ImportProvenanceInput(BaseModel):
    provider: str = Field(default="", max_length=80)
    source_label: str = Field(default="", max_length=160)
    captured_at: str | None = Field(default=None, max_length=80)
    period_start: str | None = Field(default=None, max_length=40)
    period_end: str | None = Field(default=None, max_length=40)
    mapping_template_id: str | None = Field(default=None, max_length=64)


class ImportMappingTemplateCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    source_format: Literal["csv", "json", "xlsx"]
    kind: Literal["live", "video"] = "live"
    mapping: dict[str, str] = Field(default_factory=dict, max_length=20)
    period_column: str = Field(default="", max_length=100)
    default_period_label: str = Field(default="Imported analytics", max_length=160)


class CreatorImportGovernanceStore:
    """Add provenance, source deduplication and reusable mappings to Creator imports.

    CreatorDataImportStore remains authoritative for parsing, private file storage, human
    confirmation and Creator Progress writes. This store never creates a second metrics ledger.
    """

    def __init__(self, db_path: str | None = None, audit: AuditLedger | None = None):
        self.db_path = db_path or esp.db_path
        self.audit = audit or AuditLedger(esp.accounts)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS esp_creator_import_sources (
                    user_id TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    import_id TEXT UNIQUE,
                    original_filename TEXT NOT NULL,
                    content_type TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    source_label TEXT NOT NULL DEFAULT '',
                    captured_at TEXT,
                    period_start TEXT,
                    period_end TEXT,
                    mapping_template_id TEXT,
                    reserved_at TEXT NOT NULL,
                    attached_at TEXT,
                    PRIMARY KEY(user_id,source_sha256),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_creator_import_sources_import
                    ON esp_creator_import_sources(import_id);
                CREATE TABLE IF NOT EXISTS esp_creator_import_mapping_templates (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    source_format TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    mapping_json TEXT NOT NULL,
                    period_column TEXT NOT NULL DEFAULT '',
                    default_period_label TEXT NOT NULL DEFAULT 'Imported analytics',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_user_id,source_format,name),
                    FOREIGN KEY(owner_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_creator_import_mappings_owner
                    ON esp_creator_import_mapping_templates(owner_user_id,source_format,name);
                """
            )

    def reserve_source(
        self,
        *,
        user_id: str,
        content: bytes,
        original_filename: str,
        content_type: str,
        provenance: ImportProvenanceInput,
    ) -> dict:
        digest = source_sha256(content)
        now = _now_dt()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute(
                "SELECT * FROM esp_creator_import_sources WHERE user_id=? AND source_sha256=?",
                (user_id, digest),
            ).fetchone()
            if existing:
                if existing["import_id"]:
                    raise FileExistsError(
                        f"duplicate_import:{existing['import_id']}:this exact source file is already staged or resolved"
                    )
                try:
                    reserved_at = datetime.fromisoformat(str(existing["reserved_at"]).replace("Z", "+00:00"))
                except Exception:
                    reserved_at = now
                if reserved_at > now - _SOURCE_RESERVATION_TTL:
                    raise FileExistsError("import_in_progress:this exact source file is already being staged")
                con.execute(
                    "DELETE FROM esp_creator_import_sources WHERE user_id=? AND source_sha256=? AND import_id IS NULL",
                    (user_id, digest),
                )
            con.execute(
                """INSERT INTO esp_creator_import_sources
                   (user_id,source_sha256,import_id,original_filename,content_type,provider,source_label,
                    captured_at,period_start,period_end,mapping_template_id,reserved_at,attached_at)
                   VALUES (?,?,NULL,?,?,?,?,?,?,?,?,?,NULL)""",
                (
                    user_id,
                    digest,
                    _clean(original_filename, 240),
                    _clean(content_type, 160),
                    _clean(provenance.provider, 80),
                    _clean(provenance.source_label, 160),
                    _clean(provenance.captured_at, 80) or None,
                    _clean(provenance.period_start, 40) or None,
                    _clean(provenance.period_end, 40) or None,
                    _clean(provenance.mapping_template_id, 64) or None,
                    now.isoformat(),
                ),
            )
        return {"user_id": user_id, "source_sha256": digest, "reserved_at": now.isoformat()}

    def release_source(self, *, user_id: str, digest: str) -> None:
        with self._connect() as con:
            con.execute(
                "DELETE FROM esp_creator_import_sources WHERE user_id=? AND source_sha256=? AND import_id IS NULL",
                (user_id, digest),
            )

    def attach_import(self, *, user_id: str, digest: str, import_id: str) -> dict:
        with self._connect() as con:
            cursor = con.execute(
                """UPDATE esp_creator_import_sources SET import_id=?,attached_at=?
                   WHERE user_id=? AND source_sha256=? AND import_id IS NULL""",
                (import_id, _now(), user_id, digest),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Evidence source reservation could not be attached to the staged import")
        self.audit.append(
            actor=user_id,
            action="creator.analytics_import_staged",
            subject_user_id=user_id,
            details={"import_id": import_id, "source_sha256": digest},
        )
        return self.provenance_for_import(user_id, import_id)

    def provenance_for_import(self, user_id: str, import_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                """SELECT s.*,i.status,i.source_format,i.created_at,i.imported_at,i.rejected_at
                   FROM esp_creator_import_sources s
                   JOIN esp_creator_data_imports i ON i.id=s.import_id AND i.user_id=s.user_id
                   WHERE s.user_id=? AND s.import_id=?""",
                (user_id, import_id),
            ).fetchone()
        if not row:
            raise KeyError(import_id)
        item = dict(row)
        item["imported_snapshot"] = True
        item["realtime"] = False
        item["direct_backstage_access"] = False
        item["source_hash_algorithm"] = "sha256"
        return item

    def create_mapping(self, owner_user_id: str, payload: ImportMappingTemplateCreate) -> dict:
        validated = ImportConfirm(
            kind=payload.kind,
            mapping=payload.mapping,
            period_column=payload.period_column,
            default_period_label=payload.default_period_label,
        )
        if not validated.mapping:
            raise ValueError("A saved mapping must include at least one supported progress metric")
        mapping_id, now = uuid4().hex, _now()
        try:
            with self._connect() as con:
                con.execute(
                    """INSERT INTO esp_creator_import_mapping_templates
                       (id,owner_user_id,name,source_format,kind,mapping_json,period_column,
                        default_period_label,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        mapping_id,
                        owner_user_id,
                        _clean(payload.name, 120),
                        payload.source_format,
                        validated.kind,
                        json.dumps(validated.mapping, sort_keys=True),
                        validated.period_column,
                        _clean(validated.default_period_label, 160) or "Imported analytics",
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise FileExistsError("mapping_template_exists") from exc
        self.audit.append(
            actor=owner_user_id,
            action="creator.analytics_mapping_created",
            subject_user_id=owner_user_id,
            details={"mapping_id": mapping_id, "source_format": payload.source_format},
        )
        return self.mapping(owner_user_id, mapping_id)

    def mapping(self, owner_user_id: str, mapping_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM esp_creator_import_mapping_templates WHERE owner_user_id=? AND id=?",
                (owner_user_id, mapping_id),
            ).fetchone()
        if not row:
            raise KeyError(mapping_id)
        item = dict(row)
        item["mapping"] = _loads(item.pop("mapping_json"), {})
        return item

    def list_mappings(self, owner_user_id: str, source_format: str | None = None) -> list[dict]:
        if source_format and source_format not in _SOURCE_FORMATS:
            raise ValueError("source_format must be csv, json or xlsx")
        with self._connect() as con:
            if source_format:
                rows = con.execute(
                    """SELECT * FROM esp_creator_import_mapping_templates
                       WHERE owner_user_id=? AND source_format=? ORDER BY name""",
                    (owner_user_id, source_format),
                ).fetchall()
            else:
                rows = con.execute(
                    """SELECT * FROM esp_creator_import_mapping_templates
                       WHERE owner_user_id=? ORDER BY source_format,name""",
                    (owner_user_id,),
                ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["mapping"] = _loads(item.pop("mapping_json"), {})
            result.append(item)
        return result

    def resolve_mapping(self, owner_user_id: str, mapping_id: str | None, source_format: str) -> dict | None:
        if not mapping_id:
            return None
        mapping = self.mapping(owner_user_id, mapping_id)
        if mapping["source_format"] != source_format:
            raise ValueError("Saved mapping source format does not match this import")
        return mapping


governance = CreatorImportGovernanceStore()


@router.get("/command-center/api/progress/import-mappings")
def list_import_mappings(request: Request, source_format: str | None = None):
    member = _require_creator_member(request)
    try:
        return {"mappings": governance.list_mappings(member.user_id, source_format)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/command-center/api/progress/import-mappings")
def create_import_mapping(body: ImportMappingTemplateCreate, request: Request):
    member = _require_creator_member(request)
    try:
        return {"mapping": governance.create_mapping(member.user_id, body)}
    except FileExistsError as exc:
        raise HTTPException(409, {"code": "mapping_template_exists"}) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/command-center/api/progress/imports/{import_id}/provenance")
def import_provenance(import_id: str, request: Request):
    member = _require_creator_member(request)
    try:
        return {"provenance": governance.provenance_for_import(member.user_id, import_id)}
    except KeyError as exc:
        raise HTTPException(404, "Creator data import provenance not found") from exc


__all__ = [
    "CreatorImportGovernanceStore",
    "ImportMappingTemplateCreate",
    "ImportProvenanceInput",
    "governance",
    "router",
    "source_sha256",
]
