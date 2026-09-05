from __future__ import annotations

from types import SimpleNamespace

import pytest

from aura_music_studio import shared_sky_chat2_studio_integration as mod
from aura_music_studio.shared_sky_control_room import StudioTransportError


class FakeBase:
    def __init__(self):
        self.broadcasts = {
            "b1": {"id": "b1", "project_id": "p1", "state": "draft"},
        }

    def broadcast(self, user_id: str, broadcast_id: str):
        if broadcast_id not in self.broadcasts:
            raise KeyError(broadcast_id)
        return dict(self.broadcasts[broadcast_id])


class FakeTransport:
    def __init__(self):
        self.sources: dict[str, dict] = {}
        self.session = {
            "state": "draft",
            "source_id": None,
            "internal_playback": True,
            "recording_enabled": False,
            "correlation_id": "corr_1",
            "trace_id": "trace_1",
        }
        self.register_calls = 0
        self.configure_calls = 0
        self.preflight_calls = 0
        self.reconcile_calls = 0
        self.events: list[tuple] = []
        self.recordings: list[dict] = []

    def status(self, user_id: str, broadcast_id: str):
        return {
            "session": dict(self.session),
            "destinations": [],
            "recordings": list(self.recordings),
            "playback": {"capability_state": "credentials_missing"},
            "relay": {"enabled": False},
        }

    def reconcile(self, user_id: str, broadcast_id: str):
        self.reconcile_calls += 1
        return self.status(user_id, broadcast_id)

    def source(self, user_id: str, source_id: str):
        if source_id not in self.sources:
            raise KeyError(source_id)
        return dict(self.sources[source_id])

    def register_source(
        self,
        user_id: str,
        project_id: str,
        source_type: str,
        source_ref: str,
        state: str = "ready",
        capabilities: dict | None = None,
    ):
        self.register_calls += 1
        source_id = f"src_{self.register_calls}"
        row = {
            "id": source_id,
            "user_id": user_id,
            "project_id": project_id,
            "source_type": source_type,
            "source_ref": source_ref,
            "state": state,
            "capabilities": capabilities or {},
        }
        self.sources[source_id] = row
        return dict(row)

    def configure(
        self,
        user_id: str,
        broadcast_id: str,
        *,
        source_id: str | None,
        internal_playback: bool,
        rendition_profile: dict,
        recording_enabled: bool,
        ingest_session_id: str | None = None,
    ):
        self.configure_calls += 1
        self.session.update(
            {
                "state": "configuring",
                "source_id": source_id,
                "internal_playback": internal_playback,
                "recording_enabled": recording_enabled,
                "rendition_profile": rendition_profile,
                "ingest_session_id": ingest_session_id,
            }
        )
        return self.status(user_id, broadcast_id)

    def preflight(self, user_id: str, broadcast_id: str):
        self.preflight_calls += 1
        return {
            "ready": True,
            "blocking_errors": [],
            "warnings": [],
            "correlation_id": self.session["correlation_id"],
            "trace_id": self.session["trace_id"],
        }

    def request_recording(self, user_id: str, broadcast_id: str, kind: str):
        row = {
            "id": "rec_1",
            "broadcast_id": broadcast_id,
            "kind": kind,
            "state": "requested",
            "storage_uri": "configured://…",
        }
        self.recordings = [row]
        return dict(row)

    def emit(self, *args):
        self.events.append(args)


@pytest.fixture
def adapter(monkeypatch):
    base = FakeBase()
    provider = FakeTransport()
    monkeypatch.setattr(mod, "shared_sky", base)
    return mod.CanonicalChat2StudioTransportAdapter(provider), provider, base


def landscape_snapshot():
    return {
        "profile": {
            "width": 1920,
            "height": 1080,
            "orientation": "landscape",
        },
        "scene": {"id": "scene_1"},
        "sources": [],
    }


def test_inactive_programme_commit_registers_and_configures_one_stable_source(adapter):
    bridge, provider, _ = adapter
    result = bridge.commit_programme("u1", "b1", landscape_snapshot(), "corr-cut")
    assert result.accepted is True
    assert result.authoritative is True
    assert provider.register_calls == 1
    assert provider.configure_calls == 1
    source = provider.sources[provider.session["source_id"]]
    assert source["source_type"] == "studio_program"
    assert source["source_ref"] == "studio://p1/programme/main"
    assert provider.session["rendition_profile"]["landscape"] == "1080p30"

    second = bridge.commit_programme("u1", "b1", landscape_snapshot(), "corr-cut-2")
    assert second.accepted is True
    assert provider.register_calls == 1
    assert provider.configure_calls == 1


def test_inactive_bound_source_reconfigures_without_registering_duplicate(adapter):
    bridge, provider, _ = adapter
    first = bridge.bind("u1", "b1", "p1", landscape_snapshot()["profile"])
    source_id = first["source"]["id"]
    second = bridge.bind(
        "u1",
        "b1",
        "p1",
        {"width": 1080, "height": 1920, "orientation": "portrait"},
        internal_playback=False,
        recording_enabled=True,
    )
    assert second["source"]["id"] == source_id
    assert second["created"] is False
    assert second["reconfigured"] is True
    assert provider.register_calls == 1
    assert provider.configure_calls == 2
    assert provider.session["internal_playback"] is False
    assert provider.session["recording_enabled"] is True
    assert provider.session["rendition_profile"]["portrait"] == "1080x1920p30"


