from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .assets import AssetLibrary
from .automix import apply_automix
from .autotune import AutoTuneSettings, detect_key, tune_vocal
from .build_around import BuildAroundRequest
from .effects import render_effects
from .fx_designer import FxDesignRequest, design_fx, safe_slug
from .fx_presets import get_preset as get_fx_preset, public_presets
from .instrument_catalog import public_catalog
from .job_api import queue as job_queue, start_full_song_slot
from .mastering import master, master_album, public_mastering_presets, translation_report
from .plans import (
    ADVANCED_AUTOTUNE,
    ADVANCED_FX,
    ADVANCED_INSTRUMENT_SELECTOR,
    ADVANCED_MASTERING,
    AI_FX_DESIGNER,
    ALBUM_MASTERING,
    AUTOMIX,
    BASIC_AUTOTUNE,
    BASIC_FX,
    BASIC_MASTERING,
    BASIC_STEM_SPLITTER,
    BUILD_AROUND_UPLOAD,
    MULTITRACK_DAW,
    PLUGIN_RACK,
    PRIORITY_QUEUE,
    REFERENCE_MASTERING,
    STANDARD_AUTOTUNE,
    STANDARD_FX,
    STEM_SPLITTER,
)
from .plugin_rack import PluginRackRequest, process_plugin_rack, public_plugin_catalog
from .separation import StemSeparator
from .session import StudioSession
from .tenant_storage import project_path

router = APIRouter(prefix="/production", tags=["production-suite"])

FREE_MASTER_PRESETS = {"universal", "streaming", "natural"}
BASE_MASTER_PRESETS = FREE_MASTER_PRESETS | {
    "punch", "clarity", "warm", "tape", "pop", "rock", "acoustic", "ballad", "karaoke", "broadcast"
}


class FxRenderRequest(BaseModel):
    asset_id: str
    preset_id: str


class FxDesignRenderRequest(BaseModel):
    asset_id: str
    description: str = Field(min_length=3, max_length=1500)
    category: str = "creative"
    max_effects: int = Field(default=8, ge=1, le=12)


class PluginRenderRequest(BaseModel):
    asset_id: str
    rack: PluginRackRequest


class TuneRequest(BaseModel):
    asset_id: str
    settings: AutoTuneSettings = Field(default_factory=AutoTuneSettings)


class AutoMixRequest(BaseModel):
    genre: str = "pop"
    intensity: float = Field(default=.8, ge=0.0, le=1.0)


class SplitRequest(BaseModel):
    asset_id: str
    mode: str = "four_stems"


class MasterRequest(BaseModel):
    asset_id: str
    preset: str = "universal"
    intensity: float = Field(default=1.0, ge=0.0, le=1.5)
    low_db: float = Field(default=0.0, ge=-6.0, le=6.0)
    mid_db: float = Field(default=0.0, ge=-6.0, le=6.0)
    high_db: float = Field(default=0.0, ge=-6.0, le=6.0)
    stereo_width: float | None = Field(default=None, ge=0.0, le=2.0)
    target_lufs: float | None = Field(default=None, ge=-24.0, le=-6.0)
    reference_asset_id: str | None = None


class AlbumMasterRequest(BaseModel):
    asset_ids: list[str] = Field(min_length=2, max_length=30)
    preset: str = "natural"
    target_lufs: float | None = Field(default=None, ge=-24.0, le=-6.0)
    intensity: float = Field(default=1.0, ge=0.0, le=1.5)


def _project(name: str) -> Path:
    try:
        return project_path(name, must_exist=True)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    return member


def _require(member, feature: str):
    if not member.plan.has(feature):
        raise HTTPException(403, f"{feature} requires a higher membership tier")


