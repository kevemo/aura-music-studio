from __future__ import annotations

from aura_music_studio import creation_live as cl
from aura_music_studio.creation_live_authority import _recording_truth, _safe_chat2_preflight
from aura_music_studio.shared_sky_transport_domain import transport


def test_merged_chat2_transport_contract_is_available():
    assert callable(transport.register_source)
    assert callable(transport.source)
    assert callable(transport.preflight)
    assert callable(transport.status)


def test_recording_projection_strips_storage_and_provider_fields(monkeypatch):
    monkeypatch.setattr(
        transport,
        "status",
        lambda user_id, broadcast_id: {
            "recordings": [
                {
                    "id": "rec_1",
                    "kind": "programme",
                    "state": "ready",
                    "asset_id": "asset_1",
                    "storage_uri": "s3://private-bucket/live/recording.mp4",
                    "checksum_sha256": "private-checksum",
                    "size_bytes": 1234,
                    "duration_ms": 5678,
                    "reason_code": "ok",
                    "updated_at": "2026-09-05T00:00:00+00:00",
                    "provider_payload": {"token": "never-return"},
                }
            ]
        },
    )

    truth = _recording_truth("creator-a", "broadcast-a", "rec_1")
    recording = truth["recording"]

    assert truth["available"] is True
    assert recording["asset_id"] == "asset_1"
    assert "storage_uri" not in recording
    assert "checksum_sha256" not in recording
    assert "provider_payload" not in recording
    assert "private-bucket" not in repr(truth)
    assert "never-return" not in repr(truth)


def test_transport_preflight_projection_keeps_only_actionable_safe_fields(monkeypatch):
    monkeypatch.setattr(
        transport,
        "preflight",
        lambda user_id, broadcast_id: {
            "ready": False,
            "blocking_errors": [{"code": "media_plane_unavailable", "message": "Media plane unavailable"}],
            "warnings": [{"code": "recording_optional"}],
            "correlation_id": "corr_safe",
            "destination_credentials": "must-not-leak",
            "storage_uri": "s3://private",
        },
    )

    value = _safe_chat2_preflight("creator-a", "broadcast-a")

    assert value["available"] is True
    assert value["ready"] is False
    assert value["state"] == "blocked"
    assert value["correlation_id"] == "corr_safe"
    assert "destination_credentials" not in value
    assert "storage_uri" not in value
    assert "must-not-leak" not in repr(value)


def test_creation_source_registration_passes_opaque_reference_and_safe_capabilities(monkeypatch):
    calls: list[dict] = []

    def fake_register(user_id, project_id, source_type, source_ref, state="ready", capabilities=None):
        calls.append(
            {
                "user_id": user_id,
                "project_id": project_id,
                "source_type": source_type,
                "source_ref": source_ref,
                "state": state,
                "capabilities": capabilities,
            }
        )
        return {
            "id": "src_transport",
            "project_id": project_id,
            "source_type": source_type,
            "source_ref": source_ref,
            "state": state,
            "capabilities": capabilities or {},
        }

    monkeypatch.setattr(transport, "register_source", fake_register)
    descriptor = {
        "studio_type": "music",
        "source_adapter_id": "cls_safe",
        "schema_version": 1,
        "privacy_classification": "project_safe_output",
        "capabilities": {"audio": True, "video": False, "still": False},
    }

    result = cl._transport_register("creator-a", "sky-project", descriptor, None)

    assert result["available"] is True
    assert calls[0]["source_ref"] == "creation-live://cls_safe"
    assert calls[0]["source_type"] == "music_project"
    assert calls[0]["capabilities"]["privacy"] == "project_safe_output"
    assert "token" not in repr(calls[0]).lower()
    assert "credential" not in repr(calls[0]).lower()


def test_existing_transport_source_is_reused_instead_of_registered_twice(monkeypatch):
    monkeypatch.setattr(
        transport,
        "source",
        lambda user_id, source_id: {
            "id": source_id,
            "project_id": "sky-project",
            "state": "ready",
        },
    )

    def fail_register(*args, **kwargs):
        raise AssertionError("register_source must not run when the existing source is still valid")

    monkeypatch.setattr(transport, "register_source", fail_register)
    descriptor = {
        "studio_type": "image_visual",
        "source_adapter_id": "cls_image",
        "schema_version": 1,
        "privacy_classification": "project_safe_output",
        "capabilities": {"audio": False, "video": False, "still": True},
    }

    result = cl._transport_register("creator-a", "sky-project", descriptor, "src_existing")

    assert result["available"] is True
    assert result["reused"] is True
    assert result["source"]["id"] == "src_existing"
