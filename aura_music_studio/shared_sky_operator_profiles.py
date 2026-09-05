from __future__ import annotations

import json
import sqlite3
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from .esp_niche import require_esp_hub_member
from .shared_sky_control_room import utc_now, validate_no_secrets
from .shared_sky_streaming_studios import shared_sky

router = APIRouter(tags=["Shared Sky Operator Profiles"])

OperatorCommand = Literal[
    "cut",
    "transition",
    "undo",
    "redo",
    "scene_next",
    "scene_previous",
    "marker_highlight",
]

_ALLOWED_KEYS = {
    "ENTER",
    "SPACE",
    "ARROWUP",
    "ARROWDOWN",
    "ARROWLEFT",
    "ARROWRIGHT",
    "HOME",
    "END",
    "PAGEUP",
    "PAGEDOWN",
    "F1",
    "F2",
    "F3",
    "F4",
    "F5",
    "F6",
    "F7",
    "F8",
    "F9",
    "F10",
    "F11",
    "F12",
}
_RESERVED = {
    "CTRL+R",
    "CTRL+W",
    "CTRL+T",
    "CTRL+L",
    "CTRL+N",
    "META+R",
    "META+W",
    "META+T",
    "META+L",
    "META+N",
    "META+Q",
    "ALT+F4",
}
_PROGRAMME_COMMANDS = {"cut", "transition"}
_FORBIDDEN_MACRO_PREFIXES = ("transport_", "recording_", "participant_", "destination_")


def normalize_shortcut(value: str) -> str:
    raw = [part.strip() for part in str(value or "").split("+") if part.strip()]
    if not raw or len(raw) > 4:
        raise ValueError("Hotkey must contain a key and at most three modifiers")
    modifiers: list[str] = []
    key = ""
    aliases = {"CONTROL": "CTRL", "CMD": "META", "COMMAND": "META", "OPTION": "ALT"}
    for item in raw:
        token = aliases.get(item.upper(), item.upper())
        if token in {"CTRL", "META", "ALT", "SHIFT"}:
            if token not in modifiers:
                modifiers.append(token)
            continue
        if key:
            raise ValueError("Hotkey may contain only one non-modifier key")
        key = token
    if not key:
        raise ValueError("Hotkey requires a non-modifier key")
    if not (len(key) == 1 and key.isalnum()) and key not in _ALLOWED_KEYS:
        raise ValueError("Unsupported operator hotkey key")
    if not modifiers and key not in _ALLOWED_KEYS:
        raise ValueError("Letter/number hotkeys require a modifier")
    ordered = [name for name in ("CTRL", "META", "ALT", "SHIFT") if name in modifiers]
    result = "+".join([*ordered, key])
    if result in _RESERVED:
        raise ValueError("Reserved browser/system shortcut cannot be assigned")
    return result


