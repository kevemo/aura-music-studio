from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

from .mastering import master, save_master_report, translation_report
from .models import MixConfig
from .project import ProjectWorkspace
from .separation import StemSeparator


def require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Required executable not found: {name}")
    return path


def transcode(source: Path, target: Path) -> Path:
    require_binary("ffmpeg")
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source)]
    if target.suffix.lower() == ".mp3":
        cmd += ["-codec:a", "libmp3lame", "-b:a", "320k"]
    elif target.suffix.lower() == ".wav":
        cmd += ["-c:a", "pcm_s24le", "-ar", "48000"]
    elif target.suffix.lower() == ".flac":
        cmd += ["-c:a", "flac", "-compression_level", "8", "-ar", "48000"]
    cmd.append(str(target))
    subprocess.run(cmd, check=True)
    return target


def normalize_stem_names(stems: dict[str, Path], workspace: ProjectWorkspace) -> list[Path]:
    target_dir = workspace.output_dir / "stems"
    target_dir.mkdir(parents=True, exist_ok=True)
    ordering = [
        "vocals", "instrumental", "drums", "bass", "guitar", "piano", "keys", "strings", "synth", "other"
    ]
    pretty = {
        "vocals": "Vocals",
        "instrumental": "Instrumental",
        "drums": "Drums",
        "bass": "Bass",
        "guitar": "Guitars",
        "piano": "Piano",
        "keys": "Keys",
        "strings": "Strings",
        "synth": "Synths",
        "other": "Other",
    }
    outputs = []
    for role in ordering:
        stem = stems.get(role)
        if not stem:
            continue
        index = len(outputs) + 1
        dest = target_dir / f"{index:02d}_{pretty[role]}.wav"
        transcode(stem, dest)
        outputs.append(dest)
    return outputs


def make_bandlab_pack(
    workspace: ProjectWorkspace,
    master_wav: Path,
    master_mp3: Path | None,
    master_flac: Path | None,
    stems: list[Path],
    metadata: dict,
    extra_files: list[Path] | None = None,
) -> Path:
    zip_path = workspace.output_dir / f"{workspace.root.name}_BandLab_Pack.zip"
    manifest_path = workspace.output_dir / "production_metadata.json"
    manifest_path.write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.write(master_wav, arcname=f"MASTER/{master_wav.name}")
        if master_mp3 and master_mp3.exists():
            z.write(master_mp3, arcname=f"MASTER/{master_mp3.name}")
        if master_flac and master_flac.exists():
            z.write(master_flac, arcname=f"MASTER/{master_flac.name}")
        for stem in stems:
            z.write(stem, arcname=f"STEMS/{stem.name}")
        for extra in extra_files or []:
            if extra.exists():
                z.write(extra, arcname=f"REPORTS/{extra.name}")
        z.write(manifest_path, arcname="production_metadata.json")
        z.writestr(
            "IMPORT_TO_BANDLAB.txt",
            "Import every stem at 0:00. All exported stems are real/neural audio and time-aligned.\n"
            "MIDI/score guides are never included as the final audio master.\n"
            "Keep backing harmonies behind your lead vocal and rebalance stems to taste.\n",
        )
    return zip_path


def finalize_render(source: Path, workspace: ProjectWorkspace, mix: MixConfig, metadata: dict) -> dict:
    """Finalize only real/neural audio.

    This function intentionally receives the post-neural render, not a symbolic/MIDI guide.
    """
    if source.name == "score_guide.wav" or "score_guide" in str(source):
        raise RuntimeError("Refusing to master/export a symbolic score guide as final music")

    reference = workspace.resolve_asset(mix.mastering_reference) if mix.mastering_reference else None
    master_wav = workspace.output_dir / "Aura_Final_Master.wav"
    master_wav, master_report = master(
        source,
        master_wav,
        preset=mix.mastering_preset,
        target_lufs=mix.target_lufs,
        true_peak_db=mix.true_peak_db,
        reference=reference,
    )
    translation = translation_report(master_wav)
    report_file = workspace.output_dir / "mastering_report.json"
    save_master_report(master_wav, master_report, translation, report_file)

    master_mp3 = None
    if mix.export_mp3:
        master_mp3 = workspace.output_dir / "Aura_Final_Master.mp3"
        transcode(master_wav, master_mp3)

    master_flac = None
    if mix.export_flac:
        master_flac = workspace.output_dir / "Aura_Final_Master.flac"
        transcode(master_wav, master_flac)

    stems = []
    separation_metadata = {}
    if mix.export_stems:
        try:
            separated = StemSeparator(workspace.work_dir / "separation").separate(master_wav, mode=mix.separation_mode)
            separation_metadata = {k: str(v) for k, v in separated.items()}
            stems = normalize_stem_names(separated, workspace)
        except Exception as exc:
            workspace.log(f"Advanced stem separation skipped: {type(exc).__name__}: {exc}", "stems.log")

    metadata = dict(metadata)
    metadata["mastering"] = master_report
    metadata["translation"] = translation
    metadata["separation"] = separation_metadata
    pack = make_bandlab_pack(
        workspace, master_wav, master_mp3, master_flac, stems, metadata,
        extra_files=[report_file] if mix.export_translation_report else [],
    )
    return {
        "master_wav": str(master_wav),
        "master_mp3": str(master_mp3) if master_mp3 else None,
        "master_flac": str(master_flac) if master_flac else None,
        "stems": [str(x) for x in stems],
        "mastering_report": str(report_file),
        "bandlab_pack": str(pack),
    }
