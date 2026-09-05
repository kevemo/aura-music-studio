from __future__ import annotations

import pytest

from aura_music_studio import creation_live_chat3_bridge as bridge


class FakeSky:
    def __init__(self):
        self.sources: dict[str, dict] = {}
        self.broadcast_state = "live"
        self.project_id = "sky-project"
        self.scene_id = "scene-preview"
        self.created = 0

    def broadcast(self, user_id: str, broadcast_id: str):
        if user_id != "user-1" or broadcast_id != "broadcast-1":
            raise KeyError(broadcast_id)
        return {"id": broadcast_id, "project_id": self.project_id, "state": self.broadcast_state}

    def project(self, user_id: str, project_id: str):
        if user_id != "user-1" or project_id != self.project_id:
            raise KeyError(project_id)
        return {"id": project_id, "scenes": [self.scene(user_id, self.scene_id)]}

    def scene(self, user_id: str, scene_id: str):
        if user_id != "user-1" or scene_id != self.scene_id:
            raise KeyError(scene_id)
        return {
            "id": self.scene_id,
            "project_id": self.project_id,
            "sources": [dict(row) for row in self.sources.values() if row["scene_id"] == self.scene_id],
        }

    def create_source(self, user_id: str, scene_id: str, body):
        assert user_id == "user-1" and scene_id == self.scene_id
        self.created += 1
        source_id = f"chat3-source-{self.created}"
        row = {
            "id": source_id,
            "scene_id": scene_id,
            "project_id": self.project_id,
            "source_type": body.source_type,
            "name": body.name,
            "config": dict(body.config),
            "visible": body.visible,
            "locked": body.locked,
            "z_index": body.z_index,
        }
        self.sources[source_id] = row
        return dict(row)

    def update_source(self, user_id: str, source_id: str, body):
        if user_id != "user-1" or source_id not in self.sources:
            raise KeyError(source_id)
        row = self.sources[source_id]
        for key in ("name", "config", "visible", "locked", "z_index"):
            value = getattr(body, key, None)
            if value is not None:
                row[key] = value
        return dict(row)


def item(*, studio_type="video_cinema", media_kind="audiovisual", privacy="project_safe_output"):
    return {
        "source_adapter_id": "cla_abc123",
        "project_name": "creative-project",
        "studio_type": studio_type,
        "source_status": "ready",
        "shared_sky_project_id": "sky-project",
        "broadcast_id": "broadcast-1",
        "descriptor": {
            "source_adapter_id": "cla_abc123",
            "schema_version": 1,
            "studio_type": studio_type,
            "source_type": "clean_video_output" if studio_type == "video_cinema" else "clean_music_output",
            "safe_display_name": "Safe project output",
            "media_kind": media_kind,
            "privacy_classification": privacy,
            "preview_kind": "media",
            "public_version_id": "version-7",
            "correlation_id": "corr-safe",
            "rights": {"state": "ready", "codes": ["rights_metadata_ready"]},
            "capabilities": {
                "audio": media_kind in {"audio", "audiovisual"},
                "video": media_kind in {"video", "audiovisual"},
                "still": media_kind == "still-or-slideshow",
                "version_pin": True,
            },
        },
    }


@pytest.mark.parametrize(
    ("media_kind", "privacy", "expected"),
    [
        ("audio", "project_safe_output", "audio"),
        ("audiovisual", "project_safe_output", "video"),
        ("video", "project_safe_output", "video"),
        ("still-or-slideshow", "project_safe_output", "image"),
        ("audiovisual", "advanced_workspace", "presentation"),
        ("data-overlay", "project_safe_output", "presentation"),
    ],
)
def test_chat7_media_maps_only_to_chat3_canonical_source_types(media_kind, privacy, expected):
    descriptor = item(media_kind=media_kind, privacy=privacy)["descriptor"]
    assert bridge.canonical_chat3_source_type(descriptor) == expected


def test_programme_truth_uses_exact_adapter_in_immutable_chat3_snapshot(monkeypatch):
    sky = FakeSky()
    monkeypatch.setattr(bridge.cl, "shared_sky", sky)
    session = {
        "id": "studio-session-1",
        "project_id": "sky-project",
        "broadcast_id": "broadcast-1",
        "programme_scene_id": "scene-programme",
        "programme_snapshot": {
            "sources": [
                {
                    "id": "chat3-source-9",
                    "visible": True,
                    "config": {"creation_live_adapter_id": "cla_abc123", "privacy": "programme_safe"},
                }
            ]
        },
    }
    monkeypatch.setattr(bridge, "_find_studio_session", lambda *_args, **_kwargs: session)

    truth = bridge.programme_truth("user-1", item())
    assert truth["programme_state"] == "on_programme"
    assert truth["on_air"] is True
    assert truth["programme_source_id"] == "chat3-source-9"
    assert truth["source"] == "chat3_programme_snapshot"

    sky.broadcast_state = "draft"
    offline = bridge.programme_truth("user-1", item())
    assert offline["programme_state"] == "on_programme"
    assert offline["on_air"] is False


