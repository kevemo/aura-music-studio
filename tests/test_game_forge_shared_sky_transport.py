from __future__ import annotations

import subprocess
import sys
import textwrap
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import aura_music_studio.game_forge_shared_sky_transport as bridge
from aura_music_studio.game_forge_live_integration import (
    GameForgeLiveState,
    GameForgeSafeLiveSource,
    LiveInclusionManifest,
)


def _request():
    return SimpleNamespace(headers={})


def _source(*, source_id: str = "gfs_safe_1", session_id: str = "broadcast_1") -> GameForgeSafeLiveSource:
    return GameForgeSafeLiveSource(
        source_adapter_id=source_id,
        project_id="game_1",
        creator_identity_ref="creator_1",
        live_session_id=session_id,
        source_type="clean_game_output",
        safe_display_label="Game Forge — Clean Game Output",
        media_kind="video",
        project_version=4,
        build_id="build_4",
        inclusion_manifest=LiveInclusionManifest(
            capture_scope="game_runtime",
            approved_surfaces=["game_canvas"],
        ),
        rights_readiness="verified",
        correlation_id="corr_game_1",
    )


class _FakeSharedSky:
    def __init__(self):
        self.broadcasts = {
            "broadcast_1": {
                "id": "broadcast_1",
                "project_id": "sky_project_1",
                "state": "draft",
            }
        }

    def broadcast(self, user_id: str, broadcast_id: str):
        assert user_id == "creator_1"
        if broadcast_id not in self.broadcasts:
            raise KeyError(broadcast_id)
        return dict(self.broadcasts[broadcast_id])


class _FakeTransport:
    def __init__(self):
        self.records = {}
        self.register_calls = 0
        self.configure_calls = []

    def register_source(self, user_id, project_id, source_type, source_ref, *, state, capabilities):
        self.register_calls += 1
        item = {
            "id": f"src_transport_{self.register_calls}",
            "user_id": user_id,
            "project_id": project_id,
            "source_type": source_type,
            "source_ref": source_ref,
            "state": state,
            "capabilities": dict(capabilities),
        }
        self.records[item["id"]] = item
        return dict(item)

    def source(self, user_id: str, source_id: str):
        item = self.records.get(source_id)
        if not item or item["user_id"] != user_id:
            raise KeyError(source_id)
        return dict(item)

    def configure(self, user_id, broadcast_id, **kwargs):
        self.configure_calls.append((user_id, broadcast_id, kwargs))
        return {
            "broadcast_id": broadcast_id,
            "source_id": kwargs["source_id"],
            "state": "configuring",
        }

    @contextmanager
    def connect(self):
        raise AssertionError("Direct persistence is not used by bind tests")
        yield


def _install(monkeypatch):
    source = _source()
    state = GameForgeLiveState(project_id="game_1", sources={source.source_adapter_id: source})
    saved = []
    member = SimpleNamespace(user_id="creator_1")
    fake_transport = _FakeTransport()
    fake_sky = _FakeSharedSky()

    monkeypatch.setattr(bridge, "_creator", lambda _request: member)
    monkeypatch.setattr(bridge, "_game", lambda game_id: SimpleNamespace(id=game_id))
    monkeypatch.setattr(bridge, "_load_state", lambda game_id: state)
    monkeypatch.setattr(bridge, "_save_state", lambda value: saved.append(value.model_copy(deep=True)))
    monkeypatch.setattr(bridge, "transport", fake_transport)
    monkeypatch.setattr(bridge, "shared_sky", fake_sky)
    return state, source, fake_transport, fake_sky, saved


def test_transport_binding_registers_canonical_game_project_source_once(monkeypatch):
    state, source, transport, _sky, saved = _install(monkeypatch)
    body = bridge.BindGameTransportRequest(
        internal_playback=True,
        recording_enabled=True,
        rendition_profile="landscape_1080p",
    )

    first = bridge.bind_game_live_transport("game_1", source.source_adapter_id, body, _request())
    second = bridge.bind_game_live_transport("game_1", source.source_adapter_id, body, _request())

    assert transport.register_calls == 1
    assert first["idempotent_source_registration"] is False
    assert second["idempotent_source_registration"] is True
    assert first["shared_sky_programme_source"]["source_type"] == "game_project"
    assert first["shared_sky_programme_source"]["source_ref"] == source.source_adapter_id
    assert source.shared_sky_source_ref == first["shared_sky_programme_source"]["id"]
    assert first["shared_sky_broadcast_id"] == "broadcast_1"
    assert first["shared_sky_project_id"] == "sky_project_1"
    assert first["destination_credentials_stored_by_game_forge"] is False
    assert first["whole_window_capture"] is False
    assert len(transport.configure_calls) == 2
    configure = transport.configure_calls[0][2]
    assert configure["recording_enabled"] is True
    assert configure["internal_playback"] is True
    assert configure["rendition_profile"]["width"] == 1920
    assert configure["rendition_profile"]["height"] == 1080
    assert configure["rendition_profile"]["project_version"] == 4
    assert configure["rendition_profile"]["build_id"] == "build_4"
    assert saved


