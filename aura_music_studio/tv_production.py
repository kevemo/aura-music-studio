from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .plans import BASIC_TIMELINE
from .professional_editor import ProfessionalEditorStore
from .tenant_storage import project_path

router = APIRouter(prefix="/creative", tags=["TV Production"])

TV_FILENAME = "tv_production.json"
DeliveryProfile = Literal["broadcast_1080p", "broadcast_4k", "web_1080p", "archive_master"]
EpisodeStatus = Literal["development", "editing", "review", "mastered", "delivery_ready"]


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


def _aware_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduled_at must include a timezone offset")
    return value.isoformat()


class StrictRequest(BaseModel):
    """Reject undeclared request fields at the TV metadata boundary."""

    model_config = ConfigDict(extra="forbid")


class TVGraphicsPackage(BaseModel):
    id: str = Field(default_factory=lambda: _id("tvpkg"))
    name: str = Field(min_length=1, max_length=160)
    intro_sequence_id: str | None = Field(default=None, max_length=200)
    outro_sequence_id: str | None = Field(default=None, max_length=200)
    credits_sequence_id: str | None = Field(default=None, max_length=200)
    lower_third_sequence_ids: list[str] = Field(default_factory=list, max_length=100)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class TVProgramme(BaseModel):
    id: str = Field(default_factory=lambda: _id("tvprog"))
    title: str = Field(min_length=1, max_length=200)
    synopsis: str = Field(default="", max_length=4000)
    default_graphics_package_id: str | None = Field(default=None, max_length=200)
    series_ids: list[str] = Field(default_factory=list, max_length=500)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class TVSeries(BaseModel):
    id: str = Field(default_factory=lambda: _id("tvseries"))
    programme_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    season_number: int = Field(ge=1, le=10000)
    episode_ids: list[str] = Field(default_factory=list, max_length=10000)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class TVEpisode(BaseModel):
    id: str = Field(default_factory=lambda: _id("tvep"))
    programme_id: str = Field(min_length=1, max_length=200)
    series_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=240)
    episode_number: int = Field(ge=1, le=100000)
    production_code: str = Field(default="", max_length=120)
    synopsis: str = Field(default="", max_length=4000)
    editor_sequence_id: str = Field(min_length=1, max_length=200)
    graphics_package_id: str | None = Field(default=None, max_length=200)
    target_duration_seconds: float | None = Field(default=None, gt=0.0, le=86400.0)
    scheduled_at: str | None = None
    delivery_profile: DeliveryProfile = "broadcast_1080p"
    status: EpisodeStatus = "development"
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class TVProductionDocument(BaseModel):
    schema_version: int = 1
    programmes: list[TVProgramme] = Field(default_factory=list)
    series: list[TVSeries] = Field(default_factory=list)
    episodes: list[TVEpisode] = Field(default_factory=list)
    graphics_packages: list[TVGraphicsPackage] = Field(default_factory=list)
    updated_at: str = Field(default_factory=_now)


class ProgrammeRequest(StrictRequest):
    title: str = Field(min_length=1, max_length=200)
    synopsis: str = Field(default="", max_length=4000)
    default_graphics_package_id: str | None = Field(default=None, max_length=200)


class SeriesRequest(StrictRequest):
    title: str = Field(min_length=1, max_length=200)
    season_number: int = Field(ge=1, le=10000)


class GraphicsPackageRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=160)
    intro_sequence_id: str | None = Field(default=None, max_length=200)
    outro_sequence_id: str | None = Field(default=None, max_length=200)
    credits_sequence_id: str | None = Field(default=None, max_length=200)
    lower_third_sequence_ids: list[str] = Field(default_factory=list, max_length=100)


class EpisodeRequest(StrictRequest):
    programme_id: str = Field(min_length=1, max_length=200)
    series_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=240)
    episode_number: int = Field(ge=1, le=100000)
    production_code: str = Field(default="", max_length=120)
    synopsis: str = Field(default="", max_length=4000)
    editor_sequence_id: str = Field(min_length=1, max_length=200)
    graphics_package_id: str | None = Field(default=None, max_length=200)
    target_duration_seconds: float | None = Field(default=None, gt=0.0, le=86400.0)
    scheduled_at: datetime | None = None
    delivery_profile: DeliveryProfile = "broadcast_1080p"
    status: EpisodeStatus = "development"

    @field_validator("scheduled_at")
    @classmethod
    def validate_scheduled_at(cls, value: datetime | None):
        _aware_iso(value)
        return value


