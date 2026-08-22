from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .session import Effect


def _db(value, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def compile_ffmpeg_chain(effects: list[Effect]) -> str:
    """Compile Aura track effects into a real-audio ffmpeg filter chain.

    This processes waveform audio only. MIDI/symbolic data never enters this render path.
    Unsupported/custom effects stay in session metadata until a configured plugin host renders them.
    """
    chain: list[str] = []
    for fx in effects:
        if not fx.enabled:
            continue
        p = fx.parameters
        if fx.type == "gain":
            chain.append(f"volume={_db(p.get('db'))}dB")
        elif fx.type == "eq":
            if p.get("low_db") is not None:
                chain.append(f"bass=g={_db(p.get('low_db'))}:f={float(p.get('low_hz', 120))}:w=0.7")
            if p.get("mid_db") is not None:
                chain.append(f"equalizer=f={float(p.get('mid_hz', 1800))}:width_type=o:width=1:g={_db(p.get('mid_db'))}")
            if p.get("high_db") is not None:
                chain.append(f"treble=g={_db(p.get('high_db'))}:f={float(p.get('high_hz', 8500))}:w=0.6")
        elif fx.type == "compressor":
            chain.append(
                "acompressor="
                f"threshold={float(p.get('threshold_db', -18))}dB:"
                f"ratio={float(p.get('ratio', 2.5))}:"
                f"attack={float(p.get('attack_ms', 15))}:"
                f"release={float(p.get('release_ms', 160))}"
            )
        elif fx.type == "limiter":
            limit = 10 ** (_db(p.get("ceiling_db", -1.0)) / 20.0)
            chain.append(f"alimiter=limit={limit:.5f}:attack={float(p.get('attack_ms', 5))}:release={float(p.get('release_ms', 50))}")
        elif fx.type == "gate":
            chain.append(f"agate=threshold={float(p.get('threshold_db', -45))}dB:ratio={float(p.get('ratio', 8))}:attack={float(p.get('attack_ms', 10))}:release={float(p.get('release_ms', 120))}")
        elif fx.type == "reverb":
            # Compact room-style convolution-free fallback.
            delay = int(float(p.get("predelay_ms", 30)))
            decay = max(0.05, min(float(p.get("mix", .18)), .8))
            chain.append(f"aecho=0.8:0.7:{delay}|{delay*2}:{decay}|{decay*.6}")
        elif fx.type == "delay":
            ms = int(float(p.get("delay_ms", 240)))
            feedback = max(0.0, min(float(p.get("feedback", .25)), .9))
            chain.append(f"aecho=0.8:0.7:{ms}:{feedback}")
        elif fx.type == "distortion":
            drive = max(1.0, float(p.get("drive", 2.0)))
            chain.append(f"asoftclip=type=tanh:threshold={1.0/drive:.4f}")
        elif fx.type == "pitch_shift":
            semitones = float(p.get("semitones", 0))
            ratio = 2 ** (semitones / 12.0)
            chain.append(f"asetrate=48000*{ratio:.8f},aresample=48000,atempo={1/ratio:.8f}")
        elif fx.type == "stereo_width":
            width = max(0.0, min(float(p.get("width", 1.0)), 2.0))
            chain.append(f"stereotools=mlev=1:slev={width}")
    return ",".join(chain)


def render_effects(source: Path, output: Path, effects: list[Effect], sample_rate: int = 48000) -> Path:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to render Aura effects")
    output.parent.mkdir(parents=True, exist_ok=True)
    chain = compile_ffmpeg_chain(effects)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source)]
    if chain:
        cmd += ["-af", chain]
    cmd += ["-c:a", "pcm_s24le", "-ar", str(sample_rate), str(output)]
    subprocess.run(cmd, check=True)
    return output
