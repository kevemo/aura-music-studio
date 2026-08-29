from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import aura_music_studio.engineering_jobs as engineering


def test_public_output_ref_is_project_relative(tmp_path: Path):
    project = tmp_path / "project"
    output = project / "output" / "masters" / "song.wav"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"audio")

    assert engineering._public_output_ref(project, output) == "output/masters/song.wav"


def test_public_output_ref_rejects_escape(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"audio")

    with pytest.raises(ValueError, match="outside the member project"):
        engineering._public_output_ref(project, outside)


def test_public_stem_refs_never_expose_host_paths(tmp_path: Path):
    project = tmp_path / "project"
    vocals = project / "work" / "separation" / "asset" / "vocals.wav"
    drums = project / "work" / "separation" / "asset" / "drums.wav"
    vocals.parent.mkdir(parents=True)
    vocals.write_bytes(b"v")
    drums.write_bytes(b"d")

    refs = engineering._public_stem_refs(project, {"vocals": vocals, "drums": drums})

    assert refs == {
        "vocals": "work/separation/asset/vocals.wav",
        "drums": "work/separation/asset/drums.wav",
    }
    assert all(not value.startswith(str(tmp_path)) for value in refs.values())


def test_master_job_returns_output_ref_not_absolute_output(monkeypatch, tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "assets" / "song.wav"
    source.parent.mkdir()
    source.write_bytes(b"source")
    record = SimpleNamespace(id="asset-1", name="song.wav", kind="audio", path="assets/song.wav")

    monkeypatch.setattr(
        engineering,
        "_audio",
        lambda _project, _asset_id: (SimpleNamespace(), record, source),
    )

    def fake_master(_source, output, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mastered")
        return output, {"ok": True}

    monkeypatch.setattr(engineering, "master", fake_master)
    monkeypatch.setattr(engineering, "translation_report", lambda _path: {"translation": "ok"})

    result = engineering.run_engineering_job(project, {"operation": "master", "asset_id": "asset-1"})

    assert result["output_ref"] == "output/masters/song_universal_AuraMaster.wav"
    assert "output" not in result
    assert str(tmp_path) not in result["output_ref"]


def test_split_job_returns_relative_stem_refs(monkeypatch, tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "assets" / "song.wav"
    source.parent.mkdir()
    source.write_bytes(b"source")
    record = SimpleNamespace(id="asset-1", name="song.wav", kind="audio", path="assets/song.wav")

    monkeypatch.setattr(
        engineering,
        "_audio",
        lambda _project, _asset_id: (SimpleNamespace(), record, source),
    )

    class FakeSeparator:
        def __init__(self, output_dir):
            self.output_dir = Path(output_dir)

        def separate(self, _source, *, mode):
            assert mode == "four_stems"
            self.output_dir.mkdir(parents=True, exist_ok=True)
            vocals = self.output_dir / "vocals.wav"
            instrumental = self.output_dir / "instrumental.wav"
            vocals.write_bytes(b"v")
            instrumental.write_bytes(b"i")
            return {"vocals": vocals, "instrumental": instrumental}

    monkeypatch.setattr(engineering, "StemSeparator", FakeSeparator)

    result = engineering.run_engineering_job(project, {"operation": "split", "asset_id": "asset-1"})

    assert result["stems"] == {
        "vocals": "work/separation/asset-1/vocals.wav",
        "instrumental": "work/separation/asset-1/instrumental.wav",
    }
    assert all(str(tmp_path) not in value for value in result["stems"].values())
