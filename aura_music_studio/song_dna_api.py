from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .project import ProjectWorkspace
from .song_dna import SongDNAStore, ensure_song_dna_from_manifest
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


def _project(name: str):
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
    return {
        "song_dna": dna.model_dump(mode="json"),
        "render_state": "planned",
        "next_step": "Aura should regenerate only the affected vocal phrase/region through the configured music renderer, then remaster the changed mix.",
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
        "next_step": "Route the directive to the configured audio renderer using the current stem/session as context; preserve all locked and explicitly preserved layers.",
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
        "next_step": "Regenerate only this section/region and crossfade or reconstruct local transitions; do not replace unaffected sections.",
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
