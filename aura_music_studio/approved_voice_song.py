from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .models import ProjectManifest, RenderResult
from .project import ProjectWorkspace
from .rights import RightsLedger
from .separation import StemSeparator
from .song_languages import song_language_from_manifest
from .voice import convert_singing_voice


class ApprovedVoiceSongError(RuntimeError):
    pass


def _mix_voice_and_instrumental(instrumental: Path, vocal: Path, output: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ApprovedVoiceSongError("ffmpeg is required to rebuild an approved-voice song master")
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(instrumental),
        "-i",
        str(vocal),
        "-filter_complex",
        "[0:a]aresample=48000,volume=1.0[inst];"
        "[1:a]aresample=48000,volume=1.0[lead];"
        "[inst][lead]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
        "alimiter=limit=0.97[out]",
        "-map",
        "[out]",
        "-ar",
        "48000",
        "-c:a",
        "pcm_s24le",
        str(output),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise ApprovedVoiceSongError(completed.stderr[-2000:] or "Approved-voice remix failed")
    if not output.exists() or output.stat().st_size < 4096:
        raise ApprovedVoiceSongError("Approved-voice remix did not produce a valid real-audio master")
    return output


def apply_approved_voice(
    render: RenderResult,
    workspace: ProjectWorkspace,
    manifest: ProjectManifest,
) -> RenderResult:
    """Replace a generated guide singer with the selected consent-approved voice.

    This stage is mandatory whenever `vocal_mode=approved_voice`. It never silently returns the
    generic guide vocal on failure. The guide singer provides melody, timing and target-language
    phonemes; the approved profile supplies the final vocal identity/timbre.
    """
    dna = manifest.project_dna or {}
    if dna.get("vocal_mode") != "approved_voice":
        return render

    profile_id = str(dna.get("voice_profile_id") or "").strip()
    if not profile_id:
        raise ApprovedVoiceSongError("Approved-voice project has no voice profile")

    ledger = RightsLedger(workspace.root / ".aura_rights")
    try:
        profile = ledger.get_voice(profile_id)
    except KeyError as exc:
        raise ApprovedVoiceSongError("Selected Aura Voice Profile was not found in this project") from exc
    try:
        profile.assert_usable("singing")
        profile.assert_usable("voice_conversion")
    except PermissionError as exc:
        raise ApprovedVoiceSongError(str(exc)) from exc

    separation_dir = workspace.work_dir / "approved_voice" / "separation"
    stems = StemSeparator(separation_dir).separate(render.audio_path, mode="two_stems")
    guide_vocal = stems.get("vocals")
    instrumental = stems.get("instrumental") or stems.get("other")
    if not guide_vocal or not guide_vocal.exists():
        raise ApprovedVoiceSongError("Aura could not isolate the guide lead vocal for approved-voice conversion")
    if not instrumental or not instrumental.exists():
        raise ApprovedVoiceSongError("Aura could not isolate the instrumental bed for approved-voice reconstruction")

    language = song_language_from_manifest(manifest)
    converted = workspace.work_dir / "approved_voice" / "lead_converted.wav"
    similarity = float(dna.get("voice_similarity", 0.82))
    pitch_shift = int(dna.get("voice_pitch_shift", 0))
    convert_singing_voice(
        guide_vocal,
        profile,
        converted,
        similarity=similarity,
        pitch_shift=pitch_shift,
    )

    final_path = workspace.work_dir / "approved_voice" / "approved_voice_master.wav"
    _mix_voice_and_instrumental(instrumental, converted, final_path)
    metadata = {
        **render.metadata,
        "approved_voice": {
            "profile_id": profile.id,
            "owner_label": profile.owner_label,
            "consent_checked": True,
            "allowed_uses_checked": ["singing", "voice_conversion"],
            "song_locale": language.locale,
            "song_language": language.english_name,
            "ace_vocal_language": language.ace_vocal_language,
            "similarity": min(similarity, profile.similarity_limit),
            "pitch_shift": pitch_shift,
            "guide_vocal": str(guide_vocal),
            "converted_vocal": str(converted),
            "instrumental": str(instrumental),
        },
    }
    (workspace.work_dir / "approved_voice" / "approved_voice_report.json").write_text(
        json.dumps(metadata["approved_voice"], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return RenderResult(
        renderer=render.renderer + "+consent_voice_conversion",
        audio_path=final_path,
        audio_origin="hybrid",
        is_final_quality=True,
        metadata=metadata,
    )
