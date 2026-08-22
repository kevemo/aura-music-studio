from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from .acestep_api import ACE_TRACKS, AceStepClient
from .models import ArrangementPlan, ProjectManifest
from .project import ProjectWorkspace


def _run_layer(
    command_env: str,
    name: str,
    output: Path,
    base: Path,
    workspace: ProjectWorkspace,
    manifest: ProjectManifest,
    plan: ArrangementPlan,
) -> Path | None:
    command = os.getenv(command_env)
    if not command:
        return None
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "AURA_PROJECT": str(workspace.root),
        "AURA_BASE_AUDIO": str(base),
        "AURA_GUIDE": str(workspace.work_dir / "score_guide.wav"),
        "AURA_PROMPT": plan.render_prompt,
        "AURA_NEGATIVE_PROMPT": plan.negative_prompt,
        "AURA_BPM": str(plan.tempo_bpm),
        "AURA_KEY": plan.key or "",
        "AURA_METER": plan.meter,
        "AURA_LAYER": name,
        "AURA_OUTPUT": str(output),
        "AURA_REAL_AUDIO_REQUIRED": "1",
    })
    subprocess.run(shlex.split(command), cwd=workspace.root, env=env, check=True)
    if not output.exists():
        raise RuntimeError(f"{name} layer command did not produce {output}")
    if output.suffix.lower() in {".mid", ".midi"}:
        output.unlink(missing_ok=True)
        raise RuntimeError(f"Aura refused symbolic output for final layer {name}")
    return output


def generate_complementary_layer(
    base: Path,
    workspace: ProjectWorkspace,
    *,
    track: str,
    prompt: str,
    start: float = 0.0,
    end: float = -1,
) -> Path:
    """Generate one independent real-audio instrument/vocal track from the current backing context."""
    track = track.strip().lower()
    if track not in ACE_TRACKS:
        raise ValueError(f"Unsupported complementary track: {track}")
    if not os.getenv("AURA_ACESTEP_API_URL"):
        raise RuntimeError("Complementary track generation requires the direct ACE-Step API or a dedicated layer adapter")
    client = AceStepClient()
    model = os.getenv("AURA_ACESTEP_TRACK_MODEL", "acestep-v15-base")
    return client.add_track(
        base,
        workspace.work_dir / "layers" / f"ace_{track}",
        track=track,
        prompt=prompt,
        model=model,
        start=start,
        end=end,
    )


def build_optional_layers(
    base: Path,
    workspace: ProjectWorkspace,
    manifest: ProjectManifest,
    plan: ArrangementPlan,
) -> dict[str, Path]:
    """Create independent real/neural audio layers when high-control production is requested."""
    layers: dict[str, Path] = {}
    root = workspace.work_dir / "layers"

    if manifest.production.wordless_backing_harmonies:
        p = _run_layer(
            "AURA_DIFFSINGER_CMD",
            "backing_harmonies",
            root / "backing_harmonies.wav",
            base, workspace, manifest, plan,
        )
        if not p and os.getenv("AURA_ACESTEP_API_URL"):
            p = generate_complementary_layer(
                base, workspace,
                track="backing_vocals",
                prompt=(
                    "Natural wordless backing harmony singers, ooh and aah textures, tasteful thirds/fifths, "
                    "strongest in choruses and final build, mixed as a supportive ensemble rather than a lead singer. "
                    + plan.backing_vocal_brief
                ),
            )
        if p:
            layers["backing_harmonies"] = p

    if manifest.production.original_single_note_countermelody:
        p = _run_layer(
            "AURA_GUITAR_LAYER_CMD",
            "lead_guitar_countermelody",
            root / "lead_guitar_countermelody.wav",
            base, workspace, manifest, plan,
        )
        if not p and os.getenv("AURA_ACESTEP_API_URL"):
            p = generate_complementary_layer(
                base, workspace,
                track="guitar",
                prompt=(
                    "Expressive real electric lead guitar playing an original single-note countermelody and answer phrases, "
                    "with bends, vibrato, slides and breathing space around the lead vocal; do not simply double the vocal melody. "
                    + plan.countermelody_brief
                ),
            )
        if p:
            layers["lead_guitar_countermelody"] = p
    return layers


def mix_layers(
    base: Path,
    layers: dict[str, Path],
    workspace: ProjectWorkspace,
    manifest: ProjectManifest,
) -> Path:
    if not layers:
        return base
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to mix generated real-audio layers")

    ordered: list[tuple[str, Path, float]] = []
    if "backing_harmonies" in layers:
        ordered.append(("backing_harmonies", layers["backing_harmonies"], manifest.mix.backing_vocals_db))
    if "lead_guitar_countermelody" in layers:
        ordered.append(("lead_guitar_countermelody", layers["lead_guitar_countermelody"], manifest.mix.lead_guitar_db))

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(base)]
    for _, path, _ in ordered:
        cmd += ["-i", str(path)]

    filters = ["[0:a]aresample=48000,volume=1.0[a0]"]
    labels = ["[a0]"]
    for i, (_, _, db) in enumerate(ordered, start=1):
        filters.append(f"[{i}:a]aresample=48000,volume={db}dB[a{i}]")
        labels.append(f"[a{i}]")
    filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0[mix]")

    output = workspace.work_dir / "neural_master_with_layers.wav"
    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[mix]", "-c:a", "pcm_s24le", "-ar", "48000", str(output),
    ]
    subprocess.run(cmd, check=True)
    return output