def _safe(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_") or "audio"


def _asset_audio(project: Path, asset_id: str):
    library = AssetLibrary(project)
    record = library.get(asset_id)
    if record.kind != "audio":
        raise HTTPException(400, "This operation requires an audio asset")
    return library, record, project / record.path


def _public_job(job: dict) -> dict:
    value = dict(job)
    value.pop("payload_json", None)
    value.pop("result_json", None)
    return value


@router.get("/instrument-catalog")
def instrument_catalog(request: Request):
    member = _member(request)
    advanced = member.plan.has(ADVANCED_INSTRUMENT_SELECTOR)
    catalog = public_catalog()
    for items in catalog.values():
        for item in items:
            item["locked"] = bool(item.get("pro_only") and not advanced)
    return {"plan": member.plan.id, "families": catalog}


@router.get("/fx-presets")
def fx_catalog(request: Request):
    member = _member(request)
    _require(member, BASIC_FX)
    allowed = {"free"}
    if member.plan.has(STANDARD_FX):
        allowed.add("base")
    if member.plan.has(ADVANCED_FX):
        allowed.add("pro")
    rows = public_presets()
    for row in rows:
        row["locked"] = row["tier"] not in allowed
    return {"plan": member.plan.id, "presets": rows}


@router.post("/fx-design")
def fx_design(body: FxDesignRequest, request: Request):
    _require(_member(request), AI_FX_DESIGNER)
    return design_fx(body).model_dump()


@router.get("/plugin-catalog")
def plugin_catalog(request: Request):
    _require(_member(request), PLUGIN_RACK)
    try:
        return {"plugins": public_plugin_catalog(), "catalog_only": True}
    except (ValueError, PermissionError) as exc:
        raise HTTPException(500, f"Plugin catalog configuration error: {exc}") from exc


@router.get("/mastering-presets")
def mastering_catalog(request: Request):
    member = _member(request)
    _require(member, BASIC_MASTERING)
    rows = public_mastering_presets()
    for row in rows:
        pid = row["id"]
        if member.plan.has(ADVANCED_MASTERING):
            locked = False
        elif member.plan.id == "base":
            locked = pid not in BASE_MASTER_PRESETS
        else:
            locked = pid not in FREE_MASTER_PRESETS
        row["locked"] = locked
    return {"plan": member.plan.id, "presets": rows}


@router.post("/projects/{project_name}/build-around")
def build_around(project_name: str, body: BuildAroundRequest, request: Request):
    member = _member(request)
    _require(member, BUILD_AROUND_UPLOAD)
    project = _project(project_name)
    if body.output_mode == "multitrack":
        _require(member, MULTITRACK_DAW)
    if not member.plan.has(ADVANCED_INSTRUMENT_SELECTOR):
        catalog = public_catalog()
        for switch in body.instrument_switches:
            item = next((x for x in catalog.get(switch.family, []) if x["id"] == switch.type_id), None)
            if item and item.get("pro_only"):
                raise HTTPException(403, f"{item['label']} is a Pro instrument type")
    # Validate the selected source belongs to this project before a job is admitted to the queue.
    _asset_audio(project, body.asset_id)
    start_full_song_slot(member, project_name)
    priority = 100 if member.plan.has(PRIORITY_QUEUE) else 20
    job = job_queue.submit(
        member.user_id,
        project_name,
        job_type="build_around",
        priority=priority,
        payload=body.model_dump(mode="json"),
    )
    return _public_job(job)


@router.post("/projects/{project_name}/fx")
def render_fx(project_name: str, body: FxRenderRequest, request: Request):
    member = _member(request)
    _require(member, BASIC_FX)
    try:
        preset = get_fx_preset(body.preset_id)
    except KeyError as exc:
        raise HTTPException(404, "FX preset not found") from exc
    if preset.tier == "base" and not member.plan.has(STANDARD_FX):
        raise HTTPException(403, "This FX preset unlocks on Base")
    if preset.tier == "pro" and not member.plan.has(ADVANCED_FX):
        raise HTTPException(403, "This FX preset unlocks on Pro")
    project = _project(project_name)
    _, record, source = _asset_audio(project, body.asset_id)
    out = project / "output" / "fx" / f"{_safe(Path(record.name).stem)}_{preset.id}.wav"
    render_effects(source, out, list(preset.effects))
    return {"output": str(out), "preset": preset.id, "name": preset.name, "effects": [x.model_dump() for x in preset.effects]}


@router.post("/projects/{project_name}/fx-design-render")
def fx_design_render(project_name: str, body: FxDesignRenderRequest, request: Request):
    _require(_member(request), AI_FX_DESIGNER)
    project = _project(project_name)
    _, record, source = _asset_audio(project, body.asset_id)
    design = design_fx(FxDesignRequest(description=body.description, category=body.category, max_effects=body.max_effects))
    out = project / "output" / "fx" / f"{_safe(Path(record.name).stem)}_{safe_slug(body.description)}.wav"
    render_effects(source, out, design.effects)
    return {"output": str(out), "design": design.model_dump()}


@router.post("/projects/{project_name}/plugin-rack")
def plugin_rack(project_name: str, body: PluginRenderRequest, request: Request):
    _require(_member(request), PLUGIN_RACK)
    project = _project(project_name)
    _, record, source = _asset_audio(project, body.asset_id)
    out = project / "output" / "plugins" / f"{_safe(Path(record.name).stem)}_AuraPluginRack.wav"
    try:
        rendered, report = process_plugin_rack(source, out, body.rack)
        return {"output": str(rendered), "report": report}
    except (ValueError, PermissionError, FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/projects/{project_name}/autotune")
def autotune(project_name: str, body: TuneRequest, request: Request):
    member = _member(request)
    _require(member, BASIC_AUTOTUNE)
    if body.settings.mode in {"classic", "hard"} and not member.plan.has(STANDARD_AUTOTUNE):
        raise HTTPException(403, "Classic/Hard Aura Tune modes unlock on Base")
    if body.settings.mode in {"robot", "custom"} and not member.plan.has(ADVANCED_AUTOTUNE):
        raise HTTPException(403, "Robot and custom-scale Aura Tune modes unlock on Pro")
    project = _project(project_name)
    _, record, source = _asset_audio(project, body.asset_id)
    out = project / "output" / "vocals" / f"{_safe(Path(record.name).stem)}_AuraTune_{body.settings.mode}.wav"
    rendered, report = tune_vocal(source, out, body.settings)
    return {"output": str(rendered), "report": report}


@router.get("/projects/{project_name}/autokey/{asset_id}")
def autokey(project_name: str, asset_id: str, request: Request):
    _require(_member(request), BASIC_AUTOTUNE)
    project = _project(project_name)
    _, _, source = _asset_audio(project, asset_id)
    return detect_key(source)


@router.post("/projects/{project_name}/automix")
def automix(project_name: str, body: AutoMixRequest, request: Request):
    member = _member(request)
    _require(member, AUTOMIX)
    project = _project(project_name)
    session_path = project / "aura_session.json"
    if not session_path.exists():
        raise HTTPException(404, "Create/open a multitrack session before AutoMix")
    session = StudioSession.load(session_path)
    report = apply_automix(session, project, genre=body.genre, intensity=body.intensity)
    session.save(session_path)
    return report


@router.post("/projects/{project_name}/split")
def smart_split(project_name: str, body: SplitRequest, request: Request):
    member = _member(request)
    project = _project(project_name)
    _, record, source = _asset_audio(project, body.asset_id)
    if body.mode in {"two_stems", "four_stems"}:
        _require(member, BASIC_STEM_SPLITTER)
    elif body.mode in {"six_stems", "detailed"}:
        _require(member, STEM_SPLITTER)
    else:
        raise HTTPException(400, "Choose two_stems, four_stems, six_stems or detailed")
    stems = StemSeparator(project / "work" / "separation" / record.id).separate(source, mode=body.mode)
    return {"mode": body.mode, "stems": {k: str(v) for k, v in stems.items()}}


@router.post("/projects/{project_name}/master")
def mastering(project_name: str, body: MasterRequest, request: Request):
    member = _member(request)
    _require(member, BASIC_MASTERING)
    if member.plan.has(ADVANCED_MASTERING):
        allowed = None
    elif member.plan.id == "base":
        allowed = BASE_MASTER_PRESETS
    else:
        allowed = FREE_MASTER_PRESETS
    if allowed is not None and body.preset not in allowed:
        raise HTTPException(403, "This mastering character requires a higher tier")
    if (
        abs(body.low_db) > .01 or abs(body.mid_db) > .01 or abs(body.high_db) > .01
        or body.stereo_width is not None or body.target_lufs is not None
    ) and not member.plan.has(ADVANCED_MASTERING):
        raise HTTPException(403, "Manual mastering controls unlock on Pro")
    project = _project(project_name)
    library, record, source = _asset_audio(project, body.asset_id)
    reference = None
    if body.reference_asset_id:
        _require(member, REFERENCE_MASTERING)
        ref_record = library.get(body.reference_asset_id)
        if ref_record.kind != "audio":
            raise HTTPException(400, "Reference mastering requires an audio reference")
        reference = project / ref_record.path
    out = project / "output" / "masters" / f"{_safe(Path(record.name).stem)}_{body.preset}_AuraMaster.wav"
    mastered, report = master(
        source, out, preset=body.preset, reference=reference, intensity=body.intensity,
        low_db=body.low_db, mid_db=body.mid_db, high_db=body.high_db,
        stereo_width=body.stereo_width, target_lufs=body.target_lufs,
    )
    return {"output": str(mastered), "report": report, "translation": translation_report(mastered)}


@router.post("/projects/{project_name}/album-master")
def album_master(project_name: str, body: AlbumMasterRequest, request: Request):
    member = _member(request)
    _require(member, ALBUM_MASTERING)
    project = _project(project_name)
    library = AssetLibrary(project)
    sources = []
    for asset_id in body.asset_ids:
        record = library.get(asset_id)
        if record.kind != "audio":
            raise HTTPException(400, f"Asset {asset_id} is not audio")
        sources.append(project / record.path)
    out_dir = project / "output" / "album_master"
    return {
        "tracks": master_album(
            sources, out_dir, preset=body.preset, target_lufs=body.target_lufs, intensity=body.intensity
        )
    }
