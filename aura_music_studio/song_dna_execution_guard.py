from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .lyric_alignment import line_is_surgically_aligned
from .song_dna_api import generate_song_edit_candidate as base_generate_song_edit_candidate
from .song_dna_api import _directive_for
from .song_dna_locks import router as song_dna_locks_router
from .song_section_regeneration import (
    generate_multitrack_section_candidate,
    router as section_regeneration_router,
)
from .tenant_storage import project_path

router = APIRouter()
router.include_router(song_dna_locks_router)


@router.post("/projects/{project_name}/song-dna/directives/{directive_id}/generate", include_in_schema=False)
def guarded_generate_song_edit_candidate(project_name: str, directive_id: str):
    dna, directive = _directive_for(project_name, directive_id)
    if directive.action == "replace_lyric_line":
        if not directive.target_ids:
            raise HTTPException(409, "Lyric edit has no target line")
        line = next((item for item in dna.lyric_lines if item.id == directive.target_ids[0]), None)
        if line is None:
            raise HTTPException(404, "Target lyric line not found")
        if not line_is_surgically_aligned(line):
            raise HTTPException(
                409,
                "This lyric line needs verified timing before Aura can repaint only that phrase. Open Lyric Alignment, verify the start/end, then generate the candidate.",
            )
    if directive.action == "regenerate_section":
        try:
            project = project_path(project_name, must_exist=True)
            result = generate_multitrack_section_candidate(project, directive_id)
        except KeyError as exc:
            raise HTTPException(404, "Song edit directive or section not found") from exc
        except (ValueError, RuntimeError, FileNotFoundError) as exc:
            raise HTTPException(409, str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(500, f"Unable to generate section candidate: {type(exc).__name__}: {exc}") from exc
        payload = result.model_dump(mode="json")
        payload["candidate_stream_url"] = (
            f"/projects/{project_name}/song-dna/directives/{directive_id}/candidate-audio"
        )
        return payload
    return base_generate_song_edit_candidate(project_name, directive_id)


# Keep this after the guarded generate route: section commit/discard must precede the base
# Song DNA API, while lyric generation must still pass through the verified timing guard.
router.include_router(section_regeneration_router)
