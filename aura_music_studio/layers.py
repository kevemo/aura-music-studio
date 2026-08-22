from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

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
    })
    subprocess.run(shlex.split(command), cwd=workspace.root, env=env, check=True)
    if not output.exists():
        raise RuntimeError(f"{name} layer command did not produce {output}")
    return output


def build_optional_layers(
    base: Path,
    workspace: ProjectWorkspace,
    manifest: ProjectManifest,
    plan: ArrangementPlan,
) -> dict[str, Path]:
    """Create independent real/neural audio layers when dedicated engines are configured.

    If the main music generator already rendered these parts, Aura can skip these hooks. They exist for
    higher-control workflows where harmonies/guitar need their own stems and independent mix levels.
    """
    layers: dict[str, Path] = {}
    root = workspace.work_dir / "layers"
    if manifest.production.wordless_backing_harmonies:
        p = _run_layer(
            "AURA_DIFFSINGER_CMD",
            "backing_harmonies",
            root / "backing_harmonies.wav",
            base,
            workspace,
            manifest,
            plan,
        )
        if p:
            layers["backing_harmonies"] = p
    if manifest.production.original_single_note_countermelody:
        p = _run_layer(
            "AURA_GUITAR_LAYER_CMD",
            "lead_guitar_countermelody",
            root / "lead_guitar_countermelody.wav",
            base,
            workspace,
            manifest,
            plan,
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
        "-map", "[mix]",
        "-c:a", "pcm_s24le", "-ar", "48000", str(output),
    ]
    subprocess.run(cmd, check=True)
    return output
