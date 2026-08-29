from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .owner_auth import owner_authorized
from .owner_identity import owner_actor

PACKAGE_VERSION = "esp-privacy-fulfilment/v1"
SUPPORTED_REQUEST_TYPES = {"access", "portability"}
_REFERENCE_PREFIX = "privacy-package:"

owner_router = APIRouter(prefix="/owner/privacy", tags=["Owner Privacy Fulfilment"])


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class PrivacyFulfilmentStore:
    """Prepare and deliver bounded privacy access/portability packages.

    The database stores preparation and delivery evidence only. Personal-data package
    contents are rebuilt from a strict allowlist at delivery time and are never copied
    into the fulfilment metadata table. This is deliberately not a raw database export.
    """

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

    @staticmethod
    def _ensure_column(con: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
        existing = {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS privacy_fulfilment_packages (
                    request_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    preparation_id TEXT NOT NULL UNIQUE,
                    package_version TEXT NOT NULL,
                    prepared_at TEXT NOT NULL,
                    prepared_by TEXT NOT NULL,
                    preparation_hash TEXT NOT NULL DEFAULT '',
                    delivered_at TEXT,
                    delivery_digest TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_privacy_fulfilment_user
                    ON privacy_fulfilment_packages(user_id, prepared_at DESC);
                """
            )
            self._ensure_column(
                con,
                "privacy_fulfilment_packages",
                "preparation_hash",
                "TEXT NOT NULL DEFAULT ''",
            )

    @staticmethod
    def _table_exists(con: sqlite3.Connection, table: str) -> bool:
        return bool(
            con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
        )

    @staticmethod
    def _preparation_payload(
        *,
        request_id: str,
        user_id: str,
        preparation_id: str,
        package_version: str,
        prepared_at: str,
        prepared_by: str,
    ) -> dict:
        return {
            "request_id": request_id,
            "user_id": user_id,
            "preparation_id": preparation_id,
            "package_version": package_version,
            "prepared_at": prepared_at,
            "prepared_by": prepared_by,
        }

    @classmethod
    def _preparation_hash(cls, **kwargs: str) -> str:
        payload = cls._preparation_payload(**kwargs)
        return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()

    @classmethod
    def _preparation_valid(cls, row: sqlite3.Row) -> bool:
        expected = cls._preparation_hash(
            request_id=str(row["request_id"]),
            user_id=str(row["user_id"]),
            preparation_id=str(row["preparation_id"]),
            package_version=str(row["package_version"]),
            prepared_at=str(row["prepared_at"]),
            prepared_by=str(row["prepared_by"]),
        )
        return bool(row["preparation_hash"] and str(row["preparation_hash"]) == expected)

    @classmethod
    def _metadata(cls, row: sqlite3.Row | None) -> dict | None:
        if not row:
            return None
        item = dict(row)
        item["fulfilment_reference"] = _REFERENCE_PREFIX + str(item["preparation_id"])
        item["preparation_evidence_valid"] = cls._preparation_valid(row)
        item["package_contents_persisted"] = False
        return item

    def prepared_reference(self, request_id: str) -> str:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM privacy_fulfilment_packages WHERE request_id=?",
                (request_id,),
            ).fetchone()
        if not row or not self._preparation_valid(row):
            return ""
        return _REFERENCE_PREFIX + str(row["preparation_id"])

    def prepare(self, request_id: str, *, prepared_by: str) -> dict:
        actor = str(prepared_by or "ESP Owner").strip()
        if not actor or len(actor) > 120:
            raise ValueError("Disclosure reviewer attribution is required and limited to 120 characters")

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            request_row = con.execute(
                "SELECT id,user_id,request_type,status FROM privacy_rights_requests WHERE id=?",
                (request_id,),
            ).fetchone()
            if not request_row:
                raise KeyError("Privacy request not found")
            if str(request_row["request_type"]) not in SUPPORTED_REQUEST_TYPES:
                raise ValueError("Structured disclosure preparation is available only for access or portability requests")
            if str(request_row["status"]) != "ready_for_fulfilment":
                raise ValueError("Privacy request must be ready for fulfilment before disclosure preparation")

            control = con.execute(
                "SELECT * FROM privacy_case_controls WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if not control:
                raise ValueError("Owner privacy case review must be initialized before disclosure preparation")
            if str(control["identity_status"] or "") != "verified":
                raise ValueError("Verified identity is required before disclosure preparation")
            if bool(control["legal_hold"]) or bool(control["retention_hold"]):
                raise ValueError("Active legal or retention hold blocks disclosure preparation")
            if not str(control["jurisdiction"] or "").strip() or not str(control["legal_basis"] or "").strip():
                raise ValueError("Jurisdiction and legal basis review are required before disclosure preparation")

            existing = con.execute(
                "SELECT * FROM privacy_fulfilment_packages WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if existing:
                if not self._preparation_valid(existing):
                    raise RuntimeError("Stored privacy disclosure preparation evidence failed integrity verification")
                if existing["delivered_at"]:
                    raise ValueError("This privacy request already has delivered fulfilment evidence")
                item = self._metadata(existing) or {}
                item["reused_preparation"] = True
                return item

            now = _iso()
            preparation_id = uuid4().hex
            preparation_hash = self._preparation_hash(
                request_id=request_id,
                user_id=str(request_row["user_id"]),
                preparation_id=preparation_id,
                package_version=PACKAGE_VERSION,
                prepared_at=now,
                prepared_by=actor,
            )
            con.execute(
                """INSERT INTO privacy_fulfilment_packages
                   (request_id,user_id,preparation_id,package_version,prepared_at,prepared_by,
                    preparation_hash,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    request_id,
                    str(request_row["user_id"]),
                    preparation_id,
                    PACKAGE_VERSION,
                    now,
                    actor,
                    preparation_hash,
                    now,
                ),
            )
            row = con.execute(
                "SELECT * FROM privacy_fulfilment_packages WHERE request_id=?",
                (request_id,),
            ).fetchone()
            item = self._metadata(row) or {}
            item["reused_preparation"] = False
            return item

    def _build_package(
        self,
        con: sqlite3.Connection,
        *,
        request_row: sqlite3.Row,
        prepared_at: str,
    ) -> dict:
        user_id = str(request_row["user_id"])

        account = None
        if self._table_exists(con, "users"):
            row = con.execute(
                """SELECT id,email,display_name,status,plan_id,requested_plan_id,billing_status,
                          created_at,approved_at,rejected_at,disabled_at
                   FROM users WHERE id=?""",
                (user_id,),
            ).fetchone()
            account = dict(row) if row else None

        membership_requests: list[dict] = []
        if self._table_exists(con, "membership_requests"):
            rows = con.execute(
                """SELECT id,requested_plan_id,status,created_at,expires_at,decided_at
                   FROM membership_requests WHERE user_id=? ORDER BY created_at ASC,id ASC""",
                (user_id,),
            ).fetchall()
            membership_requests = [dict(row) for row in rows]

        privacy_requests = [
            dict(row)
            for row in con.execute(
                """SELECT id,request_type,status,locale,detail,submitted_at
                   FROM privacy_rights_requests WHERE user_id=? ORDER BY submitted_at ASC,id ASC""",
                (user_id,),
            ).fetchall()
        ]

        policy_evidence: list[dict] = []
        if self._table_exists(con, "privacy_policy_evidence"):
            policy_evidence = [
                dict(row)
                for row in con.execute(
                    """SELECT id,policy_key,policy_version,decision,locale,recorded_at,source
                       FROM privacy_policy_evidence WHERE user_id=? ORDER BY recorded_at ASC,id ASC""",
                    (user_id,),
                ).fetchall()
            ]

        return {
            "schema": PACKAGE_VERSION,
            "subject": {"user_id": user_id},
            "request": {
                "id": str(request_row["id"]),
                "request_type": str(request_row["request_type"]),
                "locale": str(request_row["locale"]),
                "submitted_at": str(request_row["submitted_at"]),
            },
            "prepared_at": prepared_at,
            "sections": {
                "account": account,
                "membership_requests": membership_requests,
                "privacy_requests": privacy_requests,
                "policy_evidence": policy_evidence,
            },
            "scope": {
                "structured_machine_readable_json": True,
                "raw_database_export": False,
                "package_contents_persisted_by_fulfilment_store": False,
                "included_sections": [
                    "allowlisted_account_identity_and_membership_status",
                    "allowlisted_membership_request_history",
                    "member_privacy_request_history",
                    "member_policy_decision_evidence",
                ],
                "excluded_categories": [
                    "password_hashes_and_password_salts",
                    "session_tokens_and_session_hashes",
                    "login_throttle_security_state",
                    "owner_only_reviewer_and_internal_decision_attribution",
                    "provider_secrets_private_keys_and_authentication_credentials",
                    "raw_internal_database_tables",
                    "creative_binary_assets_and_unbounded_usage_metadata",
                ],
            },
        }

    def deliver(self, request_id: str, *, user_id: str) -> dict:
        user_id = str(user_id or "").strip()
        if not user_id:
            raise ValueError("Authenticated member id is required")

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            request_row = con.execute(
                """SELECT id,user_id,request_type,status,locale,submitted_at
                   FROM privacy_rights_requests WHERE id=? AND user_id=?""",
                (request_id, user_id),
            ).fetchone()
            if not request_row:
                raise KeyError("Privacy fulfilment package not found")
            if str(request_row["request_type"]) not in SUPPORTED_REQUEST_TYPES:
                raise ValueError("This privacy request does not produce a structured disclosure package")
            if str(request_row["status"]) != "fulfilled":
                raise ValueError("Privacy disclosure is not yet fulfilled")

            control = con.execute(
                "SELECT fulfilment_reference FROM privacy_case_controls WHERE request_id=?",
                (request_id,),
            ).fetchone()
            prepared = con.execute(
                "SELECT * FROM privacy_fulfilment_packages WHERE request_id=? AND user_id=?",
                (request_id, user_id),
            ).fetchone()
            if not control or not prepared:
                raise ValueError("Prepared privacy disclosure evidence is unavailable")
            if not self._preparation_valid(prepared):
                raise RuntimeError("Prepared privacy disclosure evidence failed integrity verification")

            expected_reference = _REFERENCE_PREFIX + str(prepared["preparation_id"])
            if str(control["fulfilment_reference"] or "") != expected_reference:
                raise ValueError("Fulfilment evidence does not match the prepared disclosure package")

            package = self._build_package(
                con,
                request_row=request_row,
                prepared_at=str(prepared["prepared_at"]),
            )
            digest = hashlib.sha256(_canonical_json(package).encode("utf-8")).hexdigest()
            prior_digest = str(prepared["delivery_digest"] or "")
            if prior_digest and prior_digest != digest:
                raise RuntimeError(
                    "The allowlisted privacy snapshot changed after delivery; owner review and a new fulfilment operation are required"
                )

            delivered_at = str(prepared["delivered_at"] or "") or _iso()
            if not prior_digest:
                con.execute(
                    """UPDATE privacy_fulfilment_packages
                       SET delivered_at=?,delivery_digest=?,updated_at=? WHERE request_id=?""",
                    (delivered_at, digest, delivered_at, request_id),
                )

        return {
            "package": package,
            "evidence": {
                "fulfilment_reference": expected_reference,
                "package_version": PACKAGE_VERSION,
                "preparation_hash": str(prepared["preparation_hash"]),
                "sha256": digest,
                "delivered_at": delivered_at,
                "package_contents_persisted": False,
            },
            "grants_esp_role_or_permission": False,
            "changes_billing_or_membership": False,
        }


def _require_owner(request: Request) -> None:
    if not owner_authorized(request):
        raise HTTPException(403, "Owner authorization required")


@owner_router.post("/cases/{request_id}/prepare-disclosure", include_in_schema=False)
def owner_prepare_privacy_disclosure(request_id: str, request: Request):
    _require_owner(request)
    try:
        prepared = PrivacyFulfilmentStore().prepare(request_id, prepared_by=owner_actor())
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return JSONResponse(
        {
            "preparation": prepared,
            "automatic_data_action_taken": False,
            "package_contents_persisted": False,
            "next_step": "Record the returned fulfilment_reference through the existing owner privacy case transition after final review. Member delivery remains unavailable until the case is fulfilled with that exact reference.",
            "grants_esp_role_or_permission": False,
            "changes_billing_or_membership": False,
        },
        headers={"Cache-Control": "private, no-store"},
    )


__all__ = [
    "PACKAGE_VERSION",
    "SUPPORTED_REQUEST_TYPES",
    "PrivacyFulfilmentStore",
    "owner_router",
]
