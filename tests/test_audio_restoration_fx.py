from __future__ import annotations

import math
import shutil
import struct
import wave

import pytest
import soundfile as sf

from aura_music_studio.audio_fx_expansion import install_audio_fx_expansion
from aura_music_studio.daw_fx_lab import ADVANCED_TYPES, BOUNDS, DEFAULTS, normalize_parameters
from aura_music_studio.effects import compile_ffmpeg_chain, render_effects
from aura_music_studio.session import Effect


install_audio_fx_expansion()


def _write_test_tone(path, *, sample_rate: int = 48000, duration_seconds: float = 0.25) -> None:
    frames = max(1, int(sample_rate * duration_seconds))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        payload = bytearray()
        for index in range(frames):
            sample = int(12000 * math.sin(2.0 * math.pi * 440.0 * index / sample_rate))
            payload.extend(struct.pack("<h", sample))
        handle.writeframes(bytes(payload))


def test_restoration_processors_register_as_advanced_executable_fx():
    install_audio_fx_expansion()
    assert {"denoise", "declick", "declip"} <= ADVANCED_TYPES
    for kind in ("denoise", "declick", "declip"):
        assert kind in DEFAULTS
        assert kind in BOUNDS
        effect = Effect(type=kind, parameters=normalize_parameters(kind, {}))
        assert compile_ffmpeg_chain([effect])


def test_restoration_parameters_are_bounded_by_canonical_normalizer():
    denoise = normalize_parameters("denoise", {"reduction_db": 999, "noise_floor_db": -999})
    assert denoise == {"reduction_db": 40.0, "noise_floor_db": -80.0}

    declick = normalize_parameters(
        "declick",
        {"window_ms": 1, "overlap_percent": 999, "ar_order": 999, "threshold": 0, "burst": 999},
    )
    assert declick == {
        "window_ms": 10.0,
        "overlap_percent": 95.0,
        "ar_order": 25.0,
        "threshold": 1.0,
        "burst": 10.0,
    }

    declip = normalize_parameters(
        "declip",
        {"window_ms": 999, "overlap_percent": 0, "ar_order": -5, "threshold": 999, "histogram_size": 99999},
    )
    assert declip == {
        "window_ms": 100.0,
        "overlap_percent": 50.0,
        "ar_order": 0.0,
        "threshold": 100.0,
        "histogram_size": 9999.0,
    }


def test_restoration_compilers_use_bounded_stock_ffmpeg_filters():
    denoise = compile_ffmpeg_chain([Effect(type="denoise", parameters=normalize_parameters("denoise", {}))])
    assert denoise == "afftdn=nr=12.000:nf=-50.000"

    declick = compile_ffmpeg_chain([Effect(type="declick", parameters=normalize_parameters("declick", {}))])
    assert declick == "adeclick=window=55.000:overlap=75.000:arorder=2.000:threshold=2.000:burst=2.000"

    declip = compile_ffmpeg_chain([Effect(type="declip", parameters=normalize_parameters("declip", {}))])
    assert declip == "adeclip=window=55.000:overlap=75.000:arorder=8.000:threshold=10.000:hsize=1000"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required for executable restoration FX smoke")
def test_restoration_chain_renders_real_waveform(tmp_path):
    source = tmp_path / "source.wav"
    output = tmp_path / "restored.wav"
    _write_test_tone(source)

    effects = [
        Effect(type="denoise", parameters=normalize_parameters("denoise", {"reduction_db": 6, "noise_floor_db": -55})),
        Effect(type="declick", parameters=normalize_parameters("declick", {"threshold": 3, "burst": 1})),
        Effect(type="declip", parameters=normalize_parameters("declip", {"threshold": 12, "histogram_size": 1200})),
    ]

    rendered = render_effects(source, output, effects)
    assert rendered == output
    assert output.is_file()
    assert output.stat().st_size > 1000

    info = sf.info(str(output))
    assert info.samplerate == 48000
    assert info.frames > 0
    assert info.channels >= 1
    assert info.subtype == "PCM_24"
