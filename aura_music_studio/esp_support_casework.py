from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .audit import AuditLedger
from .esp_command_center import EspStore, esp
from .esp_niche import require_esp_hub_member
from .esp_support_center import SupportCaseStore, support

router = APIRouter(tags=["ESP Support Casework"])

Visibility = Literal["creator", "internal"]
RelationType = Literal["related", "duplicate_of", "supersedes", "related_record"]
_ALLOWED_ATTACHMENT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".txt", ".csv", ".json", ".xlsx"}
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: str | None, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _safe_filename(value: str | None) -> str:
    name = Path(value or "support-evidence").name
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:180]
    return safe or "support-evidence"


def _loads(value: str | None, default):
    try:
        return json.loads(value or "")
    except Exception:
        return default


class CaseMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=6000)
    visibility: Visibility = "creator"
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=160)


class CaseRelationCreate(BaseModel):
    relation_type: RelationType
    related_case_id: str | None = Field(default=None, max_length=64)
    resource_type: str = Field(default="", max_length=80)
    resource_id: str = Field(default="", max_length=180)
    label: str = Field(default="", max_length=240)
    visibility: Visibility = "internal"
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=160)


class CaseAssignmentCreate(BaseModel):
    assignee_user_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="", max_length=1000)
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=160)


class CaseEscalationEventCreate(BaseModel):
    target: str = Field(min_length=2, max_length=80)
    reason: str = Field(min_length=3, max_length=3000)
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=160)


