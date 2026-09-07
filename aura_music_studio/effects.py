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


def _clip(value, low, high):
    return max(low, min(float(value), high))


def _linear_from_db(value: float) -> float:
    return 10 ** (float(value) / 20.0)


def compile_ffmpeg_chain(effects: list[Effect]) -> str:
    """Compile Aura track effects into a real-audio ffmpeg filter chain.

    Effects that can be represented safely with stock FFmpeg filters are rendered here. More
    sophisticated plugin/model processors can remain in session metadata and be routed through a
    configured plugin host without ever turning symbolic/MIDI data into a final master.
    """
    chain: list[str] = []
    for fx in effects:
        if not fx.enabled:
            continue
        p = fx.parameters
        if fx.type == "gain":
            chain.append(f"volume={_db(p.get('db'))}dB")
        elif fx.type == "highpass":
            chain.append(f"highpass=f={_clip(p.get('hz', 70), 20, 1000):.2f}")
        elif fx.type == "lowpass":
            chain.append(f"lowpass=f={_clip(p.get('hz', 18000), 1000, 22000):.2f}")
        elif fx.type == "bandpass":
            center = _clip(p.get("frequency_hz", 1000), 20, 20000)
            width = _clip(p.get("width_octaves", 1.0), 0.05, 6.0)
            chain.append(f"bandpass=f={center:.2f}:width_type=o:width={width:.3f}")
        elif fx.type == "notch":
            center = _clip(p.get("frequency_hz", 60), 20, 20000)
            width = _clip(p.get("width_octaves", 0.08), 0.01, 3.0)
            chain.append(f"bandreject=f={center:.2f}:width_type=o:width={width:.3f}")
        elif fx.type == "low_shelf":
            gain = _clip(p.get("gain_db", 0.0), -18, 18)
            hz = _clip(p.get("frequency_hz", 120), 20, 2000)
            width = _clip(p.get("width", 0.7), 0.1, 4.0)
            chain.append(f"bass=g={gain:.2f}:f={hz:.2f}:w={width:.3f}")
        elif fx.type == "high_shelf":
            gain = _clip(p.get("gain_db", 0.0), -18, 18)
            hz = _clip(p.get("frequency_hz", 8500), 1000, 20000)
            width = _clip(p.get("width", 0.6), 0.1, 4.0)
            chain.append(f"treble=g={gain:.2f}:f={hz:.2f}:w={width:.3f}")
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
        elif fx.type == "expander":
            threshold = _clip(p.get("threshold_db", -36), -80, 0)
            ratio = _clip(p.get("ratio", 2.0), 1.0, 20.0)
            attack = _clip(p.get("attack_ms", 20), 0.1, 500)
            release = _clip(p.get("release_ms", 220), 5, 3000)
            range_db = _clip(p.get("range_db", -18), -60, 0)
            range_linear = _clip(_linear_from_db(range_db), 0.0001, 1.0)
            chain.append(
                "agate="
                f"threshold={threshold:.2f}dB:ratio={ratio:.3f}:attack={attack:.3f}:"
                f"release={release:.3f}:range={range_linear:.6f}"
            )
        elif fx.type == "deesser":
            hz = _clip(p.get("frequency_hz", 6500), 3500, 11000)
            reduction = -abs(_clip(p.get("reduction_db", 4.0), 0, 12))
            chain.append(f"equalizer=f={hz:.1f}:width_type=o:width=1.2:g={reduction:.2f}")
        elif fx.type == "denoise":
            reduction = _clip(p.get("reduction_db", 12.0), 0.01, 40.0)
            floor = _clip(p.get("noise_floor_db", -50.0), -80.0, -20.0)
            chain.append(f"afftdn=nr={reduction:.3f}:nf={floor:.3f}")
        elif fx.type == "declick":
            window = _clip(p.get("window_ms", 55.0), 10.0, 100.0)
            overlap = _clip(p.get("overlap_percent", 75.0), 50.0, 95.0)
            ar_order = _clip(p.get("ar_order", 2.0), 0.0, 25.0)
            threshold = _clip(p.get("threshold", 2.0), 1.0, 100.0)
            burst = _clip(p.get("burst", 2.0), 0.0, 10.0)
            chain.append(
                "adeclick="
                f"window={window:.3f}:overlap={overlap:.3f}:arorder={ar_order:.3f}:"
                f"threshold={threshold:.3f}:burst={burst:.3f}"
            )
        elif fx.type == "declip":
            window = _clip(p.get("window_ms", 55.0), 10.0, 100.0)
            overlap = _clip(p.get("overlap_percent", 75.0), 50.0, 95.0)
            ar_order = _clip(p.get("ar_order", 8.0), 0.0, 25.0)
            threshold = _clip(p.get("threshold", 10.0), 1.0, 100.0)
            histogram = int(_clip(p.get("histogram_size", 1000), 100, 9999))
            chain.append(
                "adeclip="
                f"window={window:.3f}:overlap={overlap:.3f}:arorder={ar_order:.3f}:"
                f"threshold={threshold:.3f}:hsize={histogram}"
            )
        elif fx.type == "reverb":
            delay = int(_clip(p.get("predelay_ms", 30), 1, 500))
            decay = _clip(p.get("mix", .18), .01, .8)
            chain.append(f"aecho=0.8:0.7:{delay}|{delay*2}:{decay}|{decay*.6}")
        elif fx.type == "delay":
            ms = int(_clip(p.get("delay_ms", 240), 1, 2000))
            feedback = _clip(p.get("feedback", .25), 0.0, .9)
            chain.append(f"aecho=0.8:0.7:{ms}:{feedback}")
        elif fx.type in {"distortion", "saturation"}:
            drive = _clip(p.get("drive", 2.0 if fx.type == "distortion" else 1.35), 1.0, 12.0)
            chain.append(f"asoftclip=type=tanh:threshold={1.0/drive:.4f}")
        elif fx.type == "exciter":
            amount = _clip(p.get("amount", 2.0), 0.0, 8.0)
            chain.append(f"treble=g={amount:.2f}:f={float(p.get('frequency_hz', 7000))}:w=0.5")
        elif fx.type == "chorus":
            delay = _clip(p.get("delay_ms", 18), 5, 40)
            decay = _clip(p.get("decay", .35), .05, .9)
            speed = _clip(p.get("rate_hz", .8), .1, 5)
            depth = _clip(p.get("depth", 2.0), .1, 10)
            chain.append(f"chorus=0.7:0.9:{delay:.2f}:{decay:.3f}:{speed:.3f}:{depth:.3f}")
        elif fx.type == "flanger":
            delay = _clip(p.get("delay_ms", 2.0), 0, 30)
            depth = _clip(p.get("depth_ms", 2.0), 0, 10)
            regen = _clip(p.get("feedback", 0), -95, 95)
            speed = _clip(p.get("rate_hz", .5), .1, 10)
            chain.append(f"flanger=delay={delay:.2f}:depth={depth:.2f}:regen={regen:.2f}:speed={speed:.3f}")
        elif fx.type == "phaser":
            speed = _clip(p.get("rate_hz", .5), .1, 2)
            decay = _clip(p.get("decay", .4), .0, .99)
            chain.append(f"aphaser=in_gain=0.7:out_gain=0.9:delay=3:decay={decay:.3f}:speed={speed:.3f}:type=t")
        elif fx.type == "tremolo":
            speed = _clip(p.get("rate_hz", 5.0), .1, 20)
            depth = _clip(p.get("depth", .5), 0.0, 1.0)
            chain.append(f"tremolo=f={speed:.3f}:d={depth:.3f}")
        elif fx.type == "pitch_shift":
            semitones = _clip(p.get("semitones", 0), -12, 12)
            ratio = 2 ** (semitones / 12.0)
            chain.append(f"asetrate=48000*{ratio:.8f},aresample=48000,atempo={1/ratio:.8f}")
        elif fx.type == "doubler":
            ms = int(_clip(p.get("delay_ms", 22), 8, 45))
            mix = _clip(p.get("mix", .18), .02, .5)
            chain.append(f"aecho=0.9:0.8:{ms}:{mix}")
            chain.append(f"stereotools=mlev=1:slev={_clip(p.get('width', 1.25), 1.0, 2.0):.3f}")
        elif fx.type == "stereo_width":
            width = _clip(p.get("width", 1.0), 0.0, 2.0)
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