def test_transport_capabilities_are_bounded_and_never_embed_private_payloads():
    caps = bridge._transport_capabilities(_source())
    assert caps["studio_type"] == "game_forge"
    assert caps["capture_scope"] == "game_runtime"
    assert caps["approved_surfaces"] == ["game_canvas"]
    assert caps["whole_window_capture"] is False
    assert caps["credentials_included"] is False
    assert caps["source_code_payload_included"] is False
    forbidden = {
        "source_code",
        "repository",
        "filesystem_path",
        "environment",
        "credentials",
        "destination",
        "oauth_token",
        "stream_key",
    }
    assert forbidden.isdisjoint(caps)


def test_binding_rejects_missing_or_ended_canonical_broadcast(monkeypatch):
    _state, source, _transport, sky, _saved = _install(monkeypatch)
    sky.broadcasts.clear()
    with pytest.raises(HTTPException) as missing:
        bridge.bind_game_live_transport(
            "game_1",
            source.source_adapter_id,
            bridge.BindGameTransportRequest(),
            _request(),
        )
    assert missing.value.status_code == 404
    assert missing.value.detail["code"] == "live_session_ended"

    sky.broadcasts["broadcast_1"] = {
        "id": "broadcast_1",
        "project_id": "sky_project_1",
        "state": "ended",
    }
    with pytest.raises(HTTPException) as ended:
        bridge.bind_game_live_transport(
            "game_1",
            source.source_adapter_id,
            bridge.BindGameTransportRequest(),
            _request(),
        )
    assert ended.value.status_code == 409
    assert ended.value.detail["code"] == "live_session_ended"


def test_existing_transport_source_must_match_game_source_and_shared_sky_project(monkeypatch):
    _state, source, transport, _sky, _saved = _install(monkeypatch)
    transport.records["src_existing"] = {
        "id": "src_existing",
        "user_id": "creator_1",
        "project_id": "wrong_sky_project",
        "source_type": "game_project",
        "source_ref": source.source_adapter_id,
        "state": "ready",
        "capabilities": {},
    }
    source.shared_sky_source_ref = "src_existing"

    with pytest.raises(HTTPException) as mismatch:
        bridge.bind_game_live_transport(
            "game_1",
            source.source_adapter_id,
            bridge.BindGameTransportRequest(),
            _request(),
        )
    assert mismatch.value.status_code == 409
    assert mismatch.value.detail["code"] == "live_source_privacy_blocked"
    assert transport.register_calls == 0


def test_transport_emergency_hide_marks_game_source_not_ready_and_requests_transport_block(monkeypatch):
    _state, source, _transport, _sky, saved = _install(monkeypatch)
    calls = []

    def sync(user_id, row, *, force_not_ready, reason_code):
        calls.append((user_id, row.source_adapter_id, force_not_ready, reason_code))
        return {
            "id": "src_transport_1",
            "source_type": "game_project",
            "source_ref": row.source_adapter_id,
            "state": "failed",
        }

    monkeypatch.setattr(bridge, "_set_programme_source_state", sync)
    result = bridge.emergency_hide_game_live_transport(
        "game_1",
        source.source_adapter_id,
        _request(),
    )

    assert result["source"]["presentation_mode"] == "brb"
    assert result["source"]["status"] == "hidden"
    assert result["source"]["health"] == "not_ready"
    assert result["shared_sky_programme_source"]["state"] == "failed"
    assert result["transport_preflight_blocked"] is True
    assert result["project_deleted"] is False
    assert result["autosave_terminated"] is False
    assert result["playtest_build_deleted"] is False
    assert calls == [("creator_1", source.source_adapter_id, True, "game_forge_emergency_hide")]
    assert saved


def test_transport_bridge_routes_are_mounted_on_release_app():
    code = textwrap.dedent(
        """
        import app as production_entrypoint

        paths = production_entrypoint.app.openapi().get("paths", {})
        required = {
            ("post", "/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/transport-bind"),
            ("post", "/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/transport-sync"),
            ("post", "/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/transport-emergency-hide"),
        }
        missing = sorted((method, path) for method, path in required if method not in paths.get(path, {}))
        if missing:
            raise SystemExit(f"Game Forge canonical Shared Sky transport routes are missing: {missing}")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
