from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .lyric_alignment import estimate_alignment, line_is_surgically_aligned, run_configured_forced_alignment, verify_line
from .song_dna import SongDNAStore
from .tenant_storage import project_path

router = APIRouter(tags=["Lyric Alignment"])


class AlignmentEstimateRequest(BaseModel):
    start_seconds: float = Field(default=0.0, ge=0.0)
    end_seconds: float | None = Field(default=None, gt=0.0)


class VerifyLineRequest(BaseModel):
    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(gt=0.0)


def _project(name: str):
    try:
        return project_path(name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc


@router.get("/projects/{project_name}/lyric-alignment")
def lyric_alignment_status(project_name: str):
    project = _project(project_name)
    try:
        dna = SongDNAStore(project).load()
    except Exception as exc:
        raise HTTPException(500, f"Unable to load Song DNA: {type(exc).__name__}: {exc}") from exc
    rows = []
    for line in dna.lyric_lines:
        rows.append({
            "id": line.id,
            "order": line.order,
            "text": line.text,
            "start_seconds": line.start_seconds,
            "end_seconds": line.end_seconds,
            "alignment_state": line.metadata.get("alignment_state", "unaligned"),
            "alignment_confidence": line.metadata.get("alignment_confidence", 0.0),
            "alignment_method": line.metadata.get("alignment_method"),
            "surgical_ready": line_is_surgically_aligned(line),
        })
    return {
        "lines": rows,
        "summary": {
            "total": len(rows),
            "surgical_ready": sum(1 for row in rows if row["surgical_ready"]),
            "estimated": sum(1 for row in rows if row["alignment_state"] == "estimated"),
            "verified": sum(1 for row in rows if row["alignment_state"] in {"verified", "forced_aligned"}),
        },
        "policy": "Estimated timings speed setup but cannot authorize surgical vocal replacement. Verify manually or use a configured forced aligner with adequate confidence.",
    }


@router.post("/projects/{project_name}/lyric-alignment/estimate")
def estimate_lyric_alignment(project_name: str, request: AlignmentEstimateRequest):
    project = _project(project_name)
    try:
        dna = estimate_alignment(
            project,
            start_seconds=request.start_seconds,
            end_seconds=request.end_seconds,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Unable to estimate lyric timing: {type(exc).__name__}: {exc}") from exc
    return {
        "song_dna": dna.model_dump(mode="json"),
        "state": "estimated",
        "warning": "Estimated alignment must be verified before phrase-local vocal generation.",
    }


@router.patch("/projects/{project_name}/lyric-alignment/{line_id}")
def verify_lyric_line(project_name: str, line_id: str, request: VerifyLineRequest):
    project = _project(project_name)
    try:
        dna = verify_line(
            project,
            line_id,
            start_seconds=request.start_seconds,
            end_seconds=request.end_seconds,
            actor="member",
        )
    except KeyError as exc:
        raise HTTPException(404, "Lyric line not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    line = next(item for item in dna.lyric_lines if item.id == line_id)
    return {
        "line": line.model_dump(mode="json"),
        "surgical_ready": line_is_surgically_aligned(line),
        "detail": "Timing verified. Aura may now target this lyric phrase without treating an estimate as exact timing.",
    }


@router.post("/projects/{project_name}/lyric-alignment/forced")
def forced_lyric_alignment(project_name: str):
    project = _project(project_name)
    try:
        dna = run_configured_forced_alignment(project)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Forced alignment failed: {type(exc).__name__}: {exc}") from exc
    return {
        "song_dna": dna.model_dump(mode="json"),
        "detail": "Forced alignment completed. Only lines meeting the confidence threshold are marked ready for surgical editing.",
    }
