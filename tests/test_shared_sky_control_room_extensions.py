from __future__ import annotations

from types import SimpleNamespace

import pytest

from aura_music_studio import shared_sky_control_room_extensions as ext
from aura_music_studio.shared_sky_control_room import StudioInvariantError, StudioTransportError


class TemplateGraph:
    def __init__(self):
        self.scenes = {}
        self.sources = {}
        self.deleted = []

    def project(self, user_id, project_id):
        if (user_id, project_id) != ("u1", "p1"):
            raise KeyError(project_id)
        return {"id": project_id, "user_id": user_id}

    def create_scene(self, user_id, project_id, body):
        scene_id = f"s{len(self.scenes) + 1}"
        self.scenes[scene_id] = {
            "id": scene_id,
            "user_id": user_id,
            "project_id": project_id,
            "name": body.name,
            "layout_key": body.layout_key,
            "transition_key": body.transition_key,
            "transition_ms": body.transition_ms,
        }
        return dict(self.scenes[scene_id])

    def create_source(self, user_id, scene_id, body):
        source_id = f"src{len(self.sources) + 1}"
        self.sources[source_id] = {
            "id": source_id,
            "scene_id": scene_id,
            "project_id": "p1",
            "user_id": user_id,
            "source_type": body.source_type,
            "name": body.name,
            "config": dict(body.config),
            "visible": body.visible,
            "locked": body.locked,
            "z_index": body.z_index,
        }
        return dict(self.sources[source_id])

    def scene(self, user_id, scene_id):
        row = dict(self.scenes[scene_id])
        row["sources"] = [dict(source) for source in self.sources.values() if source["scene_id"] == scene_id]
        return row

    def delete_scene(self, user_id, scene_id):
        self.deleted.append(scene_id)
        self.scenes.pop(scene_id, None)


class FailingTemplateGraph(TemplateGraph):
    def create_source(self, user_id, scene_id, body):
        raise RuntimeError("fixture source failure")


def test_scene_template_library_contains_requested_original_starters():
    names = {row["name"] for row in ext.SCENE_TEMPLATES.values()}
    assert {
        "Camera Full Screen", "Camera + Chat", "Creator + Canvas", "Canvas Only", "Interview 2-Up",
        "Panel / Grid", "Screen Share + Presenter", "Tutorial", "Music Performance", "Gameplay",
        "Premiere", "BRB", "Starting Soon", "Ending", "Custom",
    } <= names


def test_template_instantiation_creates_hidden_unattached_capture_slots(monkeypatch):
    graph = TemplateGraph()
    monkeypatch.setattr(ext, "shared_sky", graph)
    scene = ext.instantiate_template("u1", "p1", "interview-2-up")
    assert scene["name"] == "Interview 2-Up"
    assert len(scene["sources"]) == 2
    assert all(source["source_type"] == "remote_guest" for source in scene["sources"])
    assert all(source["visible"] is False for source in scene["sources"])
    assert all(source["config"]["template_slot"] is True for source in scene["sources"])


def test_template_instantiation_rolls_back_scene_on_source_failure(monkeypatch):
    graph = FailingTemplateGraph()
    monkeypatch.setattr(ext, "shared_sky", graph)
    with pytest.raises(RuntimeError, match="fixture source failure"):
        ext.instantiate_template("u1", "p1", "camera-full-screen")
    assert graph.scenes == {}
    assert graph.deleted == ["s1"]


def test_unknown_template_is_rejected(monkeypatch):
    graph = TemplateGraph()
    monkeypatch.setattr(ext, "shared_sky", graph)
    with pytest.raises(StudioInvariantError, match="Unknown Shared Sky scene template"):
        ext.instantiate_template("u1", "p1", "competitor-copy")


def test_audio_presets_only_use_supported_real_processing_fields():
    allowed = {"gain", "pan", "delay_ms", "monitor", "high_pass_hz", "compressor", "limiter"}
    assert {"speech", "podcast", "music", "gaming", "interview", "quiet-room", "noisy-room"} <= set(ext.AUDIO_PRESETS)
    for preset in ext.AUDIO_PRESETS.values():
        assert set(preset) <= allowed


class NoParticipants:
    pass


class ParticipantProvider:
    def studio_participants(self, user_id, broadcast_id):
        return [
            {
                "participant_id": "guest-1",
                "display_name": "Guest",
                "stage": "green_room",
                "connection_state": "connected",
                "camera": "on",
                "microphone": "on",
                "connection_quality": "good",
                "role": "guest",
            }
        ]


def test_participant_adapter_never_implies_connected_means_programme():
    unsupported = ext.ParticipantCompatibilityAdapter(NoParticipants()).list("u1", "b1")
    assert unsupported["supported"] is False and unsupported["participants"] == []
    result = ext.ParticipantCompatibilityAdapter(ParticipantProvider()).list("u1", "b1")
    assert result["supported"] is True
    assert result["participants"][0]["connection_state"] == "connected"
    assert result["participants"][0]["stage"] == "green_room"


def test_participant_adapter_requires_broadcast_session_even_if_provider_exists():
    result = ext.ParticipantCompatibilityAdapter(ParticipantProvider()).list("u1", None)
    assert result == {
        "supported": False,
        "participants": [],
        "reason": "Participant staging requires a broadcast session",
    }


class NoRecording:
    pass