def test_programme_truth_does_not_treat_other_source_or_active_transport_as_on_air(monkeypatch):
    sky = FakeSky()
    monkeypatch.setattr(bridge.cl, "shared_sky", sky)
    session = {
        "id": "studio-session-1",
        "programme_scene_id": "scene-programme",
        "programme_snapshot": {
            "sources": [
                {
                    "id": "another-source",
                    "visible": True,
                    "config": {"creation_live_adapter_id": "different-adapter"},
                }
            ]
        },
    }
    monkeypatch.setattr(bridge, "_find_studio_session", lambda *_args, **_kwargs: session)
    truth = bridge.programme_truth("user-1", item())
    assert truth["programme_state"] == "not_on_programme"
    assert truth["on_air"] is False


def test_register_preview_source_is_idempotent_and_never_exposes_server_backing_ref(monkeypatch):
    sky = FakeSky()
    monkeypatch.setattr(bridge.cl, "shared_sky", sky)
    session = {
        "id": "studio-session-1",
        "project_id": "sky-project",
        "broadcast_id": "broadcast-1",
        "preview_scene_id": "scene-preview",
        "programme_scene_id": None,
        "programme_snapshot": {},
    }
    monkeypatch.setattr(bridge, "_find_studio_session", lambda *_args, **_kwargs: session)
    source_item = item()
    source_item["server_ref"] = "private/server/path/master.wav"

    first = bridge.register_preview_source("user-1", source_item)
    second = bridge.register_preview_source("user-1", source_item)

    assert first["registered"] is True and first["reused"] is False
    assert second["registered"] is True and second["reused"] is True
    assert first["chat3_source_id"] == second["chat3_source_id"]
    assert sky.created == 1
    stored = sky.sources[first["chat3_source_id"]]
    assert stored["source_type"] == "video"
    assert stored["config"]["privacy"] == "programme_safe"
    assert stored["config"]["creation_live_adapter_id"] == "cla_abc123"
    assert "server_ref" not in repr(stored["config"])
    assert "private/server/path" not in repr(stored["config"])
    assert stored["config"]["preview_endpoint"].startswith("/creation-live/projects/")


def test_detach_hides_chat3_graph_but_does_not_falsify_committed_programme(monkeypatch):
    sky = FakeSky()
    monkeypatch.setattr(bridge.cl, "shared_sky", sky)
    source_item = item()
    session = {
        "id": "studio-session-1",
        "project_id": "sky-project",
        "broadcast_id": "broadcast-1",
        "preview_scene_id": "scene-preview",
        "programme_scene_id": "scene-preview",
        "programme_snapshot": {},
    }
    monkeypatch.setattr(bridge, "_find_studio_session", lambda *_args, **_kwargs: session)
    registered = bridge.register_preview_source("user-1", source_item)
    chat3_source_id = registered["chat3_source_id"]
    session["programme_snapshot"] = {
        "sources": [
            {
                "id": chat3_source_id,
                "visible": True,
                "config": {"creation_live_adapter_id": "cla_abc123"},
            }
        ]
    }

    result = bridge.hide_graph_sources("user-1", source_item)
    assert result["hidden_sources"] == 1
    assert sky.sources[chat3_source_id]["visible"] is False
    assert result["programme_snapshot_unchanged"] is True
    assert result["authoritative_live"]["on_air"] is True
    assert result["authoritative_live"]["programme_state"] == "on_programme"


def test_register_preview_source_refuses_blocked_rights_and_missing_control_room(monkeypatch):
    blocked = item()
    blocked["descriptor"]["rights"] = {"state": "blocked", "codes": ["private_asset_not_eligible"]}
    assert bridge.register_preview_source("user-1", blocked)["state"] == "project_rights_blocked"

    monkeypatch.setattr(bridge, "_find_studio_session", lambda *_args, **_kwargs: None)
    ready = item()
    assert bridge.register_preview_source("user-1", ready)["state"] == "control_room_not_open"
