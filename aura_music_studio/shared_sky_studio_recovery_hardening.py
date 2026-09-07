from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

from .shared_sky_control_room import PROFILE_REGISTRY
from .shared_sky_studio_history_graphics import HistoryRepository

_PATCH_MARKER = "_shared_sky_recovery_versioning_installed"


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _public_session_state(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["programme_snapshot"] = _loads(item.pop("programme_snapshot_json", "{}"), {})
    item["transition"] = _loads(item.pop("transition_json", "{}"), {})
    item["autosave_state"] = _loads(item.pop("autosave_state_json", "{}"), {})
    item["last_transport_state"] = _loads(item.pop("last_transport_state_json", "{}"), {})
    item["profile"] = PROFILE_REGISTRY.get(str(item.get("profile_key") or ""), {})
    item.pop("created_at", None)
    item.pop("updated_at", None)
    return item


def _record_recovery_version(
    con: sqlite3.Connection,
    session_id: str,
    user_id: str,
) -> None:
    """Record the updated Studio session version inside the caller's transaction."""
    row = con.execute(
        "SELECT * FROM shared_sky_studio_sessions WHERE id=? AND user_id=?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        raise KeyError(session_id)
    state = _public_session_state(row)
    con.execute(
        "INSERT OR IGNORE INTO shared_sky_studio_versions(session_id,version,state_json,created_at) "
        "VALUES(?,?,?,?)",
        (session_id, int(row["version"]), _json(state), str(row["updated_at"])),
    )
    con.execute(
        "DELETE FROM shared_sky_studio_versions WHERE session_id=? AND version NOT IN "
        "(SELECT version FROM shared_sky_studio_versions WHERE session_id=? ORDER BY version DESC LIMIT 50)",
        (session_id, session_id),
    )


def install_history_recovery_versioning() -> None:
    """Make every atomic history-managed Preview edit part of the canonical recovery ledger.

    HistoryRepository deliberately owns graph-level transactions while StudioRepository owns the
    durable session-version ledger. This compatibility hardening keeps those responsibilities
    separate without introducing another persistence system: after HistoryRepository increments a
    Studio session version, the same SQLite transaction records that exact version in
    ``shared_sky_studio_versions``. A rollback therefore rolls back both the graph mutation and its
    recovery record.
    """

    current: Callable[..., Any] = HistoryRepository._bump
    if getattr(current, _PATCH_MARKER, False):
        return

    original = current

    def versioned_bump(
        self: HistoryRepository,
        con: sqlite3.Connection,
        session: dict[str, Any],
        reason: str,
        *,
        preview_scene_id: str | None = None,
    ) -> None:
        original(
            self,
            con,
            session,
            reason,
            preview_scene_id=preview_scene_id,
        )
        _record_recovery_version(con, str(session["id"]), str(session["user_id"]))

    setattr(versioned_bump, _PATCH_MARKER, True)
    HistoryRepository._bump = versioned_bump


__all__ = ["install_history_recovery_versioning"]
