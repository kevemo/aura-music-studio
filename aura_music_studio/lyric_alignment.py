from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import soundfile as sf

from .mixer import selected_audio_clips
from .session import StudioSession
from .song_dna import SongDNA, SongDNAStore, _now


_ALLOWED_ALIGNMENT_STATES = {"verified", "forced_aligned"}


def line_is_surgically_aligned(line) -> bool:
    state = str(line.metadata.get("alignment_state") or "")
    confidence = float(line.metadata.get("alignment_confidence") or 0.0)
    return (
        line.start_seconds is not None
        and line.end_seconds is not None
        and line.end_seconds > line.start_seconds
        and state in _ALLOWED_ALIGNMENT_STATES
        and confidence >= 0.70
    )


def _weight(text: str) -> float:
    words = [word for word in (text or "").split() if word]
    punctuation_pause = sum((text or "").count(mark) for mark in ",;:—-") * 0.35
    terminal_pause = sum((text or "").count(mark) for mark in ".!?") * 0.6
    return max(1.0, len(words) + punctuation_pause + terminal_pause)


def estimate_alignment(
    project: Path,
    *,
    start_seconds: float = 0.0,
    end_seconds: float | None = None,
) -> SongDNA:
    """Create clearly-labelled provisional timings from lyric density.

    This is a workflow accelerator only. It is never accepted as sufficient evidence for
    surgical vocal repainting until a user verifies it or a configured forced-aligner
    returns adequate confidence.
    """
    store = SongDNAStore(project)
    dna = store.load()
    if not dna.lyric_lines:
        raise ValueError("This song has no lyric lines to align")
    if end_seconds is None:
        end_seconds = float(dna.target_duration_seconds or 0.0)
        master = project / "output" / "Aura_Final_Master.wav"
        if master.is_file():
            try:
                end_seconds = float(sf.info(master).duration)
            except Exception:
                pass
    if end_seconds is None or end_seconds <= start_seconds:
        raise ValueError("A valid song/vocal duration is required for estimated alignment")

    weights = [_weight(line.text) for line in dna.lyric_lines]
    total = sum(weights)
    cursor = float(start_seconds)
    span = float(end_seconds - start_seconds)
    for line, weight in zip(dna.lyric_lines, weights):
        duration = span * (weight / total)
        line.start_seconds = round(cursor, 4)
        line.end_seconds = round(min(end_seconds, cursor + duration), 4)
        line.metadata = {
            **line.metadata,
            "alignment_state": "estimated",
            "alignment_method": "proportional_lyric_density",
            "alignment_confidence": 0.25,
            "alignment_updated_at": _now(),
        }
        cursor += duration
    dna.metadata = {
        **dna.metadata,
        "lyric_alignment": {
            "state": "estimated",
            "method": "proportional_lyric_density",
            "updated_at": _now(),
            "warning": "Estimated timings require verification before surgical vocal regeneration.",
        },
    }
    return store.save(dna)


def verify_line(
    project: Path,
    line_id: str,
    *,
    start_seconds: float,
    end_seconds: float,
    actor: str = "member",
) -> SongDNA:
    if start_seconds < 0 or end_seconds <= start_seconds:
        raise ValueError("Lyric timing must have a non-negative start and an end after the start")
    store = SongDNAStore(project)
    dna = store.load()
    line = next((item for item in dna.lyric_lines if item.id == line_id), None)
    if line is None:
        raise KeyError(line_id)
    if dna.target_duration_seconds and end_seconds > float(dna.target_duration_seconds) + 5:
        raise ValueError("Lyric timing extends beyond the song duration")
    line.start_seconds = round(float(start_seconds), 4)
    line.end_seconds = round(float(end_seconds), 4)
    line.metadata = {
        **line.metadata,
        "alignment_state": "verified",
        "alignment_method": "manual_member_verification",
        "alignment_confidence": 1.0,
        "alignment_verified_by": actor[:120],
        "alignment_updated_at": _now(),
    }
    dna.metadata = {
        **dna.metadata,
        "lyric_alignment": {
            **dict(dna.metadata.get("lyric_alignment") or {}),
            "updated_at": _now(),
        },
    }
    return store.save(dna)


