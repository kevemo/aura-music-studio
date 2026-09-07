from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

import aura_music_studio.aura_effect_system_render as render
from aura_music_studio.aura_effect_system_creator import EffectNodeSpec, make_effect_system
from aura_music_studio.session import StudioSession


class StubEntitlements:
    def has_entitlement(self, user_id: str, effect_id: str) -> dict:
        return {
            "effect_id": effect_id,
            "owned": True,
            "included": True,
            "entitlement_band": "core",
            "coin_price": 0,
            "source": "test",
        }


def _project(tmp_path):
    project = tmp_path / "song"
    project.mkdir()
    session = StudioSession(name="song")
    track = session.add_track("Lead Vocal", "vocals")
    session.save(project / "aura_session.json")
    source = project / "uploads" / "vocal.wav"
    source.parent.mkdir()
    source.write_bytes(b"source-audio")
    return project, track.id, source


def _system():
    return make_effect_system(
        "aura.preview.chain",
        "Aura Preview Chain",
        [EffectNodeSpec(id="gain", catalogue_item_id="music.fx.gain", parameters={"db": 2.0})],
    )


def _use_server_executable(monkeypatch):
    monkeypatch.setattr(render.shutil, "which", lambda name: sys.executable if name == "ffmpeg" else None)


def test_render_preview_uses_server_controlled_argv_and_bounded_output(tmp_path, monkeypatch):
    project, track_id, source = _project(tmp_path)
    _use_server_executable(monkeypatch)
    observed = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = list(argv)
        observed["kwargs"] = dict(kwargs)
        output = render.Path(argv[-1])
        output.write_bytes(b"preview-audio")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(render.subprocess, "run", fake_run)
    before_source = source.read_bytes()
    before_session = (project / "aura_session.json").read_bytes()

    result = render.render_effect_system_preview(
        project,
        track_id,
        _system(),
        user_id="member-1",
        source_media_path="uploads/vocal.wav",
        entitlement_store=StubEntitlements(),
    )

    assert result["rendered"] is True
    assert result["source_media_mutated"] is False
    assert result["project_metadata_mutated"] is False
    assert result["server_selected_executable"] is True
    assert result["client_supplied_command_authority"] is False
    assert result["shell_execution"] is False
    assert result["resource_budget"]["render_seconds_max"] == render.MAX_EFFECT_PREVIEW_SECONDS
    assert result["preview_audio_project_relative_path"].startswith("work/effect_system_preview_audio/")
    assert (project / result["preview_audio_project_relative_path"]).read_bytes() == b"preview-audio"
    assert source.read_bytes() == before_source
    assert (project / "aura_session.json").read_bytes() == before_session

    assert observed["argv"][0] == str(render.Path(sys.executable).resolve())
    assert observed["argv"][observed["argv"].index("-i") + 1] == str(source.resolve())
    assert observed["argv"][observed["argv"].index("-t") + 1] == str(render.MAX_EFFECT_PREVIEW_SECONDS)
    assert "volume=2.0dB" in observed["argv"][observed["argv"].index("-af") + 1]
    assert observed["kwargs"]["shell"] is False
    assert observed["kwargs"]["timeout"] == render.EFFECT_PREVIEW_RENDER_TIMEOUT_SECONDS


def test_render_preview_rejects_source_outside_project_before_spawn(tmp_path, monkeypatch):
    project, track_id, _source = _project(tmp_path)
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"outside")
    _use_server_executable(monkeypatch)
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(render.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="inside the project"):
        render.render_effect_system_preview(
            project,
            track_id,
            _system(),
            user_id="member-1",
            source_media_path=outside,
            entitlement_store=StubEntitlements(),
        )
    assert called is False


def test_render_preview_rejects_oversized_source_before_spawn(tmp_path, monkeypatch):
    project, track_id, source = _project(tmp_path)
    monkeypatch.setattr(render, "MAX_EFFECT_PREVIEW_SOURCE_BYTES", 4)
    _use_server_executable(monkeypatch)
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(render.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="source exceeds"):
        render.render_effect_system_preview(
            project,
            track_id,
            _system(),
            user_id="member-1",
            source_media_path=source,
            entitlement_store=StubEntitlements(),
        )
    assert called is False


def test_render_preview_timeout_fails_closed_and_cleans_partial_output(tmp_path, monkeypatch):
    project, track_id, source = _project(tmp_path)
    _use_server_executable(monkeypatch)

    def fake_run(argv, **kwargs):
        render.Path(argv[-1]).write_bytes(b"partial")
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs["timeout"])

    monkeypatch.setattr(render.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="time budget"):
        render.render_effect_system_preview(
            project,
            track_id,
            _system(),
            user_id="member-1",
            source_media_path=source,
            entitlement_store=StubEntitlements(),
        )
    root = project / "work" / "effect_system_preview_audio"
    assert not list(root.glob("*.tmp"))
    assert not list(root.glob("*.wav"))


def test_render_preview_rejects_oversized_output_and_cleans_it(tmp_path, monkeypatch):
    project, track_id, source = _project(tmp_path)
    monkeypatch.setattr(render, "MAX_EFFECT_PREVIEW_OUTPUT_BYTES", 4)
    _use_server_executable(monkeypatch)

    def fake_run(argv, **kwargs):
        render.Path(argv[-1]).write_bytes(b"12345")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(render.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="output exceeds"):
        render.render_effect_system_preview(
            project,
            track_id,
            _system(),
            user_id="member-1",
            source_media_path=source,
            entitlement_store=StubEntitlements(),
        )
    root = project / "work" / "effect_system_preview_audio"
    assert not list(root.glob("*.tmp"))
    assert not list(root.glob("*.wav"))


def test_render_preview_nonzero_exit_fails_closed_and_cleans_partial_output(tmp_path, monkeypatch):
    project, track_id, source = _project(tmp_path)
    _use_server_executable(monkeypatch)

    def fake_run(argv, **kwargs):
        render.Path(argv[-1]).write_bytes(b"partial")
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(render.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="render failed"):
        render.render_effect_system_preview(
            project,
            track_id,
            _system(),
            user_id="member-1",
            source_media_path=source,
            entitlement_store=StubEntitlements(),
        )
    root = project / "work" / "effect_system_preview_audio"
    assert not list(root.glob("*.tmp"))
    assert not list(root.glob("*.wav"))
