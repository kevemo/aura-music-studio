from __future__ import annotations

from aura_music_studio import creation_live as cl
from aura_music_studio.creation_live_hardening import (
    _revive_rediscovered_sources,
    install_creation_live_hardening,
)


install_creation_live_hardening()


def _descriptor(source_id: str, *, rights_state: str = "ready") -> cl.CreationLiveSourceDescriptor:
    return cl.CreationLiveSourceDescriptor(
        source_adapter_id=source_id,
        studio_type="image_visual",
        project_id="art-one",
        workspace_id="creator-a",
        creator_id="creator-a",
        source_type="clean_artwork",
        safe_display_name="Approved artwork",
        media_kind="still-or-slideshow",
        capabilities=cl.SourceCapabilities(still=True),
        privacy_classification="project_safe_output",
        inclusion_manifest=["approved project output"],
        rights=cl.RightsPreflight(
            state=rights_state,
            codes=["project_rights_blocked"] if rights_state == "blocked" else [],
            messages=["Broadcast permission was revoked."] if rights_state == "blocked" else [],
        ),
    )


def test_existing_source_is_revoked_when_fresh_discovery_becomes_rights_blocked(monkeypatch, tmp_path):
    monkeypatch.setattr(cl.shared_sky, "broadcast", lambda user_id, broadcast_id: {"id": broadcast_id, "state": "live"})
    store = cl.CreationLiveStore(tmp_path / "live.sqlite3")
    initial = _descriptor("cls_rights_change")
    store.upsert_discovered("creator-a", initial, "artwork", "output/art.png")
    attached = store.mutate(
        "creator-a",
        initial.source_adapter_id,
        expected_version=1,
        editor_instance_id="editor-a",
        source_status="registered",
        shared_sky_project_id="sky-project",
        broadcast_id="broadcast-a",
        transport_source_id="src-a",
        active_editor_instance_id="editor-a",
    )
    assert attached["source_status"] == "registered"

    blocked = _descriptor("cls_rights_change", rights_state="blocked").model_dump(mode="json")
    rows = _revive_rediscovered_sources(
        store,
        "creator-a",
        "art-one",
        "image_visual",
        [blocked],
    )

    assert rows[0]["rights"]["state"] == "blocked"
    assert rows[0]["live_source_registration_state"] == "revoked"
    current = store.get("creator-a", "cls_rights_change")
    assert current["source_status"] == "revoked"
    assert current["active_editor_instance_id"] is None
    assert current["descriptor"]["health"] == "revoked"
    assert current["descriptor"]["revoked_at"]
    # Retain provenance of what session/source was affected; safe rediscovery clears it later.
    assert current["broadcast_id"] == "broadcast-a"
    assert current["transport_source_id"] == "src-a"


def test_rights_restoration_reissues_source_without_stale_live_linkage(tmp_path):
    store = cl.CreationLiveStore(tmp_path / "live.sqlite3")
    blocked = _descriptor("cls_rights_restore", rights_state="blocked")
    store.upsert_discovered("creator-a", blocked, "artwork", "output/art.png")
    with store.connect() as con:
        con.execute(
            """
            UPDATE creation_live_sources
               SET source_status='revoked',broadcast_id='broadcast-old',transport_source_id='src-old',
                   shared_sky_project_id='sky-old',active_editor_instance_id='editor-old'
             WHERE user_id='creator-a' AND source_adapter_id='cls_rights_restore'
            """
        )

    restored = _descriptor("cls_rights_restore", rights_state="ready").model_dump(mode="json")
    rows = _revive_rediscovered_sources(
        store,
        "creator-a",
        "art-one",
        "image_visual",
        [restored],
    )

    assert rows[0]["rights"]["state"] == "ready"
    current = store.get("creator-a", "cls_rights_restore")
    assert current["source_status"] == "discovered"
    assert current["broadcast_id"] is None
    assert current["transport_source_id"] is None
    assert current["shared_sky_project_id"] is None
    assert current["active_editor_instance_id"] is None
    assert current["descriptor"]["revoked_at"] is None
