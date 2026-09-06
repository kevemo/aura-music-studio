from __future__ import annotations

from copy import deepcopy

STANDARD_EXPANDED_TYPES = frozenset({"bandpass", "notch", "low_shelf", "high_shelf"})
ADVANCED_EXPANDED_TYPES = frozenset({"expander", "denoise", "declick", "declip"})

EXPANDED_DEFAULTS: dict[str, dict[str, float]] = {
    "bandpass": {"frequency_hz": 1000.0, "width_octaves": 1.0},
    "notch": {"frequency_hz": 60.0, "width_octaves": 0.08},
    "low_shelf": {"gain_db": 0.0, "frequency_hz": 120.0, "width": 0.7},
    "high_shelf": {"gain_db": 0.0, "frequency_hz": 8500.0, "width": 0.6},
    "expander": {
        "threshold_db": -36.0,
        "ratio": 2.0,
        "attack_ms": 20.0,
        "release_ms": 220.0,
        "range_db": -18.0,
    },
    "denoise": {"reduction_db": 12.0, "noise_floor_db": -50.0},
    "declick": {
        "window_ms": 55.0,
        "overlap_percent": 75.0,
        "ar_order": 2.0,
        "threshold": 2.0,
        "burst": 2.0,
    },
    "declip": {
        "window_ms": 55.0,
        "overlap_percent": 75.0,
        "ar_order": 8.0,
        "threshold": 10.0,
        "histogram_size": 1000.0,
    },
}

EXPANDED_BOUNDS: dict[str, dict[str, tuple[float, float]]] = {
    "bandpass": {"frequency_hz": (20.0, 20000.0), "width_octaves": (0.05, 6.0)},
    "notch": {"frequency_hz": (20.0, 20000.0), "width_octaves": (0.01, 3.0)},
    "low_shelf": {"gain_db": (-18.0, 18.0), "frequency_hz": (20.0, 2000.0), "width": (0.1, 4.0)},
    "high_shelf": {"gain_db": (-18.0, 18.0), "frequency_hz": (1000.0, 20000.0), "width": (0.1, 4.0)},
    "expander": {
        "threshold_db": (-80.0, 0.0),
        "ratio": (1.0, 20.0),
        "attack_ms": (0.1, 500.0),
        "release_ms": (5.0, 3000.0),
        "range_db": (-60.0, 0.0),
    },
    "denoise": {"reduction_db": (0.01, 40.0), "noise_floor_db": (-80.0, -20.0)},
    "declick": {
        "window_ms": (10.0, 100.0),
        "overlap_percent": (50.0, 95.0),
        "ar_order": (0.0, 25.0),
        "threshold": (1.0, 100.0),
        "burst": (0.0, 10.0),
    },
    "declip": {
        "window_ms": (10.0, 100.0),
        "overlap_percent": (50.0, 95.0),
        "ar_order": (0.0, 25.0),
        "threshold": (1.0, 100.0),
        "histogram_size": (100.0, 9999.0),
    },
}

_installed = False


def _assert_compatible(existing: dict, additions: dict, *, registry_name: str) -> None:
    """Fail closed if another authority already owns a new effect id with different semantics."""
    for effect_type, value in additions.items():
        if effect_type in existing and existing[effect_type] != value:
            raise RuntimeError(
                f"Audio FX expansion collision for {effect_type!r} in {registry_name}; "
                "refusing to overwrite another processor contract"
            )


def install_audio_fx_expansion() -> None:
    """Extend the one canonical DAW FX Lab with bounded executable processors.

    This is registration only: execution remains in ``effects.compile_ffmpeg_chain`` and all
    mutations continue through the existing entitlement, revision and DAW-session endpoints.
    No arbitrary FFmpeg filter text, plugin binary, shell command or secondary effect router is
    introduced here.
    """
    global _installed
    if _installed:
        return

    from . import daw_fx_lab

    _assert_compatible(daw_fx_lab.DEFAULTS, EXPANDED_DEFAULTS, registry_name="DEFAULTS")
    _assert_compatible(daw_fx_lab.BOUNDS, EXPANDED_BOUNDS, registry_name="BOUNDS")

    daw_fx_lab.STANDARD_TYPES.update(STANDARD_EXPANDED_TYPES)
    daw_fx_lab.ADVANCED_TYPES.update(ADVANCED_EXPANDED_TYPES)
    for effect_type, defaults in EXPANDED_DEFAULTS.items():
        daw_fx_lab.DEFAULTS[effect_type] = deepcopy(defaults)
        daw_fx_lab.BOUNDS[effect_type] = deepcopy(EXPANDED_BOUNDS[effect_type])

    _installed = True


__all__ = [
    "ADVANCED_EXPANDED_TYPES",
    "EXPANDED_BOUNDS",
    "EXPANDED_DEFAULTS",
    "STANDARD_EXPANDED_TYPES",
    "install_audio_fx_expansion",
]
