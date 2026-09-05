from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .plans import BASIC_TIMELINE
from .professional_editor import ProfessionalEditorStore
from .tenant_storage import project_path

router = APIRouter(prefix="/creative", tags=["Cinema Production"])

CINEMA_FILENAME = "cinema_production.json"
FilmFormat = Literal["feature", "short", "documentary", "music_video", "trailer", "other"]
FilmStatus = Literal["development", "preproduction", "production", "editing", "review", "mastered", "delivery_ready"]
DeliveryProfile = Literal["cinema_2k", "cinema_4k", "web_1080p", "web_4k", "archive_master"]
ShotStatus = Literal["planned", "storyboarded", "captured", "selected", "editing", "approved"]
ShotSize = Literal["extreme_wide", "wide", "full", "medium", "close_up", "extreme_close_up", "insert", "other"]
CameraMovement = Literal["static", "pan", "tilt", "dolly", "truck", "pedestal", "crane", "handheld", "steadicam", "gimbal", "drone", "zoom", "other"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _clean_text(value: str, *, field: str, maximum: int) -> str:
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        raise ValueError(f"{field} is required")
    if len(clean) > maximum:
        raise ValueError(f"{field} is too long")
    return clean


class StrictRequest(BaseModel):
    """Reject undeclared fields at the cinema planning boundary."""

    model_config = ConfigDict(extra="forbid")


class ContinuityNotes(BaseModel):
    wardrobe: str = Field(default="", max_length=4000)
    props: str = Field(default="", max_length=4000)
    hair_makeup: str = Field(default="", max_length=4000)
    lighting: str = Field(default="", max_length=4000)
    screen_direction: str = Field(default="", max_length=2000)
    performance: str = Field(default="", max_length=4000)
    location: str = Field(default="", max_length=4000)


class ContinuityRequest(StrictRequest):
    wardrobe: str = Field(default="", max_length=4000)
    props: str = Field(default="", max_length=4000)
    hair_makeup: str = Field(default="", max_length=4000)
    lighting: str = Field(default="", max_length=4000)
    screen_direction: str = Field(default="", max_length=2000)
    performance: str = Field(default="", max_length=4000)
    location: str = Field(default="", max_length=4000)


class CinemaFilm(BaseModel):
    id: str = Field(default_factory=lambda: _id("film"))
    title: str = Field(min_length=1, max_length=240)
    format: FilmFormat = "feature"
    logline: str = Field(default="", max_length=1000)
    synopsis: str = Field(default="", max_length=12000)
    script_revision: str = Field(default="", max_length=120)
    master_sequence_id: str | None = Field(default=None, max_length=200)
    delivery_profile: DeliveryProfile = "cinema_4k"
    status: FilmStatus = "development"
    scene_ids: list[str] = Field(default_factory=list, max_length=10000)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class CinemaScene(BaseModel):
    id: str = Field(default_factory=lambda: _id("scene"))
    film_id: str = Field(min_length=1, max_length=200)
    scene_number: int = Field(ge=1, le=100000)
    slugline: str = Field(min_length=1, max_length=300)
    synopsis: str = Field(default="", max_length=8000)
    script_text: str = Field(default="", max_length=50000)
    editor_sequence_id: str | None = Field(default=None, max_length=200)
    shot_ids: list[str] = Field(default_factory=list, max_length=10000)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class CinemaShot(BaseModel):
    id: str = Field(default_factory=lambda: _id("shot"))
    film_id: str = Field(min_length=1, max_length=200)
    scene_id: str = Field(min_length=1, max_length=200)
    shot_number: int = Field(ge=1, le=100000)
    title: str = Field(default="Shot", min_length=1, max_length=240)
    description: str = Field(default="", max_length=12000)
    editor_sequence_id: str | None = Field(default=None, max_length=200)
    target_duration_seconds: float | None = Field(default=None, gt=0.0, le=86400.0)
    shot_size: ShotSize = "medium"
    camera_movement: CameraMovement = "static"
    lens_mm: float | None = Field(default=None, gt=0.0, le=2000.0)
    status: ShotStatus = "planned"
    continuity: ContinuityNotes = Field(default_factory=ContinuityNotes)
    storyboard_panel_ids: list[str] = Field(default_factory=list, max_length=10000)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class CinemaStoryboardPanel(BaseModel):
    id: str = Field(default_factory=lambda: _id("board"))
    film_id: str = Field(min_length=1, max_length=200)
    scene_id: str = Field(min_length=1, max_length=200)
    shot_id: str = Field(min_length=1, max_length=200)
    panel_number: int = Field(ge=1, le=100000)
    caption: str = Field(default="", max_length=4000)
    action: str = Field(default="", max_length=8000)
    dialogue: str = Field(default="", max_length=8000)
    image_sequence_id: str | None = Field(default=None, max_length=200)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class CinemaProductionDocument(BaseModel):
    schema_version: int = 1
    films: list[CinemaFilm] = Field(default_factory=list)
    scenes: list[CinemaScene] = Field(default_factory=list)
    shots: list[CinemaShot] = Field(default_factory=list)
    storyboard_panels: list[CinemaStoryboardPanel] = Field(default_factory=list)
    updated_at: str = Field(default_factory=_now)


class FilmRequest(StrictRequest):
    title: str = Field(min_length=1, max_length=240)
    format: FilmFormat = "feature"
    logline: str = Field(default="", max_length=1000)
    synopsis: str = Field(default="", max_length=12000)
    script_revision: str = Field(default="", max_length=120)
    master_sequence_id: str | None = Field(default=None, max_length=200)
    delivery_profile: DeliveryProfile = "cinema_4k"
    status: FilmStatus = "development"


class FilmPatchRequest(StrictRequest):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    logline: str | None = Field(default=None, max_length=1000)
    synopsis: str | None = Field(default=None, max_length=12000)
    script_revision: str | None = Field(default=None, max_length=120)
    master_sequence_id: str | None = Field(default=None, max_length=200)
    delivery_profile: DeliveryProfile | None = None
    status: FilmStatus | None = None


class SceneRequest(StrictRequest):
    scene_number: int = Field(ge=1, le=100000)
    slugline: str = Field(min_length=1, max_length=300)
    synopsis: str = Field(default="", max_length=8000)
    script_text: str = Field(default="", max_length=50000)
    editor_sequence_id: str | None = Field(default=None, max_length=200)


class ShotRequest(StrictRequest):
    shot_number: int = Field(ge=1, le=100000)
    title: str = Field(default="Shot", min_length=1, max_length=240)
    description: str = Field(default="", max_length=12000)
    editor_sequence_id: str | None = Field(default=None, max_length=200)
    target_duration_seconds: float | None = Field(default=None, gt=0.0, le=86400.0)
    shot_size: ShotSize = "medium"
    camera_movement: CameraMovement = "static"
    lens_mm: float | None = Field(default=None, gt=0.0, le=2000.0)
    status: ShotStatus = "planned"
    continuity: ContinuityRequest = Field(default_factory=ContinuityRequest)


class ShotPatchRequest(StrictRequest):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=12000)
    editor_sequence_id: str | None = Field(default=None, max_length=200)
    target_duration_seconds: float | None = Field(default=None, gt=0.0, le=86400.0)
    shot_size: ShotSize | None = None
    camera_movement: CameraMovement | None = None
    lens_mm: float | None = Field(default=None, gt=0.0, le=2000.0)
    status: ShotStatus | None = None
    continuity: ContinuityRequest | None = None