class SupportCaseworkStore:
    """Conversation, attachment and relationship layer over the canonical ESP support case ledger.

    ``esp_support_cases`` remains the authoritative case record. This layer adds workflow state
    that the foundation does not model: user-visible replies versus internal notes, private
    attachments, related/duplicate links, assignment/escalation history, optimistic revisions
    and idempotency. It never exposes a local storage path in an API projection.
    """

    def __init__(
        self,
        esp_store: EspStore | None = None,
        case_store: SupportCaseStore | None = None,
        audit: AuditLedger | None = None,
        storage_root: str | Path | None = None,
    ):
        self.esp = esp_store or esp
        self.cases = case_store or support
        self.db_path = self.esp.db_path
        self.audit = audit or AuditLedger(self.esp.accounts)
        root = storage_root or os.getenv("ESP_SUPPORT_ROOT") or "data/esp-support"
        self.storage_root = Path(root)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        # Ensure the canonical case tables exist for clean/test databases.
        self.cases._init_schema()
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS esp_support_casework_state (
                    case_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES esp_support_cases(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS esp_support_case_messages (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    visibility TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES esp_support_cases(id) ON DELETE CASCADE,
                    FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_support_case_messages_case
                    ON esp_support_case_messages(case_id,created_at);

                CREATE TABLE IF NOT EXISTS esp_support_case_attachments (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    content_type TEXT NOT NULL DEFAULT '',
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    visibility TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(case_id,sha256,visibility),
                    FOREIGN KEY(case_id) REFERENCES esp_support_cases(id) ON DELETE CASCADE,
                    FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_support_case_attachments_case
                    ON esp_support_case_attachments(case_id,created_at);

                CREATE TABLE IF NOT EXISTS esp_support_case_relations (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    related_case_id TEXT,
                    resource_type TEXT NOT NULL DEFAULT '',
                    resource_id TEXT NOT NULL DEFAULT '',
                    label TEXT NOT NULL DEFAULT '',
                    visibility TEXT NOT NULL DEFAULT 'internal',
                    actor_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES esp_support_cases(id) ON DELETE CASCADE,
                    FOREIGN KEY(related_case_id) REFERENCES esp_support_cases(id) ON DELETE CASCADE,
                    FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_support_case_relations_case
                    ON esp_support_case_relations(case_id,created_at);

                CREATE TABLE IF NOT EXISTS esp_support_case_events (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    visibility TEXT NOT NULL DEFAULT 'internal',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES esp_support_cases(id) ON DELETE CASCADE,
                    FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_support_case_events_case
                    ON esp_support_case_events(case_id,created_at);

                CREATE TABLE IF NOT EXISTS esp_support_case_idempotency (
                    case_id TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(case_id,actor_user_id,action,idempotency_key),
                    FOREIGN KEY(case_id) REFERENCES esp_support_cases(id) ON DELETE CASCADE,
                    FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                """
            )

    def _membership_role(self, user_id: str) -> tuple[dict | None, str]:
        membership = self.esp.membership(user_id)
        if not membership or membership.get("status") not in {"active", "owner"}:
            return membership, ""
        role = "owner" if membership.get("status") == "owner" else (membership.get("roles") or "").lower()
        return membership, role

    def _case_row(self, con: sqlite3.Connection, case_id: str) -> sqlite3.Row:
        row = con.execute("SELECT * FROM esp_support_cases WHERE id=?", (case_id,)).fetchone()
        if row is None:
            raise KeyError("Support case not found")
        return row

    def _active_assignment(self, con: sqlite3.Connection, agent_user_id: str, creator_user_id: str) -> bool:
        row = con.execute(
            """SELECT 1 FROM esp_agent_creator_assignments
               WHERE agent_user_id=? AND creator_user_id=? AND status='active'""",
            (agent_user_id, creator_user_id),
        ).fetchone()
        return row is not None

    def authorize(self, actor_user_id: str, case_id: str, *, staff_only: bool = False) -> tuple[dict, str]:
        _membership, role = self._membership_role(actor_user_id)
        if not role:
            raise PermissionError("Active ESP membership is required")
        with self._connect() as con:
            case = dict(self._case_row(con, case_id))
            if role == "owner":
                return case, role
            if actor_user_id == case["user_id"] and role in {"creator", "both"} and not staff_only:
                return case, "creator"
            if role in {"agent", "both"} and self._active_assignment(con, actor_user_id, case["user_id"]):
                return case, "agent"
        raise PermissionError("Support case is outside this member's authorised scope")

    def _ensure_state(self, con: sqlite3.Connection, case_id: str) -> int:
        row = con.execute("SELECT revision FROM esp_support_casework_state WHERE case_id=?", (case_id,)).fetchone()
        if row:
            return int(row["revision"])
        con.execute(
            "INSERT INTO esp_support_casework_state(case_id,revision,updated_at) VALUES (?,0,?)",
            (case_id, _now()),
        )
        return 0

    def revision(self, case_id: str) -> int:
        with self._connect() as con:
            self._case_row(con, case_id)
            return self._ensure_state(con, case_id)

    def _idempotent_result(
        self,
        con: sqlite3.Connection,
        case_id: str,
        actor_user_id: str,
        action: str,
        key: str,
    ) -> dict | None:
        row = con.execute(
            """SELECT result_json FROM esp_support_case_idempotency
               WHERE case_id=? AND actor_user_id=? AND action=? AND idempotency_key=?""",
            (case_id, actor_user_id, action, key),
        ).fetchone()
        return _loads(row["result_json"], {}) if row else None

    def _advance_revision(
        self,
        con: sqlite3.Connection,
        case_id: str,
        expected_revision: int,
    ) -> int:
        current = self._ensure_state(con, case_id)
        if current != expected_revision:
            raise RuntimeError(f"revision_conflict:{current}")
        next_revision = current + 1
        con.execute(
            "UPDATE esp_support_casework_state SET revision=?,updated_at=? WHERE case_id=? AND revision=?",
            (next_revision, _now(), case_id, current),
        )
        if con.total_changes < 1:
            raise RuntimeError(f"revision_conflict:{current}")
        return next_revision

    def _save_idempotent_result(
        self,
        con: sqlite3.Connection,
        case_id: str,
        actor_user_id: str,
        action: str,
        key: str,
        result: dict,
    ) -> None:
        con.execute(
            """INSERT INTO esp_support_case_idempotency
               (case_id,actor_user_id,action,idempotency_key,result_json,created_at)
               VALUES (?,?,?,?,?,?)""",
            (case_id, actor_user_id, action, key, json.dumps(result, sort_keys=True), _now()),
        )

    @staticmethod
    def _message_projection(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "case_id": row["case_id"],
            "actor_user_id": row["actor_user_id"],
            "body": row["body"],
            "visibility": row["visibility"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _attachment_projection(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "case_id": row["case_id"],
            "actor_user_id": row["actor_user_id"],
            "original_name": row["original_name"],
            "content_type": row["content_type"],
            "size_bytes": row["size_bytes"],
            "sha256": row["sha256"],
            "visibility": row["visibility"],
            "created_at": row["created_at"],
            "download_path": f"/command-center/api/support/cases/{row['case_id']}/attachments/{row['id']}/download",
            "private_storage_path_exposed": False,
        }

    @staticmethod
    def _relation_projection(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "case_id": row["case_id"],
            "relation_type": row["relation_type"],
            "related_case_id": row["related_case_id"],
            "resource_type": row["resource_type"],
            "resource_id": row["resource_id"],
            "label": row["label"],
            "visibility": row["visibility"],
            "actor_user_id": row["actor_user_id"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _event_projection(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "case_id": row["case_id"],
            "actor_user_id": row["actor_user_id"],
            "event_type": row["event_type"],
            "details": _loads(row["details_json"], {}),
            "visibility": row["visibility"],
            "created_at": row["created_at"],
        }

    def project(self, case_id: str, actor_user_id: str) -> dict:
        case, role = self.authorize(actor_user_id, case_id)
        creator_view = role == "creator"
        with self._connect() as con:
            revision = self._ensure_state(con, case_id)
            if creator_view:
                messages = con.execute(
                    "SELECT * FROM esp_support_case_messages WHERE case_id=? AND visibility='creator' ORDER BY created_at,id",
                    (case_id,),
                ).fetchall()
                attachments = con.execute(
                    "SELECT * FROM esp_support_case_attachments WHERE case_id=? AND visibility='creator' ORDER BY created_at,id",
                    (case_id,),
                ).fetchall()
                relations = con.execute(
                    "SELECT * FROM esp_support_case_relations WHERE case_id=? AND visibility='creator' ORDER BY created_at,id",
                    (case_id,),
                ).fetchall()
                events = con.execute(
                    "SELECT * FROM esp_support_case_events WHERE case_id=? AND visibility='creator' ORDER BY created_at,id",
                    (case_id,),
                ).fetchall()
            else:
                messages = con.execute("SELECT * FROM esp_support_case_messages WHERE case_id=? ORDER BY created_at,id", (case_id,)).fetchall()
                attachments = con.execute("SELECT * FROM esp_support_case_attachments WHERE case_id=? ORDER BY created_at,id", (case_id,)).fetchall()
                relations = con.execute("SELECT * FROM esp_support_case_relations WHERE case_id=? ORDER BY created_at,id", (case_id,)).fetchall()
                events = con.execute("SELECT * FROM esp_support_case_events WHERE case_id=? ORDER BY created_at,id", (case_id,)).fetchall()
        case.pop("resolution", None) if creator_view and case.get("status") not in {"resolved", "closed"} else None
        return {
            "case": case,
            "revision": revision,
            "messages": [self._message_projection(row) for row in messages],
            "attachments": [self._attachment_projection(row) for row in attachments],
            "relations": [self._relation_projection(row) for row in relations],
            "events": [self._event_projection(row) for row in events],
            "view": "creator" if creator_view else "staff",
            "private_storage_paths_exposed": False,
        }

    def add_message(self, case_id: str, actor_user_id: str, body: CaseMessageCreate) -> dict:
        _case, role = self.authorize(actor_user_id, case_id, staff_only=body.visibility == "internal")
        if role == "creator" and body.visibility != "creator":
            raise PermissionError("Creators cannot create internal support notes")
        action = "message_internal" if body.visibility == "internal" else "message_creator"
        clean_body = (body.body or "").strip()[:6000]
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = self._idempotent_result(con, case_id, actor_user_id, action, body.idempotency_key)
            if existing is not None:
                return existing
            next_revision = self._advance_revision(con, case_id, body.expected_revision)
            message_id = uuid4().hex
            created = _now()
            con.execute(
                """INSERT INTO esp_support_case_messages(id,case_id,actor_user_id,body,visibility,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (message_id, case_id, actor_user_id, clean_body, body.visibility, created),
            )
            con.execute("UPDATE esp_support_cases SET updated_at=? WHERE id=?", (created, case_id))
            result = {"message_id": message_id, "revision": next_revision, "visibility": body.visibility}
            self._save_idempotent_result(con, case_id, actor_user_id, action, body.idempotency_key, result)
        self.audit.append(
            actor=actor_user_id,
            action="support.case_message_added",
            subject_user_id=case_id,
            details={"message_id": result["message_id"], "visibility": body.visibility},
        )
        return result

    def add_attachment(
        self,
        case_id: str,
        actor_user_id: str,
        *,
        filename: str,
        content_type: str,
        content: bytes,
        visibility: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> dict:
        if visibility not in {"creator", "internal"}:
            raise ValueError("Attachment visibility must be creator or internal")
        _case, role = self.authorize(actor_user_id, case_id, staff_only=visibility == "internal")
        if role == "creator" and visibility != "creator":
            raise PermissionError("Creators cannot create internal support attachments")
        safe = _safe_filename(filename)
        suffix = Path(safe).suffix.lower()
        if suffix not in _ALLOWED_ATTACHMENT_EXTENSIONS:
            raise ValueError("Unsupported support attachment type")
        if not content:
            raise ValueError("Support attachment is empty")
        if len(content) > _MAX_ATTACHMENT_BYTES:
            raise ValueError("Support attachment exceeds the 10 MB limit")
        digest = hashlib.sha256(content).hexdigest()
        action = f"attachment_{visibility}"
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = self._idempotent_result(con, case_id, actor_user_id, action, idempotency_key)
            if existing is not None:
                return existing
            duplicate = con.execute(
                "SELECT id FROM esp_support_case_attachments WHERE case_id=? AND sha256=? AND visibility=?",
                (case_id, digest, visibility),
            ).fetchone()
            if duplicate:
                raise FileExistsError(f"duplicate_attachment:{duplicate['id']}")
            next_revision = self._advance_revision(con, case_id, expected_revision)
            attachment_id = uuid4().hex
            case_dir = self.storage_root / case_id
            case_dir.mkdir(parents=True, exist_ok=True)
            storage_path = case_dir / f"{attachment_id}{suffix}"
            storage_path.write_bytes(content)
            try:
                con.execute(
                    """INSERT INTO esp_support_case_attachments
                       (id,case_id,actor_user_id,original_name,content_type,size_bytes,sha256,storage_path,visibility,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        attachment_id,
                        case_id,
                        actor_user_id,
                        safe,
                        _clean(content_type or mimetypes.guess_type(safe)[0] or "application/octet-stream", 160),
                        len(content),
                        digest,
                        str(storage_path),
                        visibility,
                        _now(),
                    ),
                )
                result = {"attachment_id": attachment_id, "revision": next_revision, "visibility": visibility}
                self._save_idempotent_result(con, case_id, actor_user_id, action, idempotency_key, result)
            except Exception:
                storage_path.unlink(missing_ok=True)
                raise
        self.audit.append(
            actor=actor_user_id,
            action="support.case_attachment_added",
            subject_user_id=case_id,
            details={"attachment_id": result["attachment_id"], "visibility": visibility, "sha256": digest},
        )
        return result

    def attachment_file(self, case_id: str, attachment_id: str, actor_user_id: str) -> tuple[Path, dict]:
        _case, role = self.authorize(actor_user_id, case_id)
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM esp_support_case_attachments WHERE case_id=? AND id=?",
                (case_id, attachment_id),
            ).fetchone()
        if not row:
            raise KeyError("Support attachment not found")
        if role == "creator" and row["visibility"] != "creator":
            raise PermissionError("Support attachment is internal")
        path = Path(row["storage_path"])
        if not path.is_file():
            raise FileNotFoundError("Support attachment file is unavailable")
        return path, self._attachment_projection(row)

    def add_relation(self, case_id: str, actor_user_id: str, body: CaseRelationCreate) -> dict:
        case, role = self.authorize(actor_user_id, case_id, staff_only=True)
        if role == "creator":
            raise PermissionError("Support relationships are staff-managed")
        related_case_id = (body.related_case_id or "").strip() or None
        resource_type = _clean(body.resource_type, 80)
        resource_id = _clean(body.resource_id, 180)
        if body.relation_type in {"related", "duplicate_of", "supersedes"}:
            if not related_case_id:
                raise ValueError("A related case is required for this relation type")
            if related_case_id == case_id:
                raise ValueError("A support case cannot relate to itself")
            with self._connect() as con:
                related = self._case_row(con, related_case_id)
            # Non-owner staff may only connect cases belonging to the same creator. This avoids
            # accidentally exposing cross-member support history through a relation.
            if role != "owner" and related["user_id"] != case["user_id"]:
                raise PermissionError("Agents may link support cases only for the same assigned creator")
            resource_type = ""
            resource_id = ""
        else:
            if not resource_type or not resource_id:
                raise ValueError("Related-record links require resource_type and resource_id")
            related_case_id = None
        action = f"relation_{body.relation_type}"
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = self._idempotent_result(con, case_id, actor_user_id, action, body.idempotency_key)
            if existing is not None:
                return existing
            next_revision = self._advance_revision(con, case_id, body.expected_revision)
            relation_id = uuid4().hex
            con.execute(
                """INSERT INTO esp_support_case_relations
                   (id,case_id,relation_type,related_case_id,resource_type,resource_id,label,visibility,actor_user_id,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    relation_id,
                    case_id,
                    body.relation_type,
                    related_case_id,
                    resource_type,
                    resource_id,
                    _clean(body.label, 240),
                    body.visibility,
                    actor_user_id,
                    _now(),
                ),
            )
            result = {"relation_id": relation_id, "revision": next_revision, "relation_type": body.relation_type}
            self._save_idempotent_result(con, case_id, actor_user_id, action, body.idempotency_key, result)
        self.audit.append(
            actor=actor_user_id,
            action="support.case_relation_added",
            subject_user_id=case_id,
            details={"relation_id": result["relation_id"], "relation_type": body.relation_type},
        )
        return result

    def assign(self, case_id: str, actor_user_id: str, body: CaseAssignmentCreate) -> dict:
        case, role = self.authorize(actor_user_id, case_id, staff_only=True)
        if role == "creator":
            raise PermissionError("Support assignment is staff-managed")
        _membership, assignee_role = self._membership_role(body.assignee_user_id)
        if assignee_role not in {"agent", "both", "owner"}:
            raise ValueError("Assignee must have active ESP Agent or Owner access")
        if assignee_role != "owner":
            with self._connect() as con:
                if not self._active_assignment(con, body.assignee_user_id, case["user_id"]):
                    raise ValueError("Assignee must have an active assignment to this creator")
        action = "assignment"
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = self._idempotent_result(con, case_id, actor_user_id, action, body.idempotency_key)
            if existing is not None:
                return existing
            next_revision = self._advance_revision(con, case_id, body.expected_revision)
            event_id = uuid4().hex
            details = {"assignee_user_id": body.assignee_user_id, "reason": _clean(body.reason, 1000)}
            con.execute(
                """INSERT INTO esp_support_case_events(id,case_id,actor_user_id,event_type,details_json,visibility,created_at)
                   VALUES (?,?,?,?,?,'internal',?)""",
                (event_id, case_id, actor_user_id, "assigned", json.dumps(details, sort_keys=True), _now()),
            )
            # Preserve compatibility with the canonical field while retaining structured history.
            con.execute(
                "UPDATE esp_support_cases SET assigned_owner=?,updated_at=? WHERE id=?",
                (body.assignee_user_id, _now(), case_id),
            )
            result = {"event_id": event_id, "revision": next_revision, "assignee_user_id": body.assignee_user_id}
            self._save_idempotent_result(con, case_id, actor_user_id, action, body.idempotency_key, result)
        self.audit.append(
            actor=actor_user_id,
            action="support.case_assigned",
            subject_user_id=case_id,
            details={"assignee_user_id": body.assignee_user_id, "event_id": result["event_id"]},
        )
        return result

    def record_escalation(self, case_id: str, actor_user_id: str, body: CaseEscalationEventCreate) -> dict:
        _case, role = self.authorize(actor_user_id, case_id, staff_only=True)
        if role == "creator":
            raise PermissionError("Support escalation is staff-managed")
        action = "escalation"
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = self._idempotent_result(con, case_id, actor_user_id, action, body.idempotency_key)
            if existing is not None:
                return existing
            next_revision = self._advance_revision(con, case_id, body.expected_revision)
            event_id = uuid4().hex
            details = {"target": _clean(body.target, 80), "reason": _clean(body.reason, 3000)}
            con.execute(
                """INSERT INTO esp_support_case_events(id,case_id,actor_user_id,event_type,details_json,visibility,created_at)
                   VALUES (?,?,?,?,?,'internal',?)""",
                (event_id, case_id, actor_user_id, "escalated", json.dumps(details, sort_keys=True), _now()),
            )
            result = {"event_id": event_id, "revision": next_revision, "target": details["target"]}
            self._save_idempotent_result(con, case_id, actor_user_id, action, body.idempotency_key, result)
        self.audit.append(
            actor=actor_user_id,
            action="support.case_escalation_recorded",
            subject_user_id=case_id,
            details={"event_id": result["event_id"], "target": result["target"]},
        )
        return result


casework = SupportCaseworkStore()


def _member_id(request: Request) -> str:
    member, _membership = require_esp_hub_member(request)
    return member.user_id


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    if isinstance(exc, FileExistsError):
        return HTTPException(409, str(exc))
    if isinstance(exc, RuntimeError) and str(exc).startswith("revision_conflict:"):
        current = str(exc).split(":", 1)[1]
        return HTTPException(409, {"code": "revision_conflict", "current_revision": int(current)})
    if isinstance(exc, FileNotFoundError):
        return HTTPException(410, str(exc))
    return HTTPException(400, str(exc))


@router.get("/command-center/api/support/cases/{case_id}/casework")
def support_casework(case_id: str, request: Request):
    try:
        return casework.project(case_id, _member_id(request))
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/command-center/api/support/cases/{case_id}/messages")
def add_support_message(case_id: str, body: CaseMessageCreate, request: Request):
    actor = _member_id(request)
    try:
        result = casework.add_message(case_id, actor, body)
        return {"result": result, "casework": casework.project(case_id, actor)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/command-center/api/support/cases/{case_id}/attachments")
async def add_support_attachment(
    case_id: str,
    request: Request,
    attachment: UploadFile = File(...),
    visibility: Visibility = Form("creator"),
    expected_revision: int = Form(...),
    idempotency_key: str = Form(...),
):
    actor = _member_id(request)
    content = await attachment.read(_MAX_ATTACHMENT_BYTES + 1)
    try:
        result = casework.add_attachment(
            case_id,
            actor,
            filename=attachment.filename or "support-evidence",
            content_type=attachment.content_type or "",
            content=content,
            visibility=visibility,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )
        return {"result": result, "casework": casework.project(case_id, actor)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/command-center/api/support/cases/{case_id}/attachments/{attachment_id}/download")
def download_support_attachment(case_id: str, attachment_id: str, request: Request):
    actor = _member_id(request)
    try:
        path, metadata = casework.attachment_file(case_id, attachment_id, actor)
    except Exception as exc:
        raise _http_error(exc) from exc
    return FileResponse(
        path,
        media_type=metadata.get("content_type") or "application/octet-stream",
        filename=metadata.get("original_name") or "support-evidence",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/command-center/api/support/cases/{case_id}/relations")
def add_support_relation(case_id: str, body: CaseRelationCreate, request: Request):
    actor = _member_id(request)
    try:
        result = casework.add_relation(case_id, actor, body)
        return {"result": result, "casework": casework.project(case_id, actor)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/command-center/api/support/cases/{case_id}/assignment")
def assign_support_case(case_id: str, body: CaseAssignmentCreate, request: Request):
    actor = _member_id(request)
    try:
        result = casework.assign(case_id, actor, body)
        return {"result": result, "casework": casework.project(case_id, actor)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/command-center/api/support/cases/{case_id}/escalation-history")
def record_support_escalation(case_id: str, body: CaseEscalationEventCreate, request: Request):
    actor = _member_id(request)
    try:
        result = casework.record_escalation(case_id, actor, body)
        return {"result": result, "casework": casework.project(case_id, actor)}
    except Exception as exc:
        raise _http_error(exc) from exc


__all__ = [
    "CaseAssignmentCreate",
    "CaseEscalationEventCreate",
    "CaseMessageCreate",
    "CaseRelationCreate",
    "SupportCaseworkStore",
    "casework",
    "router",
]
