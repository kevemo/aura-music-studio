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


def test_register_audio_asset_confines_before_ingest(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"audio")

    class NeverIngest:
        def ingest(self, *_args, **_kwargs):
            raise AssertionError("ingest must not run for an escaped output")

    with pytest.raises(ValueError, match="outside the member project"):
        engineering._register_audio_asset(
            project,
            NeverIngest(),
            outside,
            operation="master",
            source_asset_id="source-1",
        )


def test_register_audio_asset_returns_project_asset_reference(tmp_path: Path):
    project = tmp_path / "project"
    output = project / "output" / "masters" / "song.wav"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"audio")
    ingested = project / "input" / "assets" / "registered.wav"
    ingested.parent.mkdir(parents=True)
    ingested.write_bytes(b"registered")
    calls = []

    class FakeLibrary:
        def ingest(self, source, **kwargs):
            calls.append((Path(source), kwargs))
            return SimpleNamespace(id="generated-1", path="input/assets/registered.wav")

    result = engineering._register_audio_asset(
        project,
        FakeLibrary(),
        output,
        operation="master",
        source_asset_id="source-1",
    )

    assert result == {
        "asset_id": "generated-1",
        "asset_ref": "input/assets/registered.wav",
    }
    assert calls[0][0] == output
    assert calls[0][1]["kind"] == "audio"
    assert calls[0][1]["rights_basis"] == "project_generated_derivative"
    assert "operation:master" in calls[0][1]["tags"]
    assert "source-1" in calls[0][1]["notes"]


def test_register_stem_assets_tags_each_role(monkeypatch, tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    vocals = project / "work" / "vocals.wav"
    drums = project / "work" / "drums.wav"
    vocals.parent.mkdir()
    vocals.write_bytes(b"v")
    drums.write_bytes(b"d")
    calls = []

    def fake_register(_project, _library, path, *, operation, source_asset_id, role=None):
        calls.append((Path(path).name, operation, source_asset_id, role))
        return {"asset_id": f"asset-{role}", "asset_ref": f"input/assets/{role}.wav"}

    monkeypatch.setattr(engineering, "_register_audio_asset", fake_register)
    assets = engineering._register_stem_assets(
        project,
        object(),
        {"vocals": vocals, "drums": drums},
        source_asset_id="source-1",
    )

    assert assets["vocals"]["asset_id"] == "asset-vocals"
    assert assets["drums"]["asset_id"] == "asset-drums"
    assert ("vocals.wav", "split", "source-1", "vocals") in calls
    assert ("drums.wav", "split", "source-1", "drums") in calls


def test_master_job_returns_output_ref_and_reusable_asset(monkeypatch, tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "assets" / "song.wav"
    source.parent.mkdir()
    source.write_bytes(b"source")
    record = SimpleNamespace(id="asset-1", name="song.wav", kind="audio", path="assets/song.wav")
    library = SimpleNamespace()

    monkeypatch.setattr(
        engineering,
        "_audio",
        lambda _project, _asset_id: (library, record, source),
    )

    def fake_master(_source, output, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mastered")
        return output, {"ok": True}

    monkeypatch.setattr(engineering, "master", fake_master)
    monkeypatch.setattr(engineering, "translation_report", lambda _path: {"translation": "ok"})
    monkeypatch.setattr(
        engineering,
        "_register_audio_asset",
        lambda *_args, **_kwargs: {"asset_id": "master-asset", "asset_ref": "input/assets/master.wav"},
    )

    result = engineering.run_engineering_job(project, {"operation": "master", "asset_id": "asset-1"})

    assert result["output_ref"] == "output/masters/song_universal_AuraMaster.wav"
    assert result["asset"] == {"asset_id": "master-asset", "asset_ref": "input/assets/master.wav"}
    assert "output" not in result
    assert str(tmp_path) not in result["output_ref"]


def test_split_job_returns_relative_stem_refs_and_assets(monkeypatch, tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "assets" / "song.wav"
    source.parent.mkdir()
    source.write_bytes(b"source")
    record = SimpleNamespace(id="asset-1", name="song.wav", kind="audio", path="assets/song.wav")
    library = SimpleNamespace()

    monkeypatch.setattr(
        engineering,
        "_audio",
        lambda _project, _asset_id: (library, record, source),
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
    monkeypatch.setattr(
        engineering,
        "_register_stem_assets",
        lambda *_args, **_kwargs: {
            "vocals": {"asset_id": "stem-v", "asset_ref": "input/assets/vocals.wav"},
            "instrumental": {"asset_id": "stem-i", "asset_ref": "input/assets/instrumental.wav"},
        },
    )

    result = engineering.run_engineering_job(project, {"operation": "split", "asset_id": "asset-1"})

    assert result["stems"] == {
        "vocals": "work/separation/asset-1/vocals.wav",
        "instrumental": "work/separation/asset-1/instrumental.wav",
    }
    assert result["stem_assets"]["vocals"]["asset_id"] == "stem-v"
    assert result["stem_assets"]["instrumental"]["asset_id"] == "stem-i"
    assert all(str(tmp_path) not in value for value in result["stems"].values())
