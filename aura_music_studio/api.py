from __future__ import annotations

import os
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .assets import AssetLibrary
from .creation import CreateSongRequest, build_song_project
from .doctor import system_report
from .mastering import master, translation_report
from .pipeline import AuraPipeline
from .separation import StemSeparator
from .transcription import audio_to_midi
from .voice import create_voice_profile
from .rights import RightsLedger

PROJECTS_ROOT = Path(os.getenv("AURA_PROJECTS_ROOT", "projects")).resolve()
PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Aura Music Studio API",
    version="0.4.0",
    description="Real-audio-first multi-model generative music studio API.",
)


def _project(name: str) -> Path:
    p = (PROJECTS_ROOT / name).resolve()
    if PROJECTS_ROOT not in p.parents and p != PROJECTS_ROOT:
        raise HTTPException(400, "Invalid project path")
    if not p.exists():
        raise HTTPException(404, "Project not found")
    return p


@app.get("/health")
def health():
    return {"ok": True, "real_audio_only_final": True}


@app.get("/capabilities")
def capabilities():
    return system_report()


@app.get("/projects")
def list_projects():
    return [
        {"name": p.name, "has_manifest": (p / "project.yaml").exists(), "status": str(p / "aura_status.json")}
        for p in sorted(PROJECTS_ROOT.iterdir()) if p.is_dir()
    ]


@app.post("/songs")
def create_song(request: CreateSongRequest):
    project = build_song_project(request, PROJECTS_ROOT)
    return {"project": project.name, "path": str(project)}


@app.post("/projects/{project_name}/produce")
def produce(project_name: str):
    project = _project(project_name)
    return AuraPipeline(project).run()


@app.post("/projects/{project_name}/analyze")
def analyze(project_name: str):
    return AuraPipeline(_project(project_name)).analyze_only()


@app.post("/projects/{project_name}/assets")
async def upload_asset(
    project_name: str,
    file: UploadFile = File(...),
    kind: str = Form("auto"),
    rights_basis: str = Form("user_owned_or_licensed"),
    attestation: str = Form("I confirm I have the right to use this material in this project."),
    tags: str = Form(""),
):
    project = _project(project_name)
    incoming = project / "input" / "uploads"
    incoming.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "upload.bin").name
    tmp = incoming / safe_name
    with tmp.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)
    record = AssetLibrary(project).ingest(
        tmp,
        kind=kind,
        rights_basis=rights_basis,
        attestation=attestation,
        tags=[x.strip() for x in tags.split(",") if x.strip()],
    )
    tmp.unlink(missing_ok=True)
    return record.model_dump()


@app.get("/projects/{project_name}/assets")
def assets(project_name: str):
    return [x.model_dump() for x in AssetLibrary(_project(project_name)).list()]


@app.post("/projects/{project_name}/separate")
def separate(project_name: str, asset_id: str, mode: str = "six_stems"):
    project = _project(project_name)
    library = AssetLibrary(project)
    record = library.get(asset_id)
    if record.kind != "audio":
        raise HTTPException(400, "Stem separation requires an audio asset")
    source = project / record.path
    stems = StemSeparator(project / "work" / "separation").separate(source, mode=mode)
    return {k: str(v) for k, v in stems.items()}


@app.post("/projects/{project_name}/transcribe")
def transcribe(project_name: str, asset_id: str):
    project = _project(project_name)
    record = AssetLibrary(project).get(asset_id)
    if record.kind != "audio":
        raise HTTPException(400, "Audio-to-MIDI requires an audio asset")
    out = project / "work" / "transcription" / f"{record.id}.mid"
    audio_to_midi(project / record.path, out)
    return {"midi_control_file": str(out), "final_audio": False}


@app.post("/projects/{project_name}/master")
def master_asset(project_name: str, asset_id: str, preset: str = "streaming", reference_asset_id: str | None = None):
    project = _project(project_name)
    library = AssetLibrary(project)
    source_record = library.get(asset_id)
    if source_record.kind != "audio":
        raise HTTPException(400, "Mastering requires an audio asset")
    reference = None
    if reference_asset_id:
        ref_record = library.get(reference_asset_id)
        reference = project / ref_record.path
    out = project / "output" / f"{Path(source_record.name).stem}_AuraMaster.wav"
    mastered, report = master(project / source_record.path, out, preset=preset, reference=reference)
    return {"path": str(mastered), "report": report, "translation": translation_report(mastered)}


@app.post("/projects/{project_name}/voice-profiles")
async def new_voice_profile(
    project_name: str,
    name: str = Form(...),
    owner_label: str = Form(...),
    consent_statement: str = Form(...),
    reference: UploadFile = File(...),
):
    project = _project(project_name)
    voice_dir = project / "input" / "voice_profiles"
    voice_dir.mkdir(parents=True, exist_ok=True)
    target = voice_dir / Path(reference.filename or "voice.wav").name
    with target.open("wb") as f:
        while chunk := await reference.read(1024 * 1024):
            f.write(chunk)
    profile = create_voice_profile(
        RightsLedger(project / ".aura_rights"),
        name=name,
        owner_label=owner_label,
        reference_files=[target],
        consent_statement=consent_statement,
    )
    return profile.model_dump()


@app.get("/projects/{project_name}/files")
def output_files(project_name: str):
    project = _project(project_name)
    out = project / "output"
    if not out.exists():
        return []
    return [str(p.relative_to(project)) for p in sorted(out.rglob("*")) if p.is_file()]


@app.get("/projects/{project_name}/download")
def download(project_name: str, path: str):
    project = _project(project_name)
    target = (project / path).resolve()
    if project not in target.parents or not target.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(target)