class StoryboardPanelRequest(StrictRequest):
    panel_number: int = Field(ge=1, le=100000)
    caption: str = Field(default="", max_length=4000)
    action: str = Field(default="", max_length=8000)
    dialogue: str = Field(default="", max_length=8000)
    image_sequence_id: str | None = Field(default=None, max_length=200)


class CinemaProductionStore:
    """Project-confined film planning linked to the shared professional editor graph."""

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / "work" / CINEMA_FILENAME

    def load(self) -> CinemaProductionDocument:
        if not self.path.is_file():
            return CinemaProductionDocument()
        return CinemaProductionDocument.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, document: CinemaProductionDocument) -> CinemaProductionDocument:
        self._validate_relations(document)
        document.updated_at = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(document.model_dump(mode="json"), indent=2, ensure_ascii=False)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(payload + "\n", encoding="utf-8")
        temporary.replace(self.path)
        return document

    def _editor_state(self) -> dict:
        editor = ProfessionalEditorStore(self.project_dir)
        if not editor.exists():
            raise ValueError("Professional editor is not initialized for this project")
        return editor.public_state()

    def sequence(self, sequence_id: str, *, kind: Literal["video", "image"] | None = None) -> dict:
        state = self._editor_state()
        sequence = next(
            (row for row in state["branch"].get("sequences", []) if row.get("id") == sequence_id),
            None,
        )
        if sequence is None:
            raise ValueError(f"Editor sequence not found: {sequence_id}")
        if kind is not None and sequence.get("kind") != kind:
            raise ValueError(f"Cinema reference requires a {kind} editor sequence")
        return sequence

    def _validate_relations(self, document: CinemaProductionDocument) -> None:
        films = {row.id: row for row in document.films}
        scenes = {row.id: row for row in document.scenes}
        shots = {row.id: row for row in document.shots}
        panels = {row.id: row for row in document.storyboard_panels}
        if len(films) != len(document.films):
            raise ValueError("Duplicate cinema film id")
        if len(scenes) != len(document.scenes):
            raise ValueError("Duplicate cinema scene id")
        if len(shots) != len(document.shots):
            raise ValueError("Duplicate cinema shot id")
        if len(panels) != len(document.storyboard_panels):
            raise ValueError("Duplicate cinema storyboard-panel id")

        for film in document.films:
            if film.master_sequence_id:
                self.sequence(film.master_sequence_id, kind="video")
            for scene_id in film.scene_ids:
                scene = scenes.get(scene_id)
                if scene is None or scene.film_id != film.id:
                    raise ValueError("Film/scene relationship is inconsistent")
        for scene in document.scenes:
            if scene.film_id not in films:
                raise ValueError("Cinema scene references a missing film")
            if scene.editor_sequence_id:
                self.sequence(scene.editor_sequence_id, kind="video")
            for shot_id in scene.shot_ids:
                shot = shots.get(shot_id)
                if shot is None or shot.scene_id != scene.id or shot.film_id != scene.film_id:
                    raise ValueError("Scene/shot relationship is inconsistent")
        for shot in document.shots:
            scene = scenes.get(shot.scene_id)
            if scene is None or scene.film_id != shot.film_id:
                raise ValueError("Cinema shot references an inconsistent scene")
            if shot.editor_sequence_id:
                self.sequence(shot.editor_sequence_id, kind="video")
            for panel_id in shot.storyboard_panel_ids:
                panel = panels.get(panel_id)
                if panel is None or panel.shot_id != shot.id or panel.scene_id != shot.scene_id or panel.film_id != shot.film_id:
                    raise ValueError("Shot/storyboard relationship is inconsistent")
        for panel in document.storyboard_panels:
            shot = shots.get(panel.shot_id)
            scene = scenes.get(panel.scene_id)
            if shot is None or scene is None or shot.scene_id != scene.id:
                raise ValueError("Storyboard panel references a missing shot or scene")
            if panel.film_id != shot.film_id or scene.film_id != panel.film_id:
                raise ValueError("Storyboard film/scene/shot relationship is inconsistent")
            if panel.image_sequence_id:
                self.sequence(panel.image_sequence_id, kind="image")

    def create_film(self, body: FilmRequest) -> CinemaFilm:
        document = self.load()
        if body.master_sequence_id:
            self.sequence(body.master_sequence_id, kind="video")
        film = CinemaFilm(
            title=_clean_text(body.title, field="Film title", maximum=240),
            format=body.format,
            logline=str(body.logline or "").strip(),
            synopsis=str(body.synopsis or "").strip(),
            script_revision=str(body.script_revision or "").strip(),
            master_sequence_id=body.master_sequence_id,
            delivery_profile=body.delivery_profile,
            status=body.status,
        )
        document.films.append(film)
        self.save(document)
        return film

    def patch_film(self, film_id: str, body: FilmPatchRequest) -> CinemaFilm:
        document = self.load()
        film = next((row for row in document.films if row.id == film_id), None)
        if film is None:
            raise KeyError(film_id)
        changes = body.model_dump(exclude_unset=True)
        if "title" in changes and changes["title"] is not None:
            changes["title"] = _clean_text(changes["title"], field="Film title", maximum=240)
        if "master_sequence_id" in changes and changes["master_sequence_id"] is not None:
            self.sequence(changes["master_sequence_id"], kind="video")
        payload = film.model_dump(mode="json")
        payload.update(changes)
        payload["updated_at"] = _now()
        updated = CinemaFilm.model_validate(payload)
        index = next(i for i, row in enumerate(document.films) if row.id == film_id)
        document.films[index] = updated
        self.save(document)
        return updated

    def create_scene(self, film_id: str, body: SceneRequest) -> CinemaScene:
        document = self.load()
        film = next((row for row in document.films if row.id == film_id), None)
        if film is None:
            raise KeyError(film_id)
        if any(row.film_id == film_id and row.scene_number == body.scene_number for row in document.scenes):
            raise ValueError("That scene number already exists for this film")
        if body.editor_sequence_id:
            self.sequence(body.editor_sequence_id, kind="video")
        scene = CinemaScene(
            film_id=film.id,
            scene_number=body.scene_number,
            slugline=_clean_text(body.slugline, field="Scene slugline", maximum=300),
            synopsis=str(body.synopsis or "").strip(),
            script_text=str(body.script_text or "").strip(),
            editor_sequence_id=body.editor_sequence_id,
        )
        document.scenes.append(scene)
        film.scene_ids.append(scene.id)
        film.updated_at = _now()
        self.save(document)
        return scene

    def create_shot(self, scene_id: str, body: ShotRequest) -> CinemaShot:
        document = self.load()
        scene = next((row for row in document.scenes if row.id == scene_id), None)
        if scene is None:
            raise KeyError(scene_id)
        if any(row.scene_id == scene_id and row.shot_number == body.shot_number for row in document.shots):
            raise ValueError("That shot number already exists for this scene")
        if body.editor_sequence_id:
            self.sequence(body.editor_sequence_id, kind="video")
        shot = CinemaShot(
            film_id=scene.film_id,
            scene_id=scene.id,
            shot_number=body.shot_number,
            title=_clean_text(body.title, field="Shot title", maximum=240),
            description=str(body.description or "").strip(),
            editor_sequence_id=body.editor_sequence_id,
            target_duration_seconds=body.target_duration_seconds,
            shot_size=body.shot_size,
            camera_movement=body.camera_movement,
            lens_mm=body.lens_mm,
            status=body.status,
            continuity=ContinuityNotes.model_validate(body.continuity.model_dump()),
        )
        document.shots.append(shot)
        scene.shot_ids.append(shot.id)
        scene.updated_at = _now()
        self.save(document)
        return shot

    def patch_shot(self, shot_id: str, body: ShotPatchRequest) -> CinemaShot:
        document = self.load()
        shot = next((row for row in document.shots if row.id == shot_id), None)
        if shot is None:
            raise KeyError(shot_id)
        changes = body.model_dump(exclude_unset=True)
        if "title" in changes and changes["title"] is not None:
            changes["title"] = _clean_text(changes["title"], field="Shot title", maximum=240)
        if "editor_sequence_id" in changes and changes["editor_sequence_id"] is not None:
            self.sequence(changes["editor_sequence_id"], kind="video")
        if "continuity" in changes and changes["continuity"] is not None:
            changes["continuity"] = ContinuityNotes.model_validate(changes["continuity"]).model_dump(mode="json")
        payload = shot.model_dump(mode="json")
        payload.update(changes)
        payload["updated_at"] = _now()
        updated = CinemaShot.model_validate(payload)
        index = next(i for i, row in enumerate(document.shots) if row.id == shot_id)
        document.shots[index] = updated
        self.save(document)
        return updated

    def create_storyboard_panel(self, shot_id: str, body: StoryboardPanelRequest) -> CinemaStoryboardPanel:
        document = self.load()
        shot = next((row for row in document.shots if row.id == shot_id), None)
        if shot is None:
            raise KeyError(shot_id)
        if any(row.shot_id == shot_id and row.panel_number == body.panel_number for row in document.storyboard_panels):
            raise ValueError("That storyboard panel number already exists for this shot")
        if body.image_sequence_id:
            self.sequence(body.image_sequence_id, kind="image")
        panel = CinemaStoryboardPanel(
            film_id=shot.film_id,
            scene_id=shot.scene_id,
            shot_id=shot.id,
            panel_number=body.panel_number,
            caption=str(body.caption or "").strip(),
            action=str(body.action or "").strip(),
            dialogue=str(body.dialogue or "").strip(),
            image_sequence_id=body.image_sequence_id,
        )
        document.storyboard_panels.append(panel)
        shot.storyboard_panel_ids.append(panel.id)
        if shot.status == "planned":
            shot.status = "storyboarded"
        shot.updated_at = _now()
        self.save(document)
        return panel

    def handoff_manifest(self, film_id: str) -> dict:
        document = self.load()
        film = next((row for row in document.films if row.id == film_id), None)
        if film is None:
            raise KeyError(film_id)
        if film.status != "delivery_ready":
            raise ValueError("Film must be delivery_ready before Shared Skies handoff")
        if not film.master_sequence_id:
            raise ValueError("Film requires a master video sequence before Shared Skies handoff")
        sequence = self.sequence(film.master_sequence_id, kind="video")
        return {
            "schema_version": 1,
            "kind": "shared_skies_cinema_delivery_handoff",
            "authority": "chat5_shared_skies_transport",
            "state": "prepared_not_transmitted",
            "transmission_requested": False,
            "transport_credentials_present": False,
            "film": film.model_dump(mode="json"),
            "editor_source": {
                "project_dir_exposed": False,
                "sequence_id": sequence["id"],
                "kind": sequence["kind"],
                "width": sequence["width"],
                "height": sequence["height"],
                "fps": sequence["fps"],
                "duration": sequence["duration"],
            },
            "production_summary": {
                "scene_count": len([row for row in document.scenes if row.film_id == film.id]),
                "shot_count": len([row for row in document.shots if row.film_id == film.id]),
                "storyboard_panel_count": len([row for row in document.storyboard_panels if row.film_id == film.id]),
                "metadata_only": True,
            },
        }


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    if not member.plan.has(BASIC_TIMELINE):
        raise HTTPException(403, "Cinema production workflows unlock on the Basic membership tier")
    return member


