from __future__ import annotations

import math
import shutil
import struct
import wave

import pytest
import soundfile as sf

from aura_music_studio.audio_fx_expansion import (
    ADVANCED_EXPANDED_TYPES,
    EXPANDED_DEFAULTS,
    STANDARD_EXPANDED_TYPES,
    install_audio_fx_expansion,
)
from aura_music_studio.daw_fx_lab import ADVANCED_TYPES, BOUNDS, DEFAULTS, STANDARD_TYPES, normalize_parameters
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


def test_expanded_fx_install_into_canonical_lab_idempotently():
    install_audio_fx_expansion()
    assert STANDARD_EXPANDED_TYPES <= STANDARD_TYPES
    assert ADVANCED_EXPANDED_TYPES <= ADVANCED_TYPES
    for kind, defaults in EXPANDED_DEFAULTS.items():
        assert DEFAULTS[kind] == defaults
        assert kind in BOUNDS


def test_expanded_fx_parameters_are_bounded_by_existing_normalizer():
    bandpass = normalize_parameters("bandpass", {"frequency_hz": 999999, "width_octaves": -5})
    assert bandpass == {"frequency_hz": 20000.0, "width_octaves": 0.05}

    notch = normalize_parameters("notch", {"frequency_hz": -10, "width_octaves": 99})
    assert notch == {"frequency_hz": 20.0, "width_octaves": 3.0}

    expander = normalize_parameters(
        "expander",
        {"threshold_db": -999, "ratio": 999, "attack_ms": 0, "release_ms": 99999, "range_db": -999},
    )
    assert expander == {
        "threshold_db": -80.0,
        "ratio": 20.0,
        "attack_ms": 0.1,
        "release_ms": 3000.0,
        "range_db": -60.0,
    }


def test_every_expanded_fx_is_typed_and_compiles_to_real_ffmpeg_filter():
    chains = {}
    for kind in sorted(STANDARD_EXPANDED_TYPES | ADVANCED_EXPANDED_TYPES):
        params = normalize_parameters(kind, {})
        effect = Effect(type=kind, parameters=params)
        chain = compile_ffmpeg_chain([effect])
        assert chain
        chains[kind] = chain

    assert chains["bandpass"].startswith("bandpass=")
    assert chains["notch"].startswith("bandreject=")
    assert chains["low_shelf"].startswith("bass=")
    assert chains["high_shelf"].startswith("treble=")
    assert chains["expander"].startswith("agate=")
    assert ":range=" in chains["expander"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required for executable FX render smoke")
def test_expanded_fx_chain_renders_real_waveform(tmp_path):
    source = tmp_path / "source.wav"
    output = tmp_path / "processed.wav"
    _write_test_tone(source)

    effects = [
        Effect(type="bandpass", parameters=normalize_parameters("bandpass", {"frequency_hz": 1200, "width_octaves": 2.5})),
        Effect(type="notch", parameters=normalize_parameters("notch", {"frequency_hz": 60, "width_octaves": 0.1})),
        Effect(type="low_shelf", parameters=normalize_parameters("low_shelf", {"gain_db": 1.5, "frequency_hz": 140})),
        Effect(type="high_shelf", parameters=normalize_parameters("high_shelf", {"gain_db": -1.0, "frequency_hz": 9000})),
        Effect(type="expander", parameters=normalize_parameters("expander", {"threshold_db": -45, "ratio": 1.8, "range_db": -12})),
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