class EpisodePatchRequest(StrictRequest):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    production_code: str | None = Field(default=None, max_length=120)
    synopsis: str | None = Field(default=None, max_length=4000)
    editor_sequence_id: str | None = Field(default=None, min_length=1, max_length=200)
    graphics_package_id: str | None = Field(default=None, max_length=200)
    target_duration_seconds: float | None = Field(default=None, gt=0.0, le=86400.0)
    scheduled_at: datetime | None = None
    delivery_profile: DeliveryProfile | None = None
    status: EpisodeStatus | None = None

    @field_validator("scheduled_at")
    @classmethod
    def validate_scheduled_at(cls, value: datetime | None):
        _aware_iso(value)
        return value


class TVProductionStore:
    """Project-confined TV planning data linked to the shared professional editor graph."""

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / "work" / TV_FILENAME

    def load(self) -> TVProductionDocument:
        if not self.path.is_file():
            return TVProductionDocument()
        return TVProductionDocument.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, document: TVProductionDocument) -> TVProductionDocument:
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

    def sequence(self, sequence_id: str, *, require_video: bool = False) -> dict:
        state = self._editor_state()
        sequence = next(
            (row for row in state["branch"].get("sequences", []) if row.get("id") == sequence_id),
            None,
        )
        if sequence is None:
            raise ValueError(f"Editor sequence not found: {sequence_id}")
        if require_video and sequence.get("kind") != "video":
            raise ValueError("TV episodes, intros, outros and credits require video editor sequences")
        return sequence

    def _validate_graphics_package(self, package: TVGraphicsPackage) -> None:
        for sequence_id in (
            package.intro_sequence_id,
            package.outro_sequence_id,
            package.credits_sequence_id,
        ):
            if sequence_id:
                self.sequence(sequence_id, require_video=True)
        for sequence_id in package.lower_third_sequence_ids:
            self.sequence(sequence_id, require_video=False)

    def _validate_relations(self, document: TVProductionDocument) -> None:
        programmes = {row.id: row for row in document.programmes}
        series = {row.id: row for row in document.series}
        episodes = {row.id: row for row in document.episodes}
        packages = {row.id: row for row in document.graphics_packages}
        if len(programmes) != len(document.programmes):
            raise ValueError("Duplicate TV programme id")
        if len(series) != len(document.series):
            raise ValueError("Duplicate TV series id")
        if len(episodes) != len(document.episodes):
            raise ValueError("Duplicate TV episode id")
        if len(packages) != len(document.graphics_packages):
            raise ValueError("Duplicate TV graphics-package id")

        for package in document.graphics_packages:
            self._validate_graphics_package(package)
        for programme in document.programmes:
            if programme.default_graphics_package_id and programme.default_graphics_package_id not in packages:
                raise ValueError("Programme default graphics package does not exist")
            for series_id in programme.series_ids:
                row = series.get(series_id)
                if row is None or row.programme_id != programme.id:
                    raise ValueError("Programme/series relationship is inconsistent")
        for row in document.series:
            if row.programme_id not in programmes:
                raise ValueError("TV series references a missing programme")
            for episode_id in row.episode_ids:
                episode = episodes.get(episode_id)
                if episode is None or episode.series_id != row.id or episode.programme_id != row.programme_id:
                    raise ValueError("Series/episode relationship is inconsistent")
        for episode in document.episodes:
            programme = programmes.get(episode.programme_id)
            row = series.get(episode.series_id)
            if programme is None or row is None or row.programme_id != programme.id:
                raise ValueError("Episode programme/series relationship is inconsistent")
            if episode.graphics_package_id and episode.graphics_package_id not in packages:
                raise ValueError("Episode graphics package does not exist")
            self.sequence(episode.editor_sequence_id, require_video=True)

    def create_graphics_package(self, body: GraphicsPackageRequest) -> TVGraphicsPackage:
        document = self.load()
        package = TVGraphicsPackage(
            name=_clean_text(body.name, field="Graphics package name", maximum=160),
            intro_sequence_id=body.intro_sequence_id,
            outro_sequence_id=body.outro_sequence_id,
            credits_sequence_id=body.credits_sequence_id,
            lower_third_sequence_ids=list(dict.fromkeys(body.lower_third_sequence_ids)),
        )
        self._validate_graphics_package(package)
        document.graphics_packages.append(package)
        self.save(document)
        return package

    def create_programme(self, body: ProgrammeRequest) -> TVProgramme:
        document = self.load()
        if body.default_graphics_package_id and not any(
            row.id == body.default_graphics_package_id for row in document.graphics_packages
        ):
            raise ValueError("Programme default graphics package does not exist")
        programme = TVProgramme(
            title=_clean_text(body.title, field="Programme title", maximum=200),
            synopsis=str(body.synopsis or "").strip(),
            default_graphics_package_id=body.default_graphics_package_id,
        )
        document.programmes.append(programme)
        self.save(document)
        return programme

    def create_series(self, programme_id: str, body: SeriesRequest) -> TVSeries:
        document = self.load()
        programme = next((row for row in document.programmes if row.id == programme_id), None)
        if programme is None:
            raise KeyError(programme_id)
        if any(row.programme_id == programme_id and row.season_number == body.season_number for row in document.series):
            raise ValueError("That season number already exists for this programme")
        row = TVSeries(
            programme_id=programme.id,
            title=_clean_text(body.title, field="Series title", maximum=200),
            season_number=body.season_number,
        )
        document.series.append(row)
        programme.series_ids.append(row.id)
        programme.updated_at = _now()
        self.save(document)
        return row

    def create_episode(self, body: EpisodeRequest) -> TVEpisode:
        document = self.load()
        programme = next((row for row in document.programmes if row.id == body.programme_id), None)
        row = next((item for item in document.series if item.id == body.series_id), None)
        if programme is None or row is None or row.programme_id != programme.id:
            raise ValueError("Episode programme/series relationship is invalid")
        if any(item.series_id == row.id and item.episode_number == body.episode_number for item in document.episodes):
            raise ValueError("That episode number already exists in this series")
        self.sequence(body.editor_sequence_id, require_video=True)
        graphics_package_id = body.graphics_package_id or programme.default_graphics_package_id
        if graphics_package_id and not any(item.id == graphics_package_id for item in document.graphics_packages):
            raise ValueError("Episode graphics package does not exist")
        episode = TVEpisode(
            programme_id=programme.id,
            series_id=row.id,
            title=_clean_text(body.title, field="Episode title", maximum=240),
            episode_number=body.episode_number,
            production_code=str(body.production_code or "").strip(),
            synopsis=str(body.synopsis or "").strip(),
            editor_sequence_id=body.editor_sequence_id,
            graphics_package_id=graphics_package_id,
            target_duration_seconds=body.target_duration_seconds,
            scheduled_at=_aware_iso(body.scheduled_at),
            delivery_profile=body.delivery_profile,
            status=body.status,
        )
        document.episodes.append(episode)
        row.episode_ids.append(episode.id)
        row.updated_at = _now()
        self.save(document)
        return episode

    def patch_episode(self, episode_id: str, body: EpisodePatchRequest) -> TVEpisode:
        document = self.load()
        episode = next((row for row in document.episodes if row.id == episode_id), None)
        if episode is None:
            raise KeyError(episode_id)
        changes = body.model_dump(exclude_unset=True)
        if "scheduled_at" in changes:
            changes["scheduled_at"] = _aware_iso(body.scheduled_at)
        if "title" in changes and changes["title"] is not None:
            changes["title"] = _clean_text(changes["title"], field="Episode title", maximum=240)
        if "editor_sequence_id" in changes and changes["editor_sequence_id"] is not None:
            self.sequence(changes["editor_sequence_id"], require_video=True)
        if "graphics_package_id" in changes and changes["graphics_package_id"] is not None:
            if not any(row.id == changes["graphics_package_id"] for row in document.graphics_packages):
                raise ValueError("Episode graphics package does not exist")
        payload = episode.model_dump(mode="json")
        payload.update(changes)
        payload["updated_at"] = _now()
        updated = TVEpisode.model_validate(payload)
        index = next(i for i, row in enumerate(document.episodes) if row.id == episode_id)
        document.episodes[index] = updated
        self.save(document)
        return updated

    def handoff_manifest(self, episode_id: str) -> dict:
        document = self.load()
        episode = next((row for row in document.episodes if row.id == episode_id), None)
        if episode is None:
            raise KeyError(episode_id)
        if episode.status != "delivery_ready":
            raise ValueError("Episode must be delivery_ready before Shared Skies handoff")
        sequence = self.sequence(episode.editor_sequence_id, require_video=True)
        package = next(
            (row for row in document.graphics_packages if row.id == episode.graphics_package_id),
            None,
        )
        return {
            "schema_version": 1,
            "kind": "shared_skies_tv_delivery_handoff",
            "authority": "chat5_shared_skies_transport",
            "state": "prepared_not_transmitted",
            "transmission_requested": False,
            "transport_credentials_present": False,
            "episode": episode.model_dump(mode="json"),
            "editor_source": {
                "project_dir_exposed": False,
                "sequence_id": sequence["id"],
                "kind": sequence["kind"],
                "width": sequence["width"],
                "height": sequence["height"],
                "fps": sequence["fps"],
                "duration": sequence["duration"],
            },
            "graphics_package": package.model_dump(mode="json") if package else None,
            "scheduling": {
                "scheduled_at": episode.scheduled_at,
                "metadata_only": True,
                "transport_schedule_created": False,
            },
        }


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    if not member.plan.has(BASIC_TIMELINE):
        raise HTTPException(403, "TV production workflows unlock on the Basic membership tier")
    return member


