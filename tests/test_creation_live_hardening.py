from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio import creation_live as cl
from aura_music_studio.creation_live_hardening import (
    _IN_PROGRESS,
    _request_hash,
    _revive_rediscovered_sources,
    install_creation_live_hardening,
)


install_creation_live_hardening()


def _descriptor(source_id: str, *, expires_at: str | None = None):
    return cl.CreationLiveSourceDescriptor(
        source_adapter_id=source_id,
        studio_type="music",
        project_id="song-one",
        workspace_id="creator-a",
        creator_id="creator-a",
        source_type="clean_music_output",
        safe_display_name="Clean master",
        media_kind="audio",
        capabilities=cl.SourceCapabilities(audio=True),
        privacy_classification="project_safe_output",
        inclusion_manifest=["approved project output"],
        rights=cl.RightsPreflight(state="ready"),
        expires_at=expires_at,
    )


def test_expired_source_handle_is_revoked_fail_closed(tmp_path):
    store = cl.CreationLiveStore(tmp_path / "live.sqlite3")
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    descriptor = _descriptor("cls_expired", expires_at=expired)
    store.upsert_discovered("creator-a", descriptor, "master", "output/master.wav")

    item = store.get("creator-a", descriptor.source_adapter_id)

    assert item["source_status"] == "revoked"
    assert item["descriptor"]["health"] == "revoked"
    assert item["descriptor"]["revoked_at"]
    assert item["active_editor_instance_id"] is None


def test_revoked_source_cannot_be_silently_reactivated(tmp_path):
    store = cl.CreationLiveStore(tmp_path / "live.sqlite3")
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    descriptor = _descriptor("cls_revoked", expires_at=expired)
    store.upsert_discovered("creator-a", descriptor, "master", "output/master.wav")
    item = store.get("creator-a", descriptor.source_adapter_id)
    assert item["source_status"] == "revoked"

    with pytest.raises(RuntimeError, match="source_revoked"):
        store.mutate(
            "creator-a",
            descriptor.source_adapter_id,
            expected_version=item["version"],
            editor_instance_id="editor-one",
            source_status="ready",
        )


def test_idempotency_reservation_is_removed_when_side_effect_fails(tmp_path):
    store = cl.CreationLiveStore(tmp_path / "live.sqlite3")
    descriptor = _descriptor("cls_retry")
    store.upsert_discovered("creator-a", descriptor, "master", "output/master.wav")
    request = {"source": descriptor.source_adapter_id, "expected_version": 1}

    with pytest.raises(ValueError, match="transport failed"):
        store.idempotent(
            "creator-a",
            "attach",
            "operation-retry",
            descriptor.source_adapter_id,
            request,
            lambda: (_ for _ in ()).throw(ValueError("transport failed")),
        )

    calls: list[int] = []

    def succeed():
        calls.append(1)
        return {"ok": True}

    result = store.idempotent(
        "creator-a",
        "attach",
        "operation-retry",
        descriptor.source_adapter_id,
        request,
        succeed,
    )
    assert result == {"ok": True}
    assert calls == [1]


def test_concurrent_idempotency_reservation_does_not_execute_twice(tmp_path):
    store = cl.CreationLiveStore(tmp_path / "live.sqlite3")
    descriptor = _descriptor("cls_in_progress")
    store.upsert_discovered("creator-a", descriptor, "master", "output/master.wav")
    request = {"source": descriptor.source_adapter_id, "expected_version": 1}
    digest = _request_hash(request)
    with store.connect() as con:
        con.execute(
            "INSERT INTO creation_live_idempotency VALUES(?,?,?,?,?,?,?)",
            (
                "creator-a",
                "attach",
                "operation-concurrent",
                descriptor.source_adapter_id,
                digest,
                _IN_PROGRESS,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    calls: list[int] = []
    with pytest.raises(RuntimeError, match="operation_in_progress"):
        store.idempotent(
            "creator-a",
            "attach",
            "operation-concurrent",
            descriptor.source_adapter_id,
            request,
            lambda: calls.append(1),
        )
    assert calls == []


def test_missing_project_source_is_revoked_by_discovery_reconciliation(tmp_path):
    store = cl.CreationLiveStore(tmp_path / "live.sqlite3")
    first = _descriptor("cls_keep")
    second = _descriptor("cls_remove")
    store.upsert_discovered("creator-a", first, "first", "output/first.wav")
    store.upsert_discovered("creator-a", second, "second", "output/second.wav")

    rows = _revive_rediscovered_sources(
        store,
        "creator-a",
        "song-one",
        "music",
        [first.model_dump(mode="json")],
    )

    assert [row["source_adapter_id"] for row in rows] == ["cls_keep"]
    removed = store.get("creator-a", "cls_remove")
    assert removed["source_status"] == "revoked"
    assert removed["descriptor"]["health"] == "revoked"


def test_safe_rediscovery_reissues_revoked_source_without_old_session_linkage(tmp_path):
    store = cl.CreationLiveStore(tmp_path / "live.sqlite3")
    descriptor = _descriptor("cls_reissue")
    store.upsert_discovered("creator-a", descriptor, "master", "output/master.wav")
    attached = store.mutate(
        "creator-a",
        descriptor.source_adapter_id,
        expected_version=1,
        editor_instance_id="editor-one",
        source_status="registered",
        shared_sky_project_id="sky-project",
        broadcast_id="broadcast-old",
        transport_source_id="transport-old",
        active_editor_instance_id="editor-one",
    )
    revoked = store.mutate(
        "creator-a",
        descriptor.source_adapter_id,
        expected_version=attached["version"],
        editor_instance_id="editor-one",
        source_status="revoked",
        revoked_at=datetime.now(timezone.utc).isoformat(),
        descriptor_changes={"health": "revoked"},
    )
    assert revoked["source_status"] == "revoked"

    rows = _revive_rediscovered_sources(
        store,
        "creator-a",
        "song-one",
        "music",
        [descriptor.model_dump(mode="json")],
    )
    assert rows[0]["live_source_registration_state"] == "discovered"
    reissued = store.get("creator-a", descriptor.source_adapter_id)
    assert reissued["source_status"] == "discovered"
    assert reissued["broadcast_id"] is None
    assert reissued["transport_source_id"] is None
    assert reissued["active_editor_instance_id"] is None
    assert reissued["descriptor"]["revoked_at"] is None


def test_workspace_preview_replaces_old_capture_and_observes_track_end():
    assert "if(state.previewStream){state.previewStream.getTracks().forEach(t=>t.stop())" in cl.LIVE_UI_SCRIPT
    assert "state.previewStream===previewStream" in cl.LIVE_UI_SCRIPT
    assert "Workspace preview ended. It is no longer available for attachment." in cl.LIVE_UI_SCRIPT
