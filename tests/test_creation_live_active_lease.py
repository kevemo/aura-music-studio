from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from aura_music_studio import creation_live as cl
from aura_music_studio.creation_live_hardening import install_creation_live_hardening


install_creation_live_hardening()


def _valid_descriptor(source_id: str):
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
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )


def _attach_then_age(store: cl.CreationLiveStore, source_id: str):
    """Model a valid source that later outlives its original discovery TTL."""
    descriptor = _valid_descriptor(source_id)
    store.upsert_discovered("creator-a", descriptor, "master", "output/master.wav")
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with store.connect() as con:
        row = con.execute(
            "SELECT descriptor_json FROM creation_live_sources WHERE user_id=? AND source_adapter_id=?",
            ("creator-a", source_id),
        ).fetchone()
        payload = json.loads(row["descriptor_json"])
        payload["expires_at"] = expired
        con.execute(
            """
            UPDATE creation_live_sources
               SET source_status='registered',broadcast_id='broadcast-a',active_editor_instance_id='editor-a',
                   descriptor_json=?
             WHERE user_id='creator-a' AND source_adapter_id=?
            """,
            (json.dumps(payload, separators=(",", ":")), source_id),
        )
    return descriptor


def test_active_broadcast_becomes_source_lease_even_after_discovery_ttl(monkeypatch, tmp_path):
    monkeypatch.setattr(cl.shared_sky, "broadcast", lambda user_id, broadcast_id: {"id": broadcast_id, "state": "live"})
    store = cl.CreationLiveStore(tmp_path / "live.sqlite3")
    descriptor = _attach_then_age(store, "cls_live_lease")

    item = store.get("creator-a", descriptor.source_adapter_id)

    assert item["source_status"] == "registered"
    assert item["broadcast_id"] == "broadcast-a"
    assert item["descriptor"].get("revoked_at") is None


def test_pre_live_draft_does_not_bypass_expired_discovery_handle(monkeypatch, tmp_path):
    monkeypatch.setattr(cl.shared_sky, "broadcast", lambda user_id, broadcast_id: {"id": broadcast_id, "state": "draft"})
    store = cl.CreationLiveStore(tmp_path / "live.sqlite3")
    descriptor = _attach_then_age(store, "cls_draft_expired")

    item = store.get("creator-a", descriptor.source_adapter_id)

    assert item["source_status"] == "revoked"
    assert item["descriptor"]["health"] == "revoked"


def test_terminal_broadcast_revokes_source_even_when_session_was_lease(monkeypatch, tmp_path):
    monkeypatch.setattr(cl.shared_sky, "broadcast", lambda user_id, broadcast_id: {"id": broadcast_id, "state": "ended"})
    store = cl.CreationLiveStore(tmp_path / "live.sqlite3")
    descriptor = _attach_then_age(store, "cls_ended_lease")

    item = store.get("creator-a", descriptor.source_adapter_id)

    assert item["source_status"] == "revoked"
    assert item["active_editor_instance_id"] is None
    assert item["descriptor"]["revoked_at"]