class RecordingProvider:
    def start_recording(self, user_id, broadcast_id):
        return {"authoritative": True, "state": "recording", "recording_id": "r1"}

    def stop_recording(self, user_id, broadcast_id):
        return {"authoritative": True, "state": "stopped", "recording_id": "r1"}


def test_recording_actions_fail_closed_until_chat2_contract_exists():
    adapter = ext.RecordingCompatibilityAdapter(NoRecording())
    with pytest.raises(StudioTransportError, match="recording start contract not merged"):
        adapter.action("u1", "b1", "start")
    with pytest.raises(StudioTransportError, match="requires a broadcast session"):
        adapter.action("u1", None, "stop")


def test_recording_actions_accept_authoritative_provider_state_only():
    adapter = ext.RecordingCompatibilityAdapter(RecordingProvider())
    assert adapter.action("u1", "b1", "start")["state"] == "recording"
    assert adapter.action("u1", "b1", "stop")["state"] == "stopped"


class RepoFixture:
    def __init__(self, session):
        self.session = dict(session)
        self.autosaves = []

    def get_session(self, user_id, session_id):
        return dict(self.session)

    def set_autosave_state(self, user_id, session_id, expected_version, state):
        if expected_version != self.session["version"]:
            raise AssertionError("wrong version")
        self.session["version"] += 1
        self.autosaves.append(state)
        return dict(self.session)


class MediaGraph:
    def __init__(self, source_type="video"):
        self.row = {
            "id": "media1", "project_id": "p1", "scene_id": "s1", "source_type": source_type,
            "name": "Media", "config": {"privacy": "programme_safe"}, "visible": True,
            "locked": False, "z_index": 1,
        }

    def source(self, user_id, source_id):
        return dict(self.row)

    def update_source(self, user_id, source_id, body):
        self.row["config"] = dict(body.config)
        return dict(self.row)


def test_media_cue_is_non_destructive_and_does_not_change_programme(monkeypatch):
    repo = RepoFixture({"id": "sess", "project_id": "p1", "version": 4})
    graph = MediaGraph("video")
    monkeypatch.setattr(ext, "studio_repo", repo)
    monkeypatch.setattr(ext, "shared_sky", graph)
    result = ext.apply_media_cue(
        "u1",
        "sess",
        "media1",
        ext.MediaCuePatch(
            expected_session_version=4,
            cue_ms=1500,
            trim_in_ms=500,
            trim_out_ms=5000,
            loop=True,
            volume=0.75,
            autoplay_on_scene_enter=True,
            on_scene_exit="stop",
        ),
    )
    playback = result["source"]["config"]["playback"]
    assert playback["cue_ms"] == 1500 and playback["loop"] is True
    assert result["programme_unchanged"] is True
    assert repo.autosaves[0]["reason"] == "media_cue"


def test_media_cue_rejects_non_media_and_invalid_trim(monkeypatch):
    repo = RepoFixture({"id": "sess", "project_id": "p1", "version": 1})
    monkeypatch.setattr(ext, "studio_repo", repo)
    monkeypatch.setattr(ext, "shared_sky", MediaGraph("camera"))
    with pytest.raises(StudioInvariantError, match="only to media"):
        ext.apply_media_cue("u1", "sess", "media1", ext.MediaCuePatch(expected_session_version=1))
    monkeypatch.setattr(ext, "shared_sky", MediaGraph("video"))
    with pytest.raises(StudioInvariantError, match="trim-out"):
        ext.apply_media_cue(
            "u1", "sess", "media1",
            ext.MediaCuePatch(expected_session_version=1, trim_in_ms=1000, trim_out_ms=900),
        )


def test_aura_diagnostics_are_advisory_and_evidence_backed(monkeypatch):
    fake = SimpleNamespace(
        session=lambda user_id, session_id: {
            "session": {
                "id": session_id,
                "preview_scene_id": "s1",
                "programme_scene_id": None,
            },
            "project": {
                "scenes": [
                    {
                        "id": "s1",
                        "sources": [
                            {
                                "id": "cam",
                                "source_type": "camera",
                                "visible": False,
                                "config": {"template_slot": True},
                            }
                        ],
                    }
                ]
            },
            "transport": {"state": "offline", "programme_commit_supported": True},
        }
    )
    monkeypatch.setattr(ext, "studio", fake)
    result = ext.aura_production_diagnostics("u1", "sess")
    assert result["mode"] == "advisory_only"
    assert result["authoritative_actions_performed"] is False
    assert result["evidence_source"] == "current studio/transport state"
    messages = " ".join(row["message"] for row in result["recommendations"])
    assert "no committed scene" in messages.lower()
    assert "unattached template slots" in messages.lower()


def test_aura_diagnostics_report_missing_live_commit_without_fabricating_provider_state(monkeypatch):
    fake = SimpleNamespace(
        session=lambda user_id, session_id: {
            "session": {"id": session_id, "preview_scene_id": "s1", "programme_scene_id": "s0"},
            "project": {"scenes": [{"id": "s1", "sources": []}]},
            "transport": {"state": "live", "programme_commit_supported": False},
        }
    )
    monkeypatch.setattr(ext, "studio", fake)
    result = ext.aura_production_diagnostics("u1", "sess")
    transport = next(row for row in result["recommendations"] if row["kind"] == "transport")
    assert transport["severity"] == "warning"
    assert transport["evidence"]["programme_commit_supported"] is False