def _project(project_name: str) -> Path:
    try:
        return project_path(project_name, must_exist=True)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc


def _store(project_name: str) -> CinemaProductionStore:
    return CinemaProductionStore(_project(project_name))


def _execute(callable_):
    try:
        return callable_()
    except KeyError as exc:
        raise HTTPException(404, f"Cinema production resource not found: {exc.args[0]}") from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/projects/{project_name}/cinema")
def cinema_production_state(project_name: str, request: Request):
    _member(request)
    document = _execute(_store(project_name).load)
    return {
        "cinema": document.model_dump(mode="json"),
        "project_scoped": True,
        "transport_authority": "chat5_shared_skies_transport",
        "transport_owned_here": False,
    }


@router.post("/projects/{project_name}/cinema/films")
def create_cinema_film(project_name: str, body: FilmRequest, request: Request):
    _member(request)
    film = _execute(lambda: _store(project_name).create_film(body))
    return {"film": film.model_dump(mode="json"), "project_scoped": True}


@router.patch("/projects/{project_name}/cinema/films/{film_id}")
def patch_cinema_film(project_name: str, film_id: str, body: FilmPatchRequest, request: Request):
    _member(request)
    film = _execute(lambda: _store(project_name).patch_film(film_id, body))
    return {"film": film.model_dump(mode="json"), "project_scoped": True}


