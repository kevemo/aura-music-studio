from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .esp_command_center import EspStore, esp
from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["ESP Creator Configuration Vault"])

VerificationOutcome = Literal["known_good", "partial", "failed"]

_SECRET_PATTERNS = [
    re.compile(r"(?i)password\s*[:=]"),
    re.compile(r"(?i)stream[ _-]?key\s*[:=]"),
    re.compile(r"(?i)(oauth|access|refresh)[ _-]?token\s*[:=]"),
    re.compile(r"(?i)(api|secret|recovery)[ _-]?(key|code)\s*[:=]"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_owner(membership: dict) -> bool:
    return membership.get("status") == "owner" or str(membership.get("roles") or "").lower() == "owner"


def _clean(value: str, limit: int) -> str:
    text = (value or "").strip()
    if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
        raise ValueError("The Configuration Vault must not store passwords, stream keys, tokens, API keys or recovery secrets")
    return text[:limit]


class TechProfileCreate(BaseModel):
    consent_store_setup: bool = True
    region: str = Field(default="", max_length=80)
    device_make_model: str = Field(default="", max_length=200)
    device_class: str = Field(default="", max_length=100)
    operating_system: str = Field(default="", max_length=160)
    cpu: str = Field(default="", max_length=160)
    gpu: str = Field(default="", max_length=160)
    ram: str = Field(default="", max_length=80)
    network_type: str = Field(default="", max_length=120)
    isp: str = Field(default="", max_length=160)
    microphone: str = Field(default="", max_length=240)
    audio_interface_mixer: str = Field(default="", max_length=240)
    camera: str = Field(default="", max_length=240)
    capture_device: str = Field(default="", max_length=240)
    lighting: str = Field(default="", max_length=240)
    monitoring: str = Field(default="", max_length=240)
    live_software: str = Field(default="", max_length=180)
    live_software_version: str = Field(default="", max_length=100)
    encoder: str = Field(default="", max_length=160)
    output_resolution: str = Field(default="", max_length=80)
    fps: str = Field(default="", max_length=40)
    bitrate_range: str = Field(default="", max_length=80)
    audio_sample_rate: str = Field(default="", max_length=80)
    scene_notes: str = Field(default="", max_length=3000)
    change_reason: str = Field(default="", max_length=500)


class VerificationCreate(BaseModel):
    outcome: VerificationOutcome
    test_type: str = Field(default="broadcast_preflight", max_length=120)
    duration_minutes: int = Field(default=5, ge=1, le=1440)
    conditions: str = Field(default="", max_length=2000)
    note: str = Field(default="", max_length=2000)


class CreatorTechVaultStore:
    """Opt-in, versioned, non-secret creator technical configuration history."""

    def __init__(self, esp_store: EspStore | None = None):
        self.esp = esp_store or esp
        self.db_path = self.esp.db_path
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS esp_creator_tech_profile_versions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    version_no INTEGER NOT NULL,
                    config_json TEXT NOT NULL,
                    change_reason TEXT NOT NULL DEFAULT '',
                    consent_store_setup INTEGER NOT NULL DEFAULT 1,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    retired_at TEXT,
                    UNIQUE(user_id,version_no),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_creator_tech_profile_user
                    ON esp_creator_tech_profile_versions(user_id,version_no DESC);
                CREATE TABLE IF NOT EXISTS esp_creator_tech_verifications (
                    id TEXT PRIMARY KEY,
                    profile_version_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    test_type TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL,
                    conditions TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    verified_by TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    FOREIGN KEY(profile_version_id) REFERENCES esp_creator_tech_profile_versions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_creator_tech_verification_profile
                    ON esp_creator_tech_verifications(profile_version_id,verified_at DESC);
                CREATE TABLE IF NOT EXISTS esp_creator_tech_audit (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                """
            )

    def _audit(self, con: sqlite3.Connection, user_id: str, actor: str, action: str, metadata: dict | None = None) -> None:
        con.execute(
            "INSERT INTO esp_creator_tech_audit(id,user_id,actor,action,metadata_json,created_at) VALUES (?,?,?,?,?,?)",
            (uuid4().hex, user_id, actor[:160], action[:120], json.dumps(metadata or {}, sort_keys=True), _now()),
        )

    @staticmethod
    def _config(body: TechProfileCreate) -> dict:
        return {
            "region": _clean(body.region, 80),
            "device_make_model": _clean(body.device_make_model, 200),
            "device_class": _clean(body.device_class, 100),
            "operating_system": _clean(body.operating_system, 160),
            "cpu": _clean(body.cpu, 160),
            "gpu": _clean(body.gpu, 160),
            "ram": _clean(body.ram, 80),
            "network_type": _clean(body.network_type, 120),
            "isp": _clean(body.isp, 160),
            "microphone": _clean(body.microphone, 240),
            "audio_interface_mixer": _clean(body.audio_interface_mixer, 240),
            "camera": _clean(body.camera, 240),
            "capture_device": _clean(body.capture_device, 240),
            "lighting": _clean(body.lighting, 240),
            "monitoring": _clean(body.monitoring, 240),
            "live_software": _clean(body.live_software, 180),
            "live_software_version": _clean(body.live_software_version, 100),
            "encoder": _clean(body.encoder, 160),
            "output_resolution": _clean(body.output_resolution, 80),
            "fps": _clean(body.fps, 40),
            "bitrate_range": _clean(body.bitrate_range, 80),
            "audio_sample_rate": _clean(body.audio_sample_rate, 80),
            "scene_notes": _clean(body.scene_notes, 3000),
        }

    def create_version(self, user_id: str, body: TechProfileCreate, *, actor: str) -> dict:
        if not body.consent_store_setup:
            raise ValueError("Configuration storage requires explicit creator consent")
        config = self._config(body)
        reason = _clean(body.change_reason, 500)
        now = _now()
        with self._connect() as con:
            current = con.execute(
                "SELECT id,version_no FROM esp_creator_tech_profile_versions WHERE user_id=? AND retired_at IS NULL ORDER BY version_no DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            version_no = (int(current["version_no"]) + 1) if current else 1
            if current:
                con.execute("UPDATE esp_creator_tech_profile_versions SET retired_at=? WHERE id=?", (now, current["id"]))
            version_id = uuid4().hex
            con.execute(
                """INSERT INTO esp_creator_tech_profile_versions
                (id,user_id,version_no,config_json,change_reason,consent_store_setup,created_by,created_at,retired_at)
                VALUES (?,?,?,?,?,1,?,?,NULL)""",
                (version_id, user_id, version_no, json.dumps(config, sort_keys=True), reason, actor[:160], now),
            )
            self._audit(con, user_id, actor, "tech_profile_version_created", {"profile_version_id": version_id, "version_no": version_no, "change_reason": reason})
        return self.get_version(version_id, user_id=user_id)

    def _row(self, version_id: str):
        with self._connect() as con:
            return con.execute("SELECT * FROM esp_creator_tech_profile_versions WHERE id=?", (version_id,)).fetchone()

    def get_version(self, version_id: str, *, user_id: str | None = None, owner: bool = False) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_creator_tech_profile_versions WHERE id=?", (version_id,)).fetchone()
            if not row:
                raise KeyError("Configuration version not found")
            if not owner and row["user_id"] != user_id:
                raise PermissionError("Configuration version is private to its creator and authorised ESP support")
            verifications = con.execute(
                "SELECT * FROM esp_creator_tech_verifications WHERE profile_version_id=? ORDER BY verified_at DESC",
                (version_id,),
            ).fetchall()
        item = dict(row)
        item["consent_store_setup"] = bool(item["consent_store_setup"])
        item["config"] = json.loads(item.pop("config_json") or "{}")
        item["verifications"] = [dict(value) for value in verifications]
        item["known_good"] = any(value["outcome"] == "known_good" for value in verifications)
        latest_good = next((dict(value) for value in verifications if value["outcome"] == "known_good"), None)
        item["known_good_test"] = latest_good
        return item

    def history(self, user_id: str, *, owner: bool = False, requester_user_id: str | None = None) -> list[dict]:
        if not owner and requester_user_id != user_id:
            raise PermissionError("Configuration history is private to its creator and authorised ESP support")
        with self._connect() as con:
            rows = con.execute(
                "SELECT id FROM esp_creator_tech_profile_versions WHERE user_id=? ORDER BY version_no DESC",
                (user_id,),
            ).fetchall()
        return [self.get_version(row["id"], user_id=requester_user_id, owner=owner) for row in rows]

    def current(self, user_id: str, *, owner: bool = False, requester_user_id: str | None = None) -> dict | None:
        if not owner and requester_user_id != user_id:
            raise PermissionError("Configuration profile is private to its creator and authorised ESP support")
        with self._connect() as con:
            row = con.execute(
                "SELECT id FROM esp_creator_tech_profile_versions WHERE user_id=? AND retired_at IS NULL ORDER BY version_no DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return self.get_version(row["id"], user_id=requester_user_id, owner=owner) if row else None

    def verify(self, version_id: str, body: VerificationCreate, *, actor: str) -> dict:
        row = self._row(version_id)
        if not row:
            raise KeyError("Configuration version not found")
        conditions = _clean(body.conditions, 2000)
        note = _clean(body.note, 2000)
        with self._connect() as con:
            verification_id = uuid4().hex
            con.execute(
                """INSERT INTO esp_creator_tech_verifications
                (id,profile_version_id,outcome,test_type,duration_minutes,conditions,note,verified_by,verified_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    verification_id,
                    version_id,
                    body.outcome,
                    _clean(body.test_type, 120),
                    body.duration_minutes,
                    conditions,
                    note,
                    actor[:160],
                    _now(),
                ),
            )
            self._audit(con, row["user_id"], actor, "tech_profile_verified", {"profile_version_id": version_id, "outcome": body.outcome, "verification_id": verification_id})
        return self.get_version(version_id, owner=True)

    def revoke_consent(self, user_id: str, *, actor: str) -> None:
        with self._connect() as con:
            count = con.execute("SELECT COUNT(*) AS n FROM esp_creator_tech_profile_versions WHERE user_id=?", (user_id,)).fetchone()["n"]
            con.execute("DELETE FROM esp_creator_tech_profile_versions WHERE user_id=?", (user_id,))
            self._audit(con, user_id, actor, "tech_profile_consent_revoked_and_deleted", {"deleted_versions": int(count)})


tech_vault = CreatorTechVaultStore()


@router.get("/command-center/api/tech-vault")
def member_tech_vault(request: Request):
    member, membership = require_esp_hub_member(request)
    return {
        "current": tech_vault.current(member.user_id, requester_user_id=member.user_id),
        "history": tech_vault.history(member.user_id, requester_user_id=member.user_id),
        "owner": _is_owner(membership),
        "secrets_stored": False,
        "consent_required": True,
    }


@router.post("/command-center/api/tech-vault/versions")
def member_create_tech_version(body: TechProfileCreate, request: Request):
    member, _membership = require_esp_hub_member(request)
    try:
        return {"profile": tech_vault.create_version(member.user_id, body, actor=member.user_id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/command-center/api/tech-vault")
def member_delete_tech_vault(request: Request):
    member, _membership = require_esp_hub_member(request)
    tech_vault.revoke_consent(member.user_id, actor=member.user_id)
    return {"deleted": True, "consent": False}


@router.get("/command-center/api/tech-vault/owner/{user_id}")
def owner_tech_history(user_id: str, request: Request):
    _member, membership = require_esp_hub_member(request)
    if not _is_owner(membership):
        raise HTTPException(403, "ESP Owner access is required")
    try:
        return {"current": tech_vault.current(user_id, owner=True), "history": tech_vault.history(user_id, owner=True)}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/command-center/api/tech-vault/owner/versions/{version_id}/verify")
def owner_verify_tech_version(version_id: str, body: VerificationCreate, request: Request):
    member, membership = require_esp_hub_member(request)
    if not _is_owner(membership):
        raise HTTPException(403, "ESP Owner access is required")
    try:
        return {"profile": tech_vault.verify(version_id, body, actor=member.user_id)}
    except KeyError as exc:
        raise HTTPException(404, "Configuration version not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


__all__ = ["router", "tech_vault", "CreatorTechVaultStore", "TechProfileCreate", "VerificationCreate"]