class OperatorMacro(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    commands: list[OperatorCommand] = Field(min_length=1, max_length=8)
    confirm_programme: bool = False

    @model_validator(mode="after")
    def validate_confirmation(self):
        if any(command in _PROGRAMME_COMMANDS for command in self.commands) and not self.confirm_programme:
            raise ValueError("Macros containing CUT/TRANSITION must require confirmation on every run")
        return self


class OperatorProfileUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    hotkeys: dict[str, OperatorCommand] = Field(default_factory=dict)
    macros: list[OperatorMacro] = Field(default_factory=list, max_length=16)
    expected_version: int | None = Field(default=None, ge=1)
    activate: bool = False

    @model_validator(mode="after")
    def validate_profile(self):
        if len(self.hotkeys) > 24:
            raise ValueError("Operator profiles support at most 24 hotkeys")
        normalized: dict[str, OperatorCommand] = {}
        for shortcut, command in self.hotkeys.items():
            key = normalize_shortcut(shortcut)
            if key in normalized:
                raise ValueError("Operator profile contains duplicate hotkeys")
            normalized[key] = command
        self.hotkeys = normalized
        names: set[str] = set()
        for macro in self.macros:
            clean = " ".join(macro.name.split()).casefold()
            if clean in names:
                raise ValueError("Macro names must be unique inside a profile")
            names.add(clean)
            for command in macro.commands:
                if str(command).startswith(_FORBIDDEN_MACRO_PREFIXES):
                    raise ValueError("Transport/recording/participant/destination mutations cannot be macro commands")
        return self


class OperatorProfileRepository:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        self._schema()

    def connect(self):
        con = sqlite3.connect(self.db_path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _schema(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS shared_sky_operator_profiles (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    hotkeys_json TEXT NOT NULL DEFAULT '{}',
                    macros_json TEXT NOT NULL DEFAULT '[]',
                    is_active INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id,project_id,name),
                    FOREIGN KEY(project_id) REFERENCES shared_sky_projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shared_sky_operator_profiles_project
                ON shared_sky_operator_profiles(user_id,project_id,is_active DESC,updated_at DESC);
                """
            )

    def _owned_project(self, user_id: str, project_id: str) -> None:
        shared_sky.project(user_id, project_id)

    @staticmethod
    def _public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["hotkeys"] = json.loads(item.pop("hotkeys_json") or "{}")
        item["macros"] = json.loads(item.pop("macros_json") or "[]")
        item["is_active"] = bool(item["is_active"])
        item["macro_execution"] = {
            "authority": "explicit_operator_only",
            "transport_commands_allowed": False,
            "recording_commands_allowed": False,
            "participant_commands_allowed": False,
            "programme_confirmation_required": True,
        }
        return item

    def list(self, user_id: str, project_id: str) -> list[dict[str, Any]]:
        self._owned_project(user_id, project_id)
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM shared_sky_operator_profiles WHERE user_id=? AND project_id=? "
                "ORDER BY is_active DESC,updated_at DESC,id",
                (user_id, project_id),
            ).fetchall()
        return [self._public(row) for row in rows]

    def get(self, user_id: str, project_id: str, profile_id: str) -> dict[str, Any]:
        self._owned_project(user_id, project_id)
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM shared_sky_operator_profiles WHERE id=? AND user_id=? AND project_id=?",
                (profile_id, user_id, project_id),
            ).fetchone()
        if not row:
            raise KeyError(profile_id)
        return self._public(row)

    def upsert(
        self,
        user_id: str,
        project_id: str,
        body: OperatorProfileUpsert,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        self._owned_project(user_id, project_id)
        payload = {
            "hotkeys": body.hotkeys,
            "macros": [macro.model_dump() for macro in body.macros],
        }
        validate_no_secrets(payload)
        stamp = utc_now()
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if profile_id:
                row = con.execute(
                    "SELECT version FROM shared_sky_operator_profiles WHERE id=? AND user_id=? AND project_id=?",
                    (profile_id, user_id, project_id),
                ).fetchone()
                if not row:
                    raise KeyError(profile_id)
                if body.expected_version is None or int(row["version"]) != body.expected_version:
                    raise ValueError("Operator profile version conflict")
                cur = con.execute(
                    "UPDATE shared_sky_operator_profiles SET name=?,hotkeys_json=?,macros_json=?,"
                    "version=version+1,updated_at=? WHERE id=? AND user_id=? AND project_id=? AND version=?",
                    (
                        " ".join(body.name.split()),
                        json.dumps(body.hotkeys, sort_keys=True),
                        json.dumps(payload["macros"], sort_keys=True),
                        stamp,
                        profile_id,
                        user_id,
                        project_id,
                        body.expected_version,
                    ),
                )
                if cur.rowcount != 1:
                    raise ValueError("Operator profile changed concurrently")
            else:
                profile_id = f"operator_{uuid4().hex}"
                con.execute(
                    "INSERT INTO shared_sky_operator_profiles "
                    "(id,user_id,project_id,name,hotkeys_json,macros_json,is_active,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,0,?,?)",
                    (
                        profile_id,
                        user_id,
                        project_id,
                        " ".join(body.name.split()),
                        json.dumps(body.hotkeys, sort_keys=True),
                        json.dumps(payload["macros"], sort_keys=True),
                        stamp,
                        stamp,
                    ),
                )
            if body.activate:
                con.execute(
                    "UPDATE shared_sky_operator_profiles SET is_active=CASE WHEN id=? THEN 1 ELSE 0 END,updated_at=? "
                    "WHERE user_id=? AND project_id=?",
                    (profile_id, stamp, user_id, project_id),
                )
        return self.get(user_id, project_id, str(profile_id))

    def activate(self, user_id: str, project_id: str, profile_id: str) -> dict[str, Any]:
        self.get(user_id, project_id, profile_id)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "UPDATE shared_sky_operator_profiles SET is_active=CASE WHEN id=? THEN 1 ELSE 0 END,updated_at=? "
                "WHERE user_id=? AND project_id=?",
                (profile_id, utc_now(), user_id, project_id),
            )
        return self.get(user_id, project_id, profile_id)

    def delete(self, user_id: str, project_id: str, profile_id: str) -> None:
        current = self.get(user_id, project_id, profile_id)
        if current["is_active"]:
            raise ValueError("Activate another operator profile before deleting the active profile")
        with self.connect() as con:
            con.execute(
                "DELETE FROM shared_sky_operator_profiles WHERE id=? AND user_id=? AND project_id=?",
                (profile_id, user_id, project_id),
            )


operator_profiles = OperatorProfileRepository(shared_sky.db_path)


def _member(request: Request):
    return require_esp_hub_member(request)


def _raise(exc: Exception) -> None:
    if isinstance(exc, KeyError):
        raise HTTPException(404, "Shared Sky operator profile not found") from exc
    if isinstance(exc, sqlite3.IntegrityError):
        message = str(exc).lower()
        if "unique" in message and "shared_sky_operator_profiles" in message:
            raise HTTPException(409, "An operator profile with that name already exists for this project") from exc
        raise HTTPException(409, "Operator profile constraint conflict") from exc
    if isinstance(exc, ValueError):
        status = 409 if "version" in str(exc).lower() or "concurrently" in str(exc).lower() else 400
        raise HTTPException(status, str(exc)) from exc
    raise HTTPException(500, "Shared Sky operator profile operation failed") from exc


@router.get("/shared-sky/studio/api/projects/{project_id}/operator-profiles")
def list_operator_profiles(project_id: str, request: Request):
    member, _ = _member(request)
    try:
        return {"profiles": operator_profiles.list(member.user_id, project_id)}
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/projects/{project_id}/operator-profiles")
def create_operator_profile(project_id: str, body: OperatorProfileUpsert, request: Request):
    member, _ = _member(request)
    try:
        return {"profile": operator_profiles.upsert(member.user_id, project_id, body)}
    except Exception as exc:
        _raise(exc)


@router.put("/shared-sky/studio/api/projects/{project_id}/operator-profiles/{profile_id}")
def update_operator_profile(
    project_id: str, profile_id: str, body: OperatorProfileUpsert, request: Request
):
    member, _ = _member(request)
    try:
        return {
            "profile": operator_profiles.upsert(member.user_id, project_id, body, profile_id=profile_id)
        }
    except Exception as exc:
        _raise(exc)


@router.post("/shared-sky/studio/api/projects/{project_id}/operator-profiles/{profile_id}/activate")
def activate_operator_profile(project_id: str, profile_id: str, request: Request):
    member, _ = _member(request)
    try:
        return {"profile": operator_profiles.activate(member.user_id, project_id, profile_id)}
    except Exception as exc:
        _raise(exc)


@router.delete("/shared-sky/studio/api/projects/{project_id}/operator-profiles/{profile_id}")
def delete_operator_profile(project_id: str, profile_id: str, request: Request):
    member, _ = _member(request)
    try:
        operator_profiles.delete(member.user_id, project_id, profile_id)
        return {"deleted": True, "profile_id": profile_id}
    except Exception as exc:
        _raise(exc)


def install_shared_sky_operator_profiles(app: Any) -> None:
    existing = {
        (getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", set()) or set())))
        for route in app.router.routes
    }
    for route in router.routes:
        signature = (
            getattr(route, "path", ""),
            tuple(sorted(getattr(route, "methods", set()) or set())),
        )
        if signature not in existing:
            app.router.routes.append(route)
            existing.add(signature)
    app.openapi_schema = None


__all__ = [
    "OperatorMacro",
    "OperatorProfileRepository",
    "OperatorProfileUpsert",
    "install_shared_sky_operator_profiles",
    "normalize_shortcut",
    "operator_profiles",
    "router",
]
