from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .owner_auth import owner_authorized

member_router = APIRouter(prefix="/creative/export-governance", tags=["Creative Export Governance"])
owner_router = APIRouter(prefix="/owner/creative/export-governance", tags=["Owner Creative Export Governance"])
_OPAQUE_REF = re.compile(r"^[A-Za-z0-9._:-]{1,180}$")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _member_user_id(request: Request) -> str:
    member = getattr(request.state, "member", None)
    user_id = getattr(member, "user_id", None)
    if not user_id:
        raise HTTPException(401, "Authenticated member session required")
    return str(user_id)


def _require_owner(request: Request) -> None:
    if not owner_authorized(request):
        raise HTTPException(403, "Owner authorization required")


def _opaque_ref(value: str, field_name: str, *, required: bool = True) -> str:
    value = str(value or "").strip()
    if not value and not required:
        return ""
    if not _OPAQUE_REF.fullmatch(value):
        raise ValueError(f"{field_name} must be an opaque identifier, not a URL/path/free-form payload")
    return value


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class OwnerExportReviewInput(BaseModel):
    status: Literal["needs_review", "cleared_for_platform_export", "blocked"]
    review_method: Literal["manual_ip_review", "external_similarity_service", "licensed_catalog_review"]
    evidence_reference: str = Field(min_length=1, max_length=180)
    note: str = Field(default="", max_length=1500)


class ExportProvenanceStore:
    """Evidence layer for exports; never a legal-clearance or copyright-certification engine."""

    def __init__(self, db_path: str | Path | None = None):
        configured = db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3"
        self.db_path = Path(configured)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS creative_export_provenance (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    sequence_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    media_kind TEXT NOT NULL,
                    format TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    commercial_use_requested INTEGER NOT NULL,
                    rights_attested INTEGER NOT NULL,
                    internal_exact_duplicate_detected INTEGER NOT NULL,
                    external_similarity_completed INTEGER NOT NULL DEFAULT 0,
                    review_status TEXT NOT NULL DEFAULT 'needs_review',
                    review_method TEXT NOT NULL DEFAULT '',
                    review_evidence_reference TEXT NOT NULL DEFAULT '',
                    review_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_export_user_file
                    ON creative_export_provenance(user_id, project_name, filename, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_export_sha
                    ON creative_export_provenance(sha256, created_at ASC);
                """
            )

    def record_export(self, *, user_id: str, project_name: str, sequence_id: str, filename: str, media_kind: str, format: str, path: str | Path, commercial_use_requested: bool, rights_attested: bool) -> dict:
        digest = sha256_file(path)
        export_id = uuid4().hex
        with self._connect() as con:
            duplicate = con.execute("SELECT 1 FROM creative_export_provenance WHERE sha256=? LIMIT 1", (digest,)).fetchone() is not None
            con.execute(
                """INSERT INTO creative_export_provenance
                (id,user_id,project_name,sequence_id,filename,media_kind,format,sha256,
                 commercial_use_requested,rights_attested,internal_exact_duplicate_detected,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (export_id, user_id, project_name, sequence_id, filename, media_kind, format, digest, int(commercial_use_requested), int(rights_attested), int(duplicate), _iso()),
            )
        return self.get_for_user(export_id, user_id)

    def get_for_user(self, export_id: str, user_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM creative_export_provenance WHERE id=? AND user_id=?", (export_id, user_id)).fetchone()
        if not row:
            raise KeyError(export_id)
        return self._public(dict(row))

    def latest_for_file(self, user_id: str, project_name: str, filename: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                """SELECT * FROM creative_export_provenance
                   WHERE user_id=? AND project_name=? AND filename=?
                   ORDER BY created_at DESC LIMIT 1""",
                (user_id, project_name, filename),
            ).fetchone()
        return self._public(dict(row)) if row else None

    def owner_review(self, export_id: str, body: OwnerExportReviewInput) -> dict:
        evidence = _opaque_ref(body.evidence_reference, "evidence_reference")
        with self._connect() as con:
            row = con.execute("SELECT * FROM creative_export_provenance WHERE id=?", (export_id,)).fetchone()
            if not row:
                raise KeyError(export_id)
            external_done = body.review_method == "external_similarity_service"
            con.execute(
                """UPDATE creative_export_provenance
                   SET review_status=?,review_method=?,review_evidence_reference=?,review_note=?,
                       external_similarity_completed=?,reviewed_at=? WHERE id=?""",
                (body.status, body.review_method, evidence, body.note.strip(), int(external_done), _iso(), export_id),
            )
            updated = con.execute("SELECT * FROM creative_export_provenance WHERE id=?", (export_id,)).fetchone()
        return self._public(dict(updated))

    @staticmethod
    def _public(row: dict) -> dict:
        row["commercial_use_requested"] = bool(row["commercial_use_requested"])
        row["rights_attested"] = bool(row["rights_attested"])
        row["internal_exact_duplicate_detected"] = bool(row["internal_exact_duplicate_detected"])
        row["external_similarity_completed"] = bool(row["external_similarity_completed"])
        row["similarity_scope"] = "external_review_recorded" if row["external_similarity_completed"] else "internal_exact_sha256_only"
        row["automatic_legal_clearance"] = False
        row["copyrightability_guaranteed"] = False
        row["uniqueness_guaranteed"] = False
        row["grants_esp_role_or_permission"] = False
        row["alters_billing_or_membership"] = False
        row["commercial_platform_export_allowed"] = bool(row["commercial_use_requested"] and row["rights_attested"] and row["review_status"] == "cleared_for_platform_export")
        return row


store = ExportProvenanceStore()


@member_router.get("/{export_id}")
def member_export_provenance(export_id: str, request: Request):
    user_id = _member_user_id(request)
    try:
        return store.get_for_user(export_id, user_id)
    except KeyError as exc:
        raise HTTPException(404, "Export provenance record not found") from exc


@owner_router.post("/{export_id}/review", include_in_schema=False)
def owner_review_export(export_id: str, body: OwnerExportReviewInput, request: Request):
    _require_owner(request)
    try:
        return store.owner_review(export_id, body)
    except KeyError as exc:
        raise HTTPException(404, "Export provenance record not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


__all__ = ["member_router", "owner_router", "store", "ExportProvenanceStore", "OwnerExportReviewInput", "sha256_file"]
