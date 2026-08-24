from __future__ import annotations

import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .project import ProjectWorkspace
from .song_dna import SongDNAStore, ensure_song_dna_from_manifest
from .song_edit_executor import commit_candidate, generate_candidate
from .tenant_storage import project_path

router = APIRouter(tags=["Editable Song DNA"])


class LyricLinePatch(BaseModel):
    text: str


class InstrumentReplacementPlan(BaseModel):
    replacement: str
    instruction: str = ""


class SectionRegenerationPlan(BaseModel):
    instruction: str
    preserve_instruments: bool = True


def _project(name: str) -> Path:
    try:
        return project_path(name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc


def _store(name: str) -> SongDNAStore:
    project = _project(name)
    store = SongDNAStore(project)
    if not store.path.is_file():
        workspace = ProjectWorkspace(project)
        manifest = workspace.load_manifest()
        ensure_song_dna_from_manifest(project, manifest.model_dump(mode="json"))
    return store


def _directive_for(name: str, directive_id: str):
    dna = _store(name).load()
    directive = next((item for item in dna.directives if item.id == directive_id), None)
    if directive is None:
        raise HTTPException(404, "Song edit directive not found")
    return dna, directive


def _candidate_file(name: str, directive_id: str) -> Path:
    project = _project(name).resolve()
    _dna, directive = _directive_for(name, directive_id)
    raw = str(directive.metadata.get("candidate_path") or "").strip()
    if not raw:
        raise HTTPException(404, "This edit does not have an audition candidate yet")
    value = Path(raw)
    target = value.resolve() if value.is_absolute() else (project / value).resolve()
    if target != project and project not in target.parents:
        raise HTTPException(400, "Invalid candidate path")
    if not target.is_file():
        raise HTTPException(404, "The audition candidate audio is missing")
    if target.suffix.lower() not in {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".aac"}:
        raise HTTPException(415, "Candidate is not a streamable audio file")
    return target


@router.get("/projects/{project_name}/song-dna")
def get_song_dna(project_name: str):
    try:
        dna = _store(project_name).load()
    except Exception as exc:
        raise HTTPException(500, f"Unable to load editable Song DNA: {type(exc).__name__}: {exc}") from exc
    return {
        "song_dna": dna.model_dump(mode="json"),
        "editing_model": {
            "master_is_a_render": True,
            "non_destructive": True,
            "audition_before_commit": True,
            "targetable": ["lyric_line", "section", "instrument_layer", "voice", "mix", "master"],
        },
    }


@router.post("/projects/{project_name}/song-dna/initialize")
def initialize_song_dna(project_name: str):
    project = _project(project_name)
    try:
        manifest = ProjectWorkspace(project).load_manifest()
        dna = ensure_song_dna_from_manifest(project, manifest.model_dump(mode="json"))
    except Exception as exc:
        raise HTTPException(500, f"Unable to initialize Song DNA: {type(exc).__name__}: {exc}") from exc
    return {"song_dna": dna.model_dump(mode="json")}


@router.patch("/projects/{project_name}/song-dna/lyrics/{line_id}")
def replace_lyric_line(project_name: str, line_id: str, patch: LyricLinePatch):
    try:
        dna = _store(project_name).update_lyric_line(line_id, patch.text)
    except KeyError as exc:
        raise HTTPException(404, "Lyric line not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    directive = dna.directives[-1]
    return {
        "song_dna": dna.model_dump(mode="json"),
        "directive": directive.model_dump(mode="json"),
        "render_state": "planned",
        "next_step": "Generate a candidate, audition it against the current song, then commit only if approved.",
    }


@router.post("/projects/{project_name}/song-dna/instruments/{layer_id}/replace-plan")
def plan_instrument_replacement(project_name: str, layer_id: str, request: InstrumentReplacementPlan):
    try:
        dna = _store(project_name).plan_instrument_replacement(layer_id, request.replacement, request.instruction)
    except KeyError as exc:
        raise HTTPException(404, "Instrument layer not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    directive = dna.directives[-1]
    return {
        "directive": directive.model_dump(mode="json"),
        "song_dna_version": dna.version,
        "render_state": "planned",
        "next_step": "Generate an isolated replacement against the current arrangement, audition it, then commit only if approved.",
    }


@router.post("/projects/{project_name}/song-dna/sections/{section_id}/regenerate-plan")
def plan_section_regeneration(project_name: str, section_id: str, request: SectionRegenerationPlan):
    try:
        dna = _store(project_name).plan_section_regeneration(
            section_id,
            request.instruction,
            preserve_instruments=request.preserve_instruments,
        )
    except KeyError as exc:
        raise HTTPException(404, "Song section not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    directive = dna.directives[-1]
    return {
        "directive": directive.model_dump(mode="json"),
        "song_dna_version": dna.version,
        "render_state": "planned",
        "next_step": "Regenerate only this section/region using per-layer candidates; do not replace unaffected sections or flatten the editable project.",
    }


@router.post("/projects/{project_name}/song-dna/sync-session")
def sync_song_dna_session(project_name: str):
    project = _project(project_name)
    session_path = project / "aura_session.json"
    if not session_path.is_file():
        raise HTTPException(404, "This project does not have an Aura DAW session yet")
    try:
        dna = _store(project_name).sync_session(session_path)
    except Exception as exc:
        raise HTTPException(500, f"Unable to sync DAW session into Song DNA: {type(exc).__name__}: {exc}") from exc
    return {"song_dna": dna.model_dump(mode="json")}


@router.get("/projects/{project_name}/song-dna/directives/{directive_id}")
def song_edit_status(project_name: str, directive_id: str):
    _dna, directive = _directive_for(project_name, directive_id)
    has_candidate = bool(directive.metadata.get("candidate_path")) and directive.status in {"ready", "complete"}
    return {
        "directive": directive.model_dump(mode="json"),
        "has_candidate": has_candidate,
        "candidate_stream_url": (
            f"/projects/{project_name}/song-dna/directives/{directive_id}/candidate-audio" if has_candidate else None
        ),
        "can_generate": directive.status not in {"rendering", "queued", "complete"},
        "can_commit": directive.status == "ready" and has_candidate,
        "audition_required": directive.status == "ready" and has_candidate,
    }


@router.post("/projects/{project_name}/song-dna/directives/{directive_id}/generate")
def generate_song_edit_candidate(project_name: str, directive_id: str):
    project = _project(project_name)
    try:
        result = generate_candidate(project, directive_id)
    except KeyError as exc:
        raise HTTPException(404, "Song edit directive not found") from exc
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Unable to generate edit candidate: {type(exc).__name__}: {exc}") from exc
    payload = result.model_dump(mode="json")
    if result.candidate_path:
        payload["candidate_stream_url"] = f"/projects/{project_name}/song-dna/directives/{directive_id}/candidate-audio"
    return payload


@router.get("/projects/{project_name}/song-dna/directives/{directive_id}/candidate-audio", include_in_schema=False)
def audition_song_edit_candidate(project_name: str, directive_id: str):
    candidate = _candidate_file(project_name, directive_id)
    media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
    return FileResponse(candidate, media_type=media_type, filename=None)


@router.post("/projects/{project_name}/song-dna/directives/{directive_id}/discard")
def discard_song_edit_candidate(project_name: str, directive_id: str):
    store = _store(project_name)
    dna, directive = _directive_for(project_name, directive_id)
    if directive.status not in {"ready", "failed"}:
        raise HTTPException(409, "Only a ready or failed candidate can be discarded")
    history = list(directive.metadata.get("candidate_history") or [])
    candidate_path = str(directive.metadata.get("candidate_path") or "")
    candidate_kind = str(directive.metadata.get("candidate_kind") or "")
    if candidate_path:
        history.append({
            "path": candidate_path,
            "kind": candidate_kind,
            "outcome": "rejected",
            "at": datetime.now(timezone.utc).isoformat(),
        })
    for key in ("candidate_path", "candidate_kind", "audition_required", "last_error"):
        directive.metadata.pop(key, None)
    directive.metadata["candidate_history"] = history[-20:]
    directive.status = "planned"
    directive.updated_at = datetime.now(timezone.utc).isoformat()
    store.save(dna)
    return {
        "directive": directive.model_dump(mode="json"),
        "state": "planned",
        "detail": "Candidate rejected. The original DAW take remains active and a new candidate can be generated.",
    }


@router.post("/projects/{project_name}/song-dna/directives/{directive_id}/commit")
def commit_song_edit_candidate(project_name: str, directive_id: str):
    project = _project(project_name)
    try:
        result = commit_candidate(project, directive_id)
    except KeyError as exc:
        raise HTTPException(404, "Song edit directive not found") from exc
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Unable to commit edit candidate: {type(exc).__name__}: {exc}") from exc
    return {
        **result.model_dump(mode="json"),
        "master_stream_hint": f"/projects/{project_name}/outputs",
        "perceptual_review_required": True,
    }
