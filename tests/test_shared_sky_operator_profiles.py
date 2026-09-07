from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from aura_music_studio.shared_sky_operator_profiles import (
    OperatorMacro,
    OperatorProfileRepository,
    OperatorProfileUpsert,
    _raise,
    install_shared_sky_operator_profiles,
    normalize_shortcut,
)


def _repo(tmp_path, monkeypatch):
    db = tmp_path / "operator-profiles.db"
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE shared_sky_projects (id TEXT PRIMARY KEY,user_id TEXT NOT NULL)"
        )
        con.execute(
            "INSERT INTO shared_sky_projects(id,user_id) VALUES('project-1','user-1')"
        )
    repo = OperatorProfileRepository(str(db))
    monkeypatch.setattr(repo, "_owned_project", lambda _user_id, _project_id: None)
    return repo


def test_normalize_shortcut_orders_modifiers_and_supports_aliases():
    assert normalize_shortcut("shift+ctrl+k") == "CTRL+SHIFT+K"
    assert normalize_shortcut("command+option+1") == "META+ALT+1"
    assert normalize_shortcut("F8") == "F8"


def test_normalize_shortcut_rejects_reserved_and_unsafe_shapes():
    for shortcut in ("ctrl+r", "alt+f4", "q", "ctrl+k+m", "ctrl+banana"):
        with pytest.raises(ValueError):
            normalize_shortcut(shortcut)


def test_programme_macro_requires_confirmation_every_run():
    with pytest.raises(ValidationError):
        OperatorMacro(name="Take", commands=["cut"])

    macro = OperatorMacro(name="Take", commands=["cut", "transition"], confirm_programme=True)
    assert macro.confirm_programme is True
    assert macro.commands == ["cut", "transition"]


def test_profile_normalizes_hotkeys_and_rejects_duplicates_after_normalization():
    body = OperatorProfileUpsert(
        name="Main",
        hotkeys={"shift+ctrl+k": "cut", "alt+u": "undo"},
        macros=[OperatorMacro(name="Recovery", commands=["undo", "redo"])],
    )
    assert body.hotkeys == {"CTRL+SHIFT+K": "cut", "ALT+U": "undo"}

    with pytest.raises(ValidationError):
        OperatorProfileUpsert(
            name="Duplicate",
            hotkeys={"ctrl+shift+k": "cut", "shift+ctrl+k": "undo"},
        )


def test_profile_literal_prevents_transport_recording_participant_and_destination_macros():
    for forbidden in (
        "transport_start",
        "transport_stop",
        "recording_start",
        "participant_remove",
        "destination_retry",
    ):
        with pytest.raises(ValidationError):
            OperatorMacro(name="Unsafe", commands=[forbidden])


def test_repository_create_activate_update_and_stale_version(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    first = repo.upsert(
        "user-1",
        "project-1",
        OperatorProfileUpsert(
            name="Main",
            hotkeys={"alt+c": "cut"},
            macros=[OperatorMacro(name="Recover", commands=["undo", "redo"])],
            activate=True,
        ),
    )
    assert first["version"] == 1
    assert first["is_active"] is True
    assert first["hotkeys"] == {"ALT+C": "cut"}
    assert first["macro_execution"]["transport_commands_allowed"] is False

    updated = repo.upsert(
        "user-1",
        "project-1",
        OperatorProfileUpsert(
            name="Main",
            hotkeys={"alt+c": "cut", "ctrl+shift+z": "redo"},
            macros=[],
            expected_version=1,
        ),
        profile_id=first["id"],
    )
    assert updated["version"] == 2
    assert updated["is_active"] is True

    with pytest.raises(ValueError, match="version conflict"):
        repo.upsert(
            "user-1",
            "project-1",
            OperatorProfileUpsert(name="Main", expected_version=1),
            profile_id=first["id"],
        )


def test_only_one_profile_active_and_active_profile_cannot_be_deleted(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    first = repo.upsert(
        "user-1", "project-1", OperatorProfileUpsert(name="A", activate=True)
    )
    second = repo.upsert(
        "user-1", "project-1", OperatorProfileUpsert(name="B", activate=True)
    )
    rows = repo.list("user-1", "project-1")
    assert sum(1 for row in rows if row["is_active"]) == 1
    assert repo.get("user-1", "project-1", second["id"])["is_active"] is True
    assert repo.get("user-1", "project-1", first["id"])["is_active"] is False

    with pytest.raises(ValueError, match="Activate another"):
        repo.delete("user-1", "project-1", second["id"])

    repo.activate("user-1", "project-1", first["id"])
    repo.delete("user-1", "project-1", second["id"])
    assert [row["id"] for row in repo.list("user-1", "project-1")] == [first["id"]]


def test_duplicate_profile_name_is_not_a_silent_second_record(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    repo.upsert("user-1", "project-1", OperatorProfileUpsert(name="Main"))
    with pytest.raises(sqlite3.IntegrityError):
        repo.upsert("user-1", "project-1", OperatorProfileUpsert(name="Main"))
    assert len(repo.list("user-1", "project-1")) == 1


def test_duplicate_profile_constraint_maps_to_safe_409_without_db_detail():
    raw = (
        "UNIQUE constraint failed: shared_sky_operator_profiles.user_id, "
        "shared_sky_operator_profiles.project_id, shared_sky_operator_profiles.name"
    )
    with pytest.raises(HTTPException) as raised:
        _raise(sqlite3.IntegrityError(raw))
    assert raised.value.status_code == 409
    assert raised.value.detail == "An operator profile with that name already exists for this project"
    assert "constraint failed" not in str(raised.value.detail).lower()


def test_other_integrity_conflict_is_safe_and_does_not_leak_sqlite_text():
    with pytest.raises(HTTPException) as raised:
        _raise(sqlite3.IntegrityError("FOREIGN KEY constraint failed: secret-db-detail"))
    assert raised.value.status_code == 409
    assert raised.value.detail == "Operator profile constraint conflict"
    assert "secret-db-detail" not in str(raised.value.detail)


def test_installer_is_idempotent_on_production_app():
    from app import app

    expected = {
        "/shared-sky/studio/api/projects/{project_id}/operator-profiles",
        "/shared-sky/studio/api/projects/{project_id}/operator-profiles/{profile_id}",
        "/shared-sky/studio/api/projects/{project_id}/operator-profiles/{profile_id}/activate",
    }

    def matching_paths():
        return [
            getattr(route, "path", "")
            for route in app.router.routes
            if "operator-profiles" in getattr(route, "path", "")
        ]

    before = matching_paths()
    install_shared_sky_operator_profiles(app)
    after_once = matching_paths()
    install_shared_sky_operator_profiles(app)
    after_twice = matching_paths()
    assert after_twice == after_once
    assert len(after_twice) == 5
    assert expected.issubset(set(after_twice))
    assert set(before).issubset(set(after_twice))