def test_active_transport_rejects_cut_when_not_bound_to_chat3_programme(adapter):
    bridge, provider, base = adapter
    provider.session["state"] = "live"
    base.broadcasts["b1"]["state"] = "live"
    result = bridge.commit_programme("u1", "b1", landscape_snapshot(), "corr-live")
    assert result.accepted is False
    assert "not bound" in result.reason
    assert provider.register_calls == 0
    assert provider.configure_calls == 0


def test_active_transport_accepts_cut_when_stable_studio_source_is_bound(adapter):
    bridge, provider, base = adapter
    source = provider.register_source(
        "u1",
        "p1",
        "studio_program",
        "studio://p1/programme/main",
        state="ready",
        capabilities={"audio": True},
    )
    provider.session.update({"state": "live", "source_id": source["id"]})
    base.broadcasts["b1"]["state"] = "live"
    result = bridge.commit_programme("u1", "b1", landscape_snapshot(), "corr-live")
    assert result.accepted is True
    assert result.state == "live"
    assert provider.configure_calls == 0
    assert provider.events and provider.events[-1][1] == "studio_programme_committed"


def test_active_bound_source_does_not_mutate_transport_configuration(adapter):
    bridge, provider, base = adapter
    source = provider.register_source(
        "u1", "p1", "studio_program", "studio://p1/programme/main", state="ready"
    )
    provider.session.update({"state": "live", "source_id": source["id"]})
    base.broadcasts["b1"]["state"] = "live"
    result = bridge.bind(
        "u1",
        "b1",
        "p1",
        {"width": 1080, "height": 1920, "orientation": "portrait"},
        internal_playback=False,
        recording_enabled=True,
    )
    assert result["reconfigured"] is False
    assert provider.configure_calls == 0
    assert provider.session["internal_playback"] is True
    assert provider.session["recording_enabled"] is False


def test_preflight_binds_before_calling_chat2_authority(adapter):
    bridge, provider, _ = adapter
    result = bridge.preflight(
        "u1",
        "b1",
        "p1",
        landscape_snapshot()["profile"],
        internal_playback=True,
        recording_enabled=True,
    )
    assert result["authoritative"] is True
    assert result["binding"]["source"]["source_type"] == "studio_program"
    assert result["preflight"]["ready"] is True
    assert provider.preflight_calls == 1
    assert provider.session["recording_enabled"] is True


def test_status_exposes_actual_chat2_recording_and_correlation_truth(adapter):
    bridge, provider, _ = adapter
    bridge.commit_programme("u1", "b1", landscape_snapshot(), "corr")
    provider.recordings = [{"id": "rec", "kind": "programme", "state": "recording"}]
    status = bridge.status("u1", "b1")
    assert status["authoritative"] is True
    assert status["programme_source_bound"] is True
    assert status["correlation_id"] == "corr_1"
    assert status["recordings"][0]["state"] == "recording"
    assert provider.reconcile_calls == 1

    capabilities = bridge.recording_capabilities("u1", "b1")
    assert capabilities["supported"] is True
    assert capabilities["state"] == "recording"
    assert capabilities["manual_stop_supported"] is False
    assert provider.reconcile_calls == 2


def test_recording_action_uses_chat2_request_and_refuses_fake_stop():
    provider = FakeTransport()
    actions = mod.CanonicalChat2RecordingActions(provider)
    started = actions.action("u1", "b1", "start", "programme")
    assert started["authoritative"] is True
    assert started["state"] == "requested"
    with pytest.raises(StudioTransportError, match="does not expose standalone recording stop"):
        actions.action("u1", "b1", "stop", "programme")


def test_profile_mapping_is_explicit_and_profile_driven():
    portrait = mod._profile_rendition(
        {"width": 1080, "height": 1920, "orientation": "portrait"}
    )
    square = mod._profile_rendition(
        {"width": 1080, "height": 1080, "orientation": "square"}
    )
    assert portrait["portrait"] == "1080x1920p30"
    assert portrait["renditions"][0]["height"] == 1920
    assert square["square"] == "1080x1080p30"


def test_active_bind_refuses_source_swap(adapter):
    bridge, provider, base = adapter
    provider.session["state"] = "degraded"
    provider.session["source_id"] = "other"
    provider.sources["other"] = {
        "id": "other",
        "project_id": "p1",
        "source_type": "external_encoder",
        "source_ref": "rtmp://encoder",
        "state": "ready",
    }
    base.broadcasts["b1"]["state"] = "live"
    with pytest.raises(StudioTransportError, match="stop/reconfigure"):
        bridge.bind(
            "u1",
            "b1",
            "p1",
            landscape_snapshot()["profile"],
        )
