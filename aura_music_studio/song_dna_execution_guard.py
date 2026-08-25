from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .lyric_alignment import line_is_surgically_aligned
from .song_dna_api import generate_song_edit_candidate as base_generate_song_edit_candidate
from .song_dna_api import _directive_for
from .song_dna_locks import router as song_dna_locks_router

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
    return base_generate_song_edit_candidate(project_name, directive_id)
