from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest
from fastapi import FastAPI

from aura_music_studio.shared_skies_emergency_programme import (
    build_emergency_hidden_snapshot,
    install_shared_skies_emergency_programme,
)
from aura_music_studio.shared_sky_control_room import StudioInvariantError
from aura_music_studio.shared_sky_transport_domain import SharedSkyTransportStore


def _snapshot(*, visible: bool = True) -> dict:
    return {
        "schema_version": 1,
        "scene": {"id": "scene-1", "name": "Programme"},
        "sources": [
            {
                "id": "safe-source",
                "name": "Safe",
                "source_type": "image",
                "visible": True,
                "config": {"privacy": "programme_safe"},
            },
            {
                "id": "target-source",
                "name": "Target",
                "source_type": "video",
                "visible": visible,
                "config": {"privacy": "programme_safe"},
            },
        ],
    }


def test_emergency_hide_changes_only_committed_target_copy():
    original = _snapshot()
    hidden, changed = build_emergency_hidden_snapshot(original, "target-source")

    assert changed is True
    assert original["sources"][1]["visible"] is True
    assert hidden["sources"][0]["visible"] is True
    assert hidden["sources"][1]["visible"] is False


def test_emergency_hide_is_idempotent_and_fails_closed_for_unknown_or_secret_state():
    hidden, changed = build_emergency_hidden_snapshot(_snapshot(visible=False), "target-source")
    assert changed is False
    assert hidden["sources"][1]["visible"] is False

    with pytest.raises(StudioInvariantError):
        build_emergency_hidden_snapshot(_snapshot(), "missing-source")

    unsafe = _snapshot()
    unsafe["sources"][1]["config"]["stream_key"] = "must-never-persist"
    with pytest.raises(StudioInvariantError):
        build_emergency_hidden_snapshot(unsafe, "target-source")


def test_emergency_route_installation_is_singular():
    app = FastAPI()
    install_shared_skies_emergency_programme(app)
    install_shared_skies_emergency_programme(app)

    matches = [
        route
        for route in app.router.routes
        if getattr(route, "path", "")
        == "/shared-sky/studio/api/sessions/{session_id}/emergency/hide-source/{source_id}"
        and "POST" in (getattr(route, "methods", set()) or set())
    ]
    assert len(matches) == 1


class _CapacityProbe:
    def __init__(self, state: str | None):
        self.state = state

    @contextmanager
    def connect(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute(
            "CREATE TABLE shared_sky_transport_sessions (broadcast_id TEXT PRIMARY KEY, state TEXT NOT NULL)"
        )
        if self.state is not None:
            con.execute(
                "INSERT INTO shared_sky_transport_sessions (broadcast_id,state) VALUES (?,?)",
                ("live-1", self.state),
            )
        try:
            yield con
        finally:
            con.close()


def test_transport_participant_capacity_is_explicit_bounded_and_fail_closed(monkeypatch):
    probe = _CapacityProbe("live")

    monkeypatch.delenv("SHARED_SKY_MULTIHOST_MAX_PARTICIPANTS", raising=False)
    assert SharedSkyTransportStore.participant_capacity(probe, "live-1") == 0

    monkeypatch.setenv("SHARED_SKY_MULTIHOST_MAX_PARTICIPANTS", "99")
    assert SharedSkyTransportStore.participant_capacity(probe, "live-1") == 8

    monkeypatch.setenv("SHARED_SKY_MULTIHOST_MAX_PARTICIPANTS", "4")
    assert SharedSkyTransportStore.participant_capacity(probe, "live-1") == 4
    assert SharedSkyTransportStore.participant_capacity(probe, "unknown") == 0

    terminal = _CapacityProbe("ended")
    assert SharedSkyTransportStore.participant_capacity(terminal, "live-1") == 0

    monkeypatch.setenv("SHARED_SKY_MULTIHOST_MAX_PARTICIPANTS", "not-a-number")
    assert SharedSkyTransportStore.participant_capacity(probe, "live-1") == 0