def _project(project_name: str) -> Path:
    try:
        return project_path(project_name, must_exist=True)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc


def _store(project_name: str) -> TVProductionStore:
    return TVProductionStore(_project(project_name))


def _execute(callable_):
    try:
        return callable_()
    except KeyError as exc:
        raise HTTPException(404, f"TV production resource not found: {exc.args[0]}") from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/projects/{project_name}/tv")
def tv_production_state(project_name: str, request: Request):
    _member(request)
    store = _store(project_name)
    document = _execute(store.load)
    return {
        "tv": document.model_dump(mode="json"),
        "project_scoped": True,
        "transport_authority": "chat5_shared_skies_transport",
        "transport_owned_here": False,
    }


@router.post("/projects/{project_name}/tv/graphics-packages")
def create_tv_graphics_package(project_name: str, body: GraphicsPackageRequest, request: Request):
    _member(request)
    package = _execute(lambda: _store(project_name).create_graphics_package(body))
    return {"graphics_package": package.model_dump(mode="json"), "project_scoped": True}


@router.post("/projects/{project_name}/tv/programmes")
def create_tv_programme(project_name: str, body: ProgrammeRequest, request: Request):
    _member(request)
    programme = _execute(lambda: _store(project_name).create_programme(body))
    return {"programme": programme.model_dump(mode="json"), "project_scoped": True}


