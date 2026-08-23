from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from aura_music_studio import __version__
from aura_music_studio.accounts import AccountStore
from aura_music_studio.compute_capabilities import compatibility, job_types_for_capabilities
from aura_music_studio.compute_nodes import ComputeNodeRegistry
from aura_music_studio.jobs import StudioJobQueue
from aura_music_studio.node_transfer import RESULT_ALLOWED_ROOT_FILES, extract_project_bundle


def _store(tmp_path: Path, monkeypatch) -> AccountStore:
    db = tmp_path / "studio.sqlite3"
    monkeypatch.setenv("LSS_DB_PATH", str(db))
    return AccountStore(db)


def test_compute_node_enrollment_is_single_use_and_secret_is_hashed(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    registry = ComputeNodeRegistry(store)
    enrollment = registry.create_enrollment(label="GPU 1", ttl_minutes=30)
    enrolled = registry.enroll(
        enrollment["token"],
        name="GPU 1",
        capabilities=["music_generation"],
        hardware={"gpu": "test"},
    )
    assert enrolled["node_secret"]
    authenticated = registry.authenticate(enrolled["node_id"], enrolled["node_secret"])
    assert authenticated["id"] == enrolled["node_id"]

    with sqlite3.connect(store.db_path) as con:
        row = con.execute("SELECT secret_hash FROM compute_nodes WHERE id=?", (enrolled["node_id"],)).fetchone()
    assert row is not None
    assert row[0] != enrolled["node_secret"]
    assert enrolled["node_secret"] not in row[0]

    with pytest.raises(PermissionError):
        registry.enroll(enrollment["token"], name="Replay", capabilities=["engineering"])


def test_revoked_node_credential_stops_authenticating(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    registry = ComputeNodeRegistry(store)
    token = registry.create_enrollment()["token"]
    node = registry.enroll(token, name="Node", capabilities=["engineering"])
    assert registry.revoke(node["node_id"])
    with pytest.raises(PermissionError):
        registry.authenticate(node["node_id"], node["node_secret"])


def test_capabilities_map_only_to_supported_job_types():
    assert set(job_types_for_capabilities(["music_generation"])) == {"produce", "build_around"}
    assert set(job_types_for_capabilities(["stem_separation"])) == {"engineering:split"}
    engineering = set(job_types_for_capabilities(["engineering"]))
    assert "engineering:master" in engineering
    assert "produce" not in engineering


def test_same_version_is_required_by_default(monkeypatch):
    monkeypatch.delenv("LSS_NODE_REQUIRE_SAME_VERSION", raising=False)
    good = compatibility({"software": {"live_sound_studio_version": __version__}})
    bad = compatibility({"software": {"live_sound_studio_version": "0.0.1"}})
    unknown = compatibility({"software": {}})
    assert good["compatible"] is True
    assert bad["compatible"] is False
    assert unknown["compatible"] is False


def test_remote_lease_claims_only_allowed_job_types_and_only_owner_can_renew(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    with sqlite3.connect(store.db_path) as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(users)").fetchall()}
        values = {
            "id": "u1",
            "email": "node-test@example.invalid",
            "email_normalized": "node-test@example.invalid",
            "display_name": "Node Test",
            "password_hash": "x",
            "password_salt": "x",
            "requested_plan_id": "pro",
            "plan_id": "pro",
            "status": "active",
            "billing_status": "active",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        keys = [key for key in values if key in columns]
        con.execute(
            f"INSERT INTO users ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
            tuple(values[key] for key in keys),
        )
    queue = StudioJobQueue(store)
    queue.submit("u1", "song-a", job_type="produce", priority=10)
    queue.submit("u1", "song-b", job_type="engineering:split", priority=20)
    leased = queue.claim_next_for_job_types("node:n1", ["produce"])
    assert leased is not None
    assert leased["job_type"] == "produce"
    assert leased["worker_id"] == "node:n1"
    assert queue.renew_owned(leased["id"], "node:n1")
    assert not queue.renew_owned(leased["id"], "node:other")
    assert queue.complete_owned(leased["id"], "node:n1", {"ok": True})
    assert not queue.complete_owned(leased["id"], "node:other", {"ok": False})


def test_node_results_cannot_replace_project_or_asset_ownership_metadata():
    assert "project.yaml" not in RESULT_ALLOWED_ROOT_FILES
    assert "project.json" not in RESULT_ALLOWED_ROOT_FILES
    assert "assets.json" not in RESULT_ALLOWED_ROOT_FILES


def test_project_bundle_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("LSS_NODE_MAX_BUNDLE_BYTES", str(64 * 1024 * 1024))
    archive = tmp_path / "bad.zip"
    manifest = {
        "format": "esp-node-job-v1",
        "job": {"id": "j", "job_type": "produce", "project_name": "p", "payload": {}},
        "files": [{"path": "../escape.txt", "sha256": "bad", "bytes": 3}],
    }
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("node_job.json", json.dumps(manifest))
        z.writestr("project/../escape.txt", b"bad")
    with pytest.raises(ValueError):
        extract_project_bundle(archive, tmp_path / "out")
