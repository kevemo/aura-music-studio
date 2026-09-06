from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .lyric_alignment import line_is_surgically_aligned
from .rhiannon_voice_contracts import SpeechTimingSpan, SpeechTimingTrack, TimingKind
from .song_dna import SongDNAStore
from .tenant_storage import project_path

router = APIRouter(tags=["Rhiannon Voice Timing"])


def build_verified_phrase_timing(project) -> tuple[SpeechTimingTrack, dict]:
    """Convert only verified/forced-aligned lyric lines into shared timing truth.

    Estimated lyric-density timings are intentionally excluded. The current Chat 2 alignment
    foundation provides line/phrase timing, not real word/phoneme/viseme timing, so this adapter
    must keep `precise_timing=False` until an executable runtime supplies canonical visemes.
    """
    dna = SongDNAStore(project).load()
    if not dna.lyric_lines:
        raise ValueError("This song has no lyric lines")

    spans: list[SpeechTimingSpan] = []
    accepted = 0
    rejected = 0
    max_end_ms = 0
    for line in dna.lyric_lines:
        if not line_is_surgically_aligned(line):
            rejected += 1
            continue
        start_ms = max(0, int(round(float(line.start_seconds or 0.0) * 1000.0)))
        end_ms = max(start_ms, int(round(float(line.end_seconds or 0.0) * 1000.0)))
        confidence = max(0.0, min(1.0, float(line.metadata.get("alignment_confidence") or 0.0)))
        spans.append(
            SpeechTimingSpan(
                kind=TimingKind.PHRASE,
                value=(line.text or "").strip()[:120] or "lyric phrase",
                start_ms=start_ms,
                end_ms=end_ms,
                confidence=confidence,
            )
        )
        accepted += 1
        max_end_ms = max(max_end_ms, end_ms)

    if not spans:
        raise ValueError("No verified or forced-aligned lyric phrases are available")

    target_ms = int(round(float(dna.target_duration_seconds or 0.0) * 1000.0))
    audio_duration_ms = max(max_end_ms, target_ms, 1)
    track = SpeechTimingTrack(
        audio_duration_ms=audio_duration_ms,
        spans=spans,
        precise_timing=False,
        source="derived",
    )
    coverage = {
        "total_lyric_lines": len(dna.lyric_lines),
        "accepted_verified_phrases": accepted,
        "excluded_unverified_or_estimated_lines": rejected,
        "complete_phrase_coverage": accepted == len(dna.lyric_lines),
        "word_timing_available": False,
        "phoneme_timing_available": False,
        "viseme_timing_available": False,
        "precise_timing": False,
        "source": "verified_or_forced_song_dna_lyric_alignment",
    }
    return track, coverage


@router.get("/projects/{project_name}/voice-timing/phrases")
def rhiannon_phrase_timing(project_name: str):
    try:
        project = project_path(project_name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc
    try:
        timing, coverage = build_verified_phrase_timing(project)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "timing": timing.model_dump(mode="json"),
        "coverage": coverage,
        "handoff_consumers": ["chat1_rhiannon_3d", "chat3_video_cinema", "chat4_game_forge"],
        "visual_authority_owned_here": False,
    }


__all__ = ["build_verified_phrase_timing", "router"]
