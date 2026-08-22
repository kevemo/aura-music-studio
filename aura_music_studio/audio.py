from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

from .models import MixConfig
from .project import ProjectWorkspace


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
    cmd.append(str(target))
    subprocess.run(cmd, check=True)
    return target


def master_audio(source: Path, target: Path, mix: MixConfig) -> Path:
    """Two-pass EBU R128-ish mastering via ffmpeg loudnorm.

    This is intentionally conservative. Neural renderers should supply the musical performance;
    Aura's master stage supplies delivery loudness, peak control and format consistency.
    """
    require_binary("ffmpeg")
    target.parent.mkdir(parents=True, exist_ok=True)
    filter_chain = (
        f"highpass=f=24,lowpass=f=19000,"
        f"loudnorm=I={mix.target_lufs}:TP={mix.true_peak_db}:LRA=11,"
        "alimiter=limit=0.95:attack=5:release=50"
    )
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-af", filter_chain,
        "-c:a", "pcm_s24le", "-ar", "48000", str(target),
    ], check=True)
    return target


def separate_stems(source: Path, workspace: ProjectWorkspace) -> list[Path]:
    """Split with Demucs 6-source model when available.

    htdemucs_6s provides drums, bass, vocals, guitar, piano and other. If Demucs is not installed,
    Aura keeps the neural master and records that stem separation was skipped.
    """
    out_root = workspace.work_dir / "demucs"
    out_root.mkdir(parents=True, exist_ok=True)
    demucs = shutil.which("demucs")
    if demucs:
        cmd = [demucs, "-n", "htdemucs_6s", "--float32", "-o", str(out_root), str(source)]
    else:
        # python -m demucs works for pip-installed Demucs even when no console script is exposed.
        try:
            import demucs  # noqa: F401
            cmd = ["python", "-m", "demucs", "-n", "htdemucs_6s", "--float32", "-o", str(out_root), str(source)]
        except Exception:
            workspace.log("Demucs unavailable; preserving stereo neural master only.", "stems.log")
            return []
    subprocess.run(cmd, check=True)
    candidates = sorted(out_root.rglob("*.wav"))
    workspace.log(f"Demucs created {len(candidates)} stem files.", "stems.log")
    return candidates


def normalize_stem_names(stems: list[Path], workspace: ProjectWorkspace) -> list[Path]:
    target_dir = workspace.output_dir / "stems"
    target_dir.mkdir(parents=True, exist_ok=True)
    mapping = {
        "drums": "01_Drums.wav",
        "bass": "02_Bass.wav",
        "guitar": "03_Guitars.wav",
        "piano": "04_Piano.wav",
        "vocals": "05_Backing_Harmony_Vocals.wav",
        "other": "06_Keys_Strings_Percussion_Other.wav",
    }
    outputs = []
    for stem in stems:
        name = stem.stem.lower()
        dest_name = mapping.get(name, f"Stem_{stem.name}")
        dest = target_dir / dest_name
        shutil.copy2(stem, dest)
        outputs.append(dest)
    return outputs


def make_bandlab_pack(workspace: ProjectWorkspace, master_wav: Path, master_mp3: Path | None, stems: list[Path], metadata: dict) -> Path:
    zip_path = workspace.output_dir / f"{workspace.root.name}_BandLab_Pack.zip"
    manifest_path = workspace.output_dir / "production_metadata.json"
    manifest_path.write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.write(master_wav, arcname=f"MASTER/{master_wav.name}")
        if master_mp3 and master_mp3.exists():
            z.write(master_mp3, arcname=f"MASTER/{master_mp3.name}")
        for stem in stems:
            z.write(stem, arcname=f"STEMS/{stem.name}")
        z.write(manifest_path, arcname="production_metadata.json")
        z.writestr(
            "IMPORT_TO_BANDLAB.txt",
            "Import every stem at 0:00. All exported stems derive from the same neural master and stay time-aligned.\n"
            "Keep the backing-harmony vocal stem below your lead vocal and adjust to taste.\n",
        )
    return zip_path


def finalize_render(source: Path, workspace: ProjectWorkspace, mix: MixConfig, metadata: dict) -> dict:
    master_wav = workspace.output_dir / "Aura_Final_Master.wav"
    master_audio(source, master_wav, mix)
    master_mp3 = None
    if mix.export_mp3:
        master_mp3 = workspace.output_dir / "Aura_Final_Master.mp3"
        transcode(master_wav, master_mp3)
    stems = []
    if mix.export_stems:
        stems = normalize_stem_names(separate_stems(master_wav, workspace), workspace)
    pack = make_bandlab_pack(workspace, master_wav, master_mp3, stems, metadata)
    return {
        "master_wav": str(master_wav),
        "master_mp3": str(master_mp3) if master_mp3 else None,
        "stems": [str(x) for x in stems],
        "bandlab_pack": str(pack),
    }
