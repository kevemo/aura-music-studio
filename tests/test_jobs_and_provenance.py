from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from aura_music_studio.accounts import AccountStore
from aura_music_studio.jobs import StudioJobQueue
from aura_music_studio.project import ProjectWorkspace
from aura_music_studio.provenance import build_provenance, write_provenance


def _member(store: AccountStore, email: str) -> str:
    signup = store.signup(email, "Queue Test", "very-secure-password", "free")
    store.decide_membership(signup.approval_token, "approve", "ESP Test")
    return signup.user_id


def test_queue_claims_highest_priority_first(tmp_path):
    store = AccountStore(tmp_path / "studio.sqlite3")
    low = _member(store, "low@example.com")
    high = _member(store, "high@example.com")
    queue = StudioJobQueue(store)
    low_job = queue.submit(low, "low-song", priority=20)
    high_job = queue.submit(high, "pro-song", priority=100)

    claimed = queue.claim_next("worker-1")
    assert claimed is not None
    assert claimed["id"] == high_job["id"]
    assert claimed["priority"] == 100
    assert queue.get(low_job["id"])["status"] == "queued"


def test_queue_deduplicates_active_project_job(tmp_path):
    store = AccountStore(tmp_path / "studio.sqlite3")
    user = _member(store, "member@example.com")
    queue = StudioJobQueue(store)
    first = queue.submit(user, "song", priority=20)
    second = queue.submit(user, "song", priority=20)
    assert second["id"] == first["id"]
    assert queue.summary()["counts"]["queued"] == 1


def test_stale_running_job_is_requeued(tmp_path):
    store = AccountStore(tmp_path / "studio.sqlite3")
    user = _member(store, "stale@example.com")
    queue = StudioJobQueue(store)
    job = queue.submit(user, "song", priority=20)
    claimed = queue.claim_next("dead-worker")
    assert claimed and claimed["status"] == "running"

    old = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    with queue._connect() as con:
        con.execute("UPDATE studio_jobs SET started_at=? WHERE id=?", (old, job["id"]))

    changed = queue.requeue_stale(stale_after_seconds=60)
    assert changed == 1
    recovered = queue.get(job["id"])
    assert recovered["status"] == "queued"
    assert recovered["worker_id"] is None


def test_provenance_hashes_outputs_and_can_sign(tmp_path, monkeypatch):
    workspace = ProjectWorkspace(tmp_path / "project")
    audio = workspace.output_dir / "master.wav"
    audio.write_bytes(b"RIFF-test-real-audio-bytes")
    (workspace.root / "assets.json").write_text(
        json.dumps([
            {
                "id": "asset1",
                "name": "input.wav",
                "kind": "audio",
                "sha256": "abc123",
                "rights_record_id": "rights1",
                "tags": ["user-owned"],
            }
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("LSS_PROVENANCE_SECRET", "test-secret")
    record = build_provenance(
        workspace,
        manifest={"project_name": "p", "title": "Song", "mode": "original", "rights_confirmed": True},
        renderer="acestep_api",
        renderer_metadata={"model": "acestep-v15-xl-turbo"},
        audio_origin="neural",
        quality_control={"passes_basic_integrity": True},
        exports={"master_wav": str(audio)},
    )
    assert record["integrity"]["signed"] is True
    assert len(record["integrity"]["canonical_sha256"]) == 64
    assert len(record["integrity"]["hmac_sha256"]) == 64
    assert record["outputs"][0]["filename"] == "master.wav"
    target = write_provenance(workspace, record)
    assert target.exists()
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["generation"]["renderer"] == "acestep_api"