def _project_audio(project: Path, dna: SongDNA) -> Path:
    vocal = next((layer for layer in dna.instruments if layer.role == "vocals" and layer.track_id), None)
    if vocal and (project / "aura_session.json").is_file():
        session = StudioSession.load(project / "aura_session.json")
        track = session.find_track(vocal.track_id or "")
        clips = selected_audio_clips(track)
        if len(clips) == 1 and clips[0].source:
            raw = Path(clips[0].source)
            candidate = raw.resolve() if raw.is_absolute() else (project / raw).resolve()
            root = project.resolve()
            if (candidate == root or root in candidate.parents) and candidate.is_file():
                return candidate
    for path in (
        project / "output" / "stems" / "vocals.wav",
        project / "stems" / "vocals.wav",
        project / "output" / "Aura_Final_Master.wav",
    ):
        if path.is_file():
            return path
    raise FileNotFoundError("No vocal/master audio is available for lyric alignment")


def run_configured_forced_alignment(project: Path) -> SongDNA:
    """Run an operator-configured forced-aligner without invoking a shell.

    `AURA_LYRIC_ALIGN_CMD` is an argv template such as:
      aligner --audio {audio} --lyrics {lyrics} --output {output}

    The tool must emit JSON: {"lines":[{"start":0.1,"end":1.2,"confidence":0.9}, ...]}.
    This keeps the core model independent from WhisperX/MFA/other aligners while giving the
    deployment a production integration point.
    """
    template = (os.getenv("AURA_LYRIC_ALIGN_CMD") or "").strip()
    if not template:
        raise RuntimeError("No forced lyric-alignment engine is configured")
    store = SongDNAStore(project)
    dna = store.load()
    if not dna.lyric_lines:
        raise ValueError("This song has no lyrics to align")
    audio = _project_audio(project, dna)
    work = project / "work" / "lyric_alignment"
    work.mkdir(parents=True, exist_ok=True)
    lyrics = work / "lyrics.txt"
    lyrics.write_text("\n".join(line.text for line in dna.lyric_lines) + "\n", encoding="utf-8")
    output = work / "alignment.json"
    argv = [
        part.replace("{audio}", str(audio)).replace("{lyrics}", str(lyrics)).replace("{output}", str(output))
        for part in shlex.split(template)
    ]
    if not argv:
        raise RuntimeError("Configured lyric aligner command is empty")
    timeout = max(30, min(3600, int(os.getenv("AURA_LYRIC_ALIGN_TIMEOUT_SECONDS", "600"))))
    subprocess.run(argv, check=True, timeout=timeout, cwd=work)
    if not output.is_file():
        raise RuntimeError("Forced aligner did not create its alignment JSON")
    payload = json.loads(output.read_text(encoding="utf-8"))
    rows = list(payload.get("lines") or [])
    if len(rows) != len(dna.lyric_lines):
        raise RuntimeError("Forced aligner line count does not match Song DNA lyrics")
    accepted = 0
    for line, row in zip(dna.lyric_lines, rows):
        start = float(row.get("start"))
        end = float(row.get("end"))
        confidence = max(0.0, min(1.0, float(row.get("confidence", 0.0))))
        if start < 0 or end <= start:
            raise RuntimeError("Forced aligner returned an invalid lyric time range")
        line.start_seconds = round(start, 4)
        line.end_seconds = round(end, 4)
        line.metadata = {
            **line.metadata,
            "alignment_state": "forced_aligned" if confidence >= 0.70 else "estimated",
            "alignment_method": "configured_forced_aligner",
            "alignment_confidence": confidence,
            "alignment_updated_at": _now(),
        }
        if confidence >= 0.70:
            accepted += 1
    dna.metadata = {
        **dna.metadata,
        "lyric_alignment": {
            "state": "forced_aligned" if accepted == len(rows) else "partial",
            "method": "configured_forced_aligner",
            "accepted_lines": accepted,
            "total_lines": len(rows),
            "audio_ref": str(audio),
            "updated_at": _now(),
        },
    }
    return store.save(dna)