@router.post("/projects/{project_name}/cinema/films/{film_id}/scenes")
def create_cinema_scene(project_name: str, film_id: str, body: SceneRequest, request: Request):
    _member(request)
    scene = _execute(lambda: _store(project_name).create_scene(film_id, body))
    return {"scene": scene.model_dump(mode="json"), "project_scoped": True}


@router.post("/projects/{project_name}/cinema/scenes/{scene_id}/shots")
def create_cinema_shot(project_name: str, scene_id: str, body: ShotRequest, request: Request):
    _member(request)
    shot = _execute(lambda: _store(project_name).create_shot(scene_id, body))
    return {"shot": shot.model_dump(mode="json"), "project_scoped": True}


@router.patch("/projects/{project_name}/cinema/shots/{shot_id}")
def patch_cinema_shot(project_name: str, shot_id: str, body: ShotPatchRequest, request: Request):
    _member(request)
    shot = _execute(lambda: _store(project_name).patch_shot(shot_id, body))
    return {"shot": shot.model_dump(mode="json"), "project_scoped": True}


@router.post("/projects/{project_name}/cinema/shots/{shot_id}/storyboard-panels")
def create_cinema_storyboard_panel(project_name: str, shot_id: str, body: StoryboardPanelRequest, request: Request):
    _member(request)
    panel = _execute(lambda: _store(project_name).create_storyboard_panel(shot_id, body))
    return {"storyboard_panel": panel.model_dump(mode="json"), "project_scoped": True}


@router.post("/projects/{project_name}/cinema/films/{film_id}/shared-skies-handoff")
def prepare_cinema_shared_skies_handoff(project_name: str, film_id: str, request: Request):
    _member(request)
    manifest = _execute(lambda: _store(project_name).handoff_manifest(film_id))
    return {"handoff": manifest}


__all__ = [
    "CinemaFilm",
    "CinemaProductionDocument",
    "CinemaProductionStore",
    "CinemaScene",
    "CinemaShot",
    "CinemaStoryboardPanel",
    "ContinuityRequest",
    "FilmPatchRequest",
    "FilmRequest",
    "SceneRequest",
    "ShotPatchRequest",
    "ShotRequest",
    "StoryboardPanelRequest",
    "router",
]