@router.post("/projects/{project_name}/tv/programmes/{programme_id}/series")
def create_tv_series(project_name: str, programme_id: str, body: SeriesRequest, request: Request):
    _member(request)
    row = _execute(lambda: _store(project_name).create_series(programme_id, body))
    return {"series": row.model_dump(mode="json"), "project_scoped": True}


@router.post("/projects/{project_name}/tv/episodes")
def create_tv_episode(project_name: str, body: EpisodeRequest, request: Request):
    _member(request)
    episode = _execute(lambda: _store(project_name).create_episode(body))
    return {"episode": episode.model_dump(mode="json"), "project_scoped": True}


@router.patch("/projects/{project_name}/tv/episodes/{episode_id}")
def patch_tv_episode(project_name: str, episode_id: str, body: EpisodePatchRequest, request: Request):
    _member(request)
    episode = _execute(lambda: _store(project_name).patch_episode(episode_id, body))
    return {"episode": episode.model_dump(mode="json"), "project_scoped": True}


@router.post("/projects/{project_name}/tv/episodes/{episode_id}/shared-skies-handoff")
def prepare_shared_skies_handoff(project_name: str, episode_id: str, request: Request):
    _member(request)
    manifest = _execute(lambda: _store(project_name).handoff_manifest(episode_id))
    return {"handoff": manifest}


__all__ = [
    "EpisodePatchRequest",
    "EpisodeRequest",
    "GraphicsPackageRequest",
    "ProgrammeRequest",
    "SeriesRequest",
    "TVEpisode",
    "TVGraphicsPackage",
    "TVProductionDocument",
    "TVProductionStore",
    "TVProgramme",
    "TVSeries",
    "router",
]
