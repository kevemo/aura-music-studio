from __future__ import annotations

import json

import numpy as np
import pytest
import soundfile as sf
from pydantic import ValidationError

from aura_music_studio.executable_audio_effects import (
    AudioEffectGraph,
    AudioEffectNode,
    load_effect_preset,
    render_audio_effect_graph,
    save_effect_preset,
)


def _tone(path, *, sample_rate: int = 24000, seconds: float = 0.25, hz: float = 440.0):
    frames = int(sample_rate * seconds)
    t = np.arange(frames, dtype=np.float32) / sample_rate
    audio = (0.25 * np.sin(2.0 * np.pi * hz * t)).astype(np.float32)
    sf.write(path, audio, sample_rate, subtype="FLOAT")
    return audio, sample_rate


def test_real_audio_effect_graph_renders_and_changes_samples(tmp_path):
    source = tmp_path / "source.wav"
    destination = tmp_path / "rendered.wav"
    original, sample_rate = _tone(source)
    graph = AudioEffectGraph(
        name="Clean vocal chain",
        nodes=[
            AudioEffectNode(kind="gain", gain_db=-6.0),
            AudioEffectNode(kind="fade_in", duration_seconds=0.03),
            AudioEffectNode(kind="normalize_peak", peak_dbfs=-3.0),
        ],
    )

    evidence = render_audio_effect_graph(source, destination, graph)
    rendered, rendered_rate = sf.read(destination, dtype="float32")

    assert destination.is_file()
    assert rendered_rate == sample_rate
    assert rendered.shape == original.shape
    assert not np.allclose(rendered, original)
    assert evidence["rendered"] is True
    assert evidence["audio_origin"] == "local_allowlisted_dsp"
    assert evidence["arbitrary_code_execution"] is False
    assert evidence["network_access"] is False
    assert evidence["effect_graph_fingerprint"] == graph.fingerprint()


def test_filter_graph_is_executable_not_schema_only(tmp_path):
    source = tmp_path / "source.wav"
    destination = tmp_path / "filtered.wav"
    _tone(source, hz=120.0)
    graph = AudioEffectGraph(nodes=[AudioEffectNode(kind="high_pass", cutoff_hz=1000.0)])

    render_audio_effect_graph(source, destination, graph)
    before, _ = sf.read(source, dtype="float32")
    after, _ = sf.read(destination, dtype="float32")

    assert float(np.sqrt(np.mean(after**2))) < float(np.sqrt(np.mean(before**2))) * 0.5


def test_mix_zero_is_non_destructive(tmp_path):
    source = tmp_path / "source.wav"
    destination = tmp_path / "rendered.wav"
    original, _ = _tone(source)
    graph = AudioEffectGraph(nodes=[AudioEffectNode(kind="gain", gain_db=24.0, mix=0.0)])

    render_audio_effect_graph(source, destination, graph)
    rendered, _ = sf.read(destination, dtype="float32")

    assert np.allclose(rendered, original, atol=1e-6)


def test_unknown_effect_kind_fails_closed():
    with pytest.raises(ValidationError):
        AudioEffectNode.model_validate({"kind": "shell", "command": "rm -rf /"})


def test_graph_caps_node_count():
    nodes = [AudioEffectNode(kind="gain") for _ in range(17)]
    with pytest.raises(ValidationError):
        AudioEffectGraph(nodes=nodes)


def test_filter_rejects_cutoff_above_runtime_nyquist(tmp_path):
    source = tmp_path / "source.wav"
    destination = tmp_path / "filtered.wav"
    _tone(source, sample_rate=16000)
    graph = AudioEffectGraph(nodes=[AudioEffectNode(kind="low_pass", cutoff_hz=12000.0)])

    with pytest.raises(ValueError, match="Nyquist"):
        render_audio_effect_graph(source, destination, graph)


def test_reusable_preset_roundtrip_and_defensive_validation(tmp_path):
    presets = tmp_path / "presets"
    graph = AudioEffectGraph(
        name="Podcast clean",
        nodes=[
            AudioEffectNode(kind="high_pass", cutoff_hz=90.0),
            AudioEffectNode(kind="normalize_peak", peak_dbfs=-1.0),
        ],
    )

    target = save_effect_preset(presets, "podcast-clean", graph)
    loaded = load_effect_preset(presets, "podcast-clean")

    assert target.parent == presets.resolve()
    assert loaded == graph
    assert loaded.fingerprint() == graph.fingerprint()
    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == 1


@pytest.mark.parametrize("name", ["../escape", "nested/preset", "bad name", "", "x" * 81])
def test_preset_names_reject_path_traversal_and_unsafe_names(tmp_path, name):
    with pytest.raises(ValueError):
        save_effect_preset(tmp_path / "presets", name, AudioEffectGraph())


def test_unsupported_source_and_output_formats_fail_closed(tmp_path):
    source = tmp_path / "source.mp3"
    source.write_bytes(b"not audio")
    with pytest.raises(ValueError, match="WAV, FLAC or OGG"):
        render_audio_effect_graph(source, tmp_path / "out.wav", AudioEffectGraph())

    valid = tmp_path / "source.wav"
    _tone(valid)
    with pytest.raises(ValueError, match="output"):
        render_audio_effect_graph(valid, tmp_path / "out.mp3", AudioEffectGraph())
