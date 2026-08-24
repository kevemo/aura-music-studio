from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from aura_music_studio.image_editing import ImageEditError, ImageEditRequest, ImageEditingService
from aura_music_studio.image_jobs import ImageJobStore


def _fake_result(job_id: str, output: Path, *, prompt: str = "image") -> dict:
    return {
        "id": job_id,
        "provider": "local",
        "model": "test",
        "status": "completed",
        "output_path": str(output),
        "request_json": json.dumps({"prompt": prompt}),
        "created_at": "2026-08-24T00:00:00+00:00",
        "error": None,
    }


def test_local_edit_creates_new_file_and_never_overwrites_source(tmp_path, monkeypatch):
    output_root = tmp_path / "images"
    output_root.mkdir()
    source = output_root / "source.png"
    original = b"original-image" * 64
    source.write_bytes(original)

    script = tmp_path / "copy_edit.py"
    script.write_text(
        "import os, pathlib\n"
        "src=pathlib.Path(os.environ['AURA_IMAGE_EDIT_SOURCE'])\n"
        "dst=pathlib.Path(os.environ['AURA_IMAGE_EDIT_OUTPUT'])\n"
        "dst.write_bytes(src.read_bytes()+b'-edited')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AURA_IMAGE_EDIT_CMD", f"{sys.executable} {script}")
    service = ImageEditingService(output_root)

    result = service.edit(
        source,
        ImageEditRequest(prompt="Make the gold glow brighter", provider="local", aspect_ratio="1:1"),
    )

    assert result.status == "completed"
    assert Path(result.output_path).resolve() != source.resolve()
    assert Path(result.output_path).read_bytes().endswith(b"-edited")
    assert source.read_bytes() == original


def test_image_edit_requires_real_owned_source_file(tmp_path):
    service = ImageEditingService(tmp_path / "images")
    with pytest.raises(ImageEditError, match="Source image is unavailable"):
        service.edit(tmp_path / "missing.png", ImageEditRequest(prompt="edit", provider="local"))


def test_image_edit_rejects_invalid_strength_and_provider(tmp_path, monkeypatch):
    root = tmp_path / "images"
    root.mkdir()
    source = root / "source.png"
    source.write_bytes(b"x" * 512)
    service = ImageEditingService(root)

    with pytest.raises(ImageEditError, match="edit_strength"):
        service.edit(source, ImageEditRequest(prompt="edit", provider="local", edit_strength=1.5))

    with pytest.raises(ImageEditError, match="Unknown image edit provider"):
        service.edit(source, ImageEditRequest(prompt="edit", provider="unknown"))


def test_lineage_is_saved_only_between_jobs_owned_by_same_user(tmp_path):
    db = tmp_path / "images.sqlite3"
    store = ImageJobStore(db)
    source = tmp_path / "source.png"
    child = tmp_path / "child.png"
    source.write_bytes(b"a" * 512)
    child.write_bytes(b"b" * 512)

    store.save(
        user_id="user-a",
        result=_fake_result("parent", source),
        mode="poster",
        prompt="first poster",
        project_id="project-a",
        provenance_hash="parent-hash",
    )
    store.save(
        user_id="user-a",
        result=_fake_result("child", child),
        mode="edit",
        prompt="change title glow",
        project_id="project-a",
        provenance_hash="child-hash",
    )
    lineage = store.save_edit_lineage(
        user_id="user-a",
        parent_job_id="parent",
        child_job_id="child",
        edit_prompt="change title glow",
        source_sha256="source-hash",
    )

    assert lineage["parent_job_id"] == "parent"
    assert lineage["child_job_id"] == "child"
    assert store.lineage_for_user("user-a", "child")["parent"]["parent_job_id"] == "parent"
    with pytest.raises(KeyError):
        store.lineage_for_user("user-b", "child")


def test_lineage_refuses_cross_tenant_parent_child(tmp_path):
    store = ImageJobStore(tmp_path / "images.sqlite3")
    source = tmp_path / "source.png"
    child = tmp_path / "child.png"
    source.write_bytes(b"a" * 512)
    child.write_bytes(b"b" * 512)
    store.save(
        user_id="user-a",
        result=_fake_result("parent", source),
        mode="image",
        prompt="parent",
        project_id=None,
        provenance_hash="p",
    )
    store.save(
        user_id="user-b",
        result=_fake_result("child", child),
        mode="edit",
        prompt="child",
        project_id=None,
        provenance_hash="c",
    )

    with pytest.raises(ValueError, match="user-owned"):
        store.save_edit_lineage(
            user_id="user-a",
            parent_job_id="parent",
            child_job_id="child",
            edit_prompt="forbidden cross-user edit",
            source_sha256="hash",
        )


def test_provenance_changes_when_source_changes(tmp_path):
    service = ImageEditingService(tmp_path / "images")
    request = ImageEditRequest(prompt="increase contrast")
    from aura_music_studio.image_editing import ImageEditResult

    result = ImageEditResult(
        id="edit-1",
        provider="local",
        model="test",
        status="completed",
        output_path="example.png",
        created_at="2026-08-24T00:00:00+00:00",
        request_json=json.dumps(request.__dict__, sort_keys=True),
    )
    assert service.provenance_hash(result, source_sha256="a") != service.provenance_hash(result, source_sha256="b")
