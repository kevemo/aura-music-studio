from __future__ import annotations

from types import SimpleNamespace

import pytest

from aura_music_studio import shared_sky_chat2_studio_operator as mod


class FakeStudioRepo:
    def __init__(self):
        self.session = {
            "id": "sess",
            "user_id": "u1",
            "project_id": "p1",
            "broadcast_id": "b1",
            "profile": {"width": 1920, "height": 1080, "orientation": "landscape"},
            "version": 4,
        }

    def get_session(self, user_id: str, session_id: str):
        if user_id != "u1" or session_id != "sess":
            raise KeyError(session_id)
        return dict(self.session)

    def _mutate(self, user_id: str, session_id: str, expected_version: int, fields: dict):
        if expected_version != self.session["version"]:
            raise RuntimeError("unexpected test version")
        self.session.update(fields)
        self.session["version"] += 1
        return dict(self.session)


class FakeBase:
    def __init__(self):
        self.events = []

    def broadcast(self, user_id: str, broadcast_id: str):
        return {"id": broadcast_id, "project_id": "p1", "state": "draft"}

    def event(self, user_id: str, broadcast_id: str, event_type: str, payload: dict):
        self.events.append((broadcast_id, event_type, payload))


class FakeBridge:
    def __init__(self):
        self.preflight_calls = 0
        self.preflight_result = {"ready": True, "blocking_errors": []}

    def status(self, user_id: str, broadcast_id: str):
        return {"state": "configuring", "programme_source_bound": True, "authoritative": True}

    def preflight(self, user_id: str, broadcast_id: str, project_id: str, profile: dict):
        self.preflight_calls += 1
        return {
            "binding": {"configured": True},
            "preflight": dict(self.preflight_result),
            "authoritative": True,
        }


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.markers = []

    def status(self, user_id: str, broadcast_id: str):
        return {"session": {"state": "draft"}}

    def start(self, user_id: str, broadcast_id: str, key: str):
        self.calls.append(("start", broadcast_id, key))
        return {"broadcast": {"session": {"state": "live"}}, "partial": False}

    def stop(self, user_id: str, broadcast_id: str, key: str):
        self.calls.append(("stop", broadcast_id, key))
        return {"broadcast": {"session": {"state": "ended"}}, "already_terminal": False}

    def retry_destination(self, user_id: str, broadcast_id: str, destination_id: str, key: str):
        self.calls.append(("retry", broadcast_id, destination_id, key))
        return {"destination_id": destination_id, "state": "live"}

    def add_highlight_marker(
        self,
        user_id: str,
        broadcast_id: str,
        *,
        offset_ms: int,
        label: str,
        marker_type: str,
    ):
        marker = {
            "id": "mark_1",
            "broadcast_id": broadcast_id,
            "offset_ms": offset_ms,
            "label": label,
            "marker_type": marker_type,
        }
        self.markers.append(marker)
        return marker

    def highlight_markers(self, user_id: str, broadcast_id: str):
        return list(self.markers)


@pytest.fixture
def environment(monkeypatch):
    repo = FakeStudioRepo()
    base = FakeBase()
    bridge = FakeBridge()
    transport = FakeTransport()
    monkeypatch.setattr(mod, "studio_repo", repo)
    monkeypatch.setattr(mod, "shared_sky", base)
    monkeypatch.setattr(mod, "chat2_studio_transport", bridge)
    monkeypatch.setattr(mod, "transport", transport)
    monkeypatch.setattr(mod, "_member", lambda request: (SimpleNamespace(user_id="u1"), {}))
    return repo, base, bridge, transport


def test_attach_broadcast_is_versioned_and_project_checked(environment):
    repo, _base, bridge, _transport = environment
    repo.session["broadcast_id"] = None
    result = mod.attach_broadcast(
        "sess",
        mod.BroadcastAttachRequest(broadcast_id="b1", expected_studio_version=4),
        None,
    )
    assert result["session"]["broadcast_id"] == "b1"
    assert result["session"]["version"] == 5
    assert result["transport"]["authoritative"] is True


def test_start_runs_canonical_preflight_then_idempotent_transport(environment):
    _repo, base, bridge, transport = environment
    result = mod.start_transport(
        "sess",
        mod.TransportActionRequest(expected_studio_version=4, idempotency_key="start-key-0001"),
        None,
    )
    assert bridge.preflight_calls == 1
    assert transport.calls == [("start", "b1", "start-key-0001")]
    assert result["broadcast"]["session"]["state"] == "live"
    assert base.events[-1][1] == "studio_transport_start"


def test_failed_preflight_preserves_authoritative_blockers_and_does_not_start(environment):
    _repo, _base, bridge, transport = environment
    bridge.preflight_result = {
        "ready": False,
        "blocking_errors": [
            {"code": "internal_playback_unconfigured", "message": "Playback origin is missing"}
        ],
        "warnings": [],
    }
    with pytest.raises(mod.HTTPException) as caught:
        mod.start_transport(
            "sess",
            mod.TransportActionRequest(expected_studio_version=4, idempotency_key="start-key-0003"),
            None,
        )
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "preflight_blocked"
    assert caught.value.detail["ready"] is False
    assert caught.value.detail["blocking_errors"][0]["code"] == "internal_playback_unconfigured"
    assert transport.calls == []


def test_stop_and_retry_preserve_client_idempotency_key(environment):
    _repo, _base, _bridge, transport = environment
    stopped = mod.stop_transport(
        "sess",
        mod.TransportActionRequest(expected_studio_version=4, idempotency_key="stop-key-0001"),
        None,
    )
    retried = mod.retry_transport_destination(
        "sess",
        mod.DestinationRetryRequest(
            expected_studio_version=4,
            idempotency_key="retry-key-0001",
            destination_id="dest_1",
        ),
        None,
    )
    assert stopped["broadcast"]["session"]["state"] == "ended"
    assert retried["state"] == "live"
    assert ("stop", "b1", "stop-key-0001") in transport.calls
    assert ("retry", "b1", "dest_1", "retry-key-0001") in transport.calls


def test_marker_actions_use_chat2_authoritative_marker_store(environment):
    _repo, base, _bridge, _transport = environment
    result = mod.create_marker(
        "sess",
        mod.MarkerRequest(offset_ms=15000, label="Big moment", marker_type="highlight"),
        None,
    )
    listed = mod.list_markers("sess", None)
    assert result["authoritative"] is True
    assert result["marker"]["offset_ms"] == 15000
    assert listed["markers"][0]["label"] == "Big moment"
    assert base.events[-1][1] == "studio_marker_created"


def test_stale_studio_version_blocks_transport_action_before_provider_call(environment):
    _repo, _base, _bridge, transport = environment
    with pytest.raises(Exception, match="Studio version conflict"):
        mod.start_transport(
            "sess",
            mod.TransportActionRequest(expected_studio_version=3, idempotency_key="start-key-0002"),
            None,
        )
    assert transport.calls == []
