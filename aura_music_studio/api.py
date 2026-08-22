from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .access_control import MembershipAccessMiddleware
from .assets import AssetLibrary
from .branding import PRODUCT_FULL_NAME, PRODUCT_NAME, TAGLINE
from .creation import CreateSongRequest, build_song_project
from .doctor import system_report
from .engine_manager import EngineManager
from .mastering import master, translation_report
from .membership_api import router as membership_router
from .mixer import render_session
from .pipeline import AuraPipeline
from .producer import llm_plan
from .rights import RightsLedger
from .samples import SampleRequest, analyze_sample, generate_sample, make_loop
from .separation import StemSeparator
from .session import StudioSession
from .styles import StyleBlend, build_style_dna, style_prompt
from .transcription import audio_to_midi
from .voice import create_voice_profile

PROJECTS_ROOT = Path(os.getenv("AURA_PROJECTS_ROOT", "projects")).resolve()
PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title=f"{PRODUCT_NAME} API",
    version="0.5.0",
    description=f"{PRODUCT_FULL_NAME} — {TAGLINE}. Real-audio-first multi-model generative music studio API, powered by Aura.",
)
app.add_middleware(MembershipAccessMiddleware)
app.include_router(membership_router)


class ProducerRequest(BaseModel):
    request: str


def _project(name: str) -> Path:
    p = (PROJECTS_ROOT / name).resolve()
    if PROJECTS_ROOT not in p.parents and p != PROJECTS_ROOT:
        raise HTTPException(400, "Invalid project path")
    if not p.exists():
        raise HTTPException(404, "Project not found")
    return p


def _session_path(project: Path) -> Path:
    return project / "aura_session.json"


@app.get("/health")
def health():
    return {
        "ok": True,
        "product": PRODUCT_FULL_NAME,
        "tagline": TAGLINE,
        "ai_producer": "Aura",
        "real_audio_only_final": True,
        "membership_tiers": ["free", "base", "pro"],
        "api_version": "0.5.0",
    }


@app.get("/capabilities")
def capabilities():
    return system_report()


@app.get("/engines")
def engines():
    return EngineManager().status()


@app.get("/projects")
def list_projects():
    return [
        {"name": p.name, "has_manifest": (p / "project.yaml").exists(), "has_session": _session_path(p).exists()}
        for p in sorted(PROJECTS_ROOT.iterdir()) if p.is_dir()
    ]


@app.post("/songs")
def create_song(request: CreateSongRequest):
    project = build_song_project(request, PROJECTS_ROOT)
    return {"project": project.name, "path": str(project)}


@app.post("/projects/{project_name}/producer")
def producer(project_name: str, request: ProducerRequest):
    project = _project(project_name)
    summary = {"project": project_name}
    status = project / "aura_status.json"
    if status.exists():
        try:
            summary["status"] = json.loads(status.read_text(encoding="utf-8"))
        except Exception:
            pass
    return llm_plan(request.request, summary).model_dump()


@app.post("/projects/{project_name}/produce")
def produce(project_name: str):
    return AuraPipeline(_project(project_name)).run()


@app.post("/projects/{project_name}/analyze")
def analyze(project_name: str):
    return AuraPipeline(_project(project_name)).analyze_only()


@app.get("/projects/{project_name}/session")
def get_session(project_name: str):
    project = _project(project_name)
    path = _session_path(project)
    if path.exists():
        return StudioSession.load(path).model_dump()
    session = StudioSession(name=project_name)
    session.add_track("Master", "master")
    session.save(path)
    return session.model_dump()


@app.put("/projects/{project_name}/session")
def put_session(project_name: str, session: StudioSession):
    project = _project(project_name)
    session.save(_session_path(project))
    return {"saved": True, "session_id": session.id}


@app.post("/projects/{project_name}/session/render")
def mix_session(project_name: str):
    project = _project(project_name)
    path = _session_path(project)
    if not path.exists():
        raise HTTPException(404, "Studio session not found")
    session = StudioSession.load(path)
    output = project / "output" / "Aura_Session_Mix.wav"
    render_session(session, project, output)
    return {"path": str(output), "audio_origin": "real_audio_session_mix"}


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
        tmp, kind=kind, rights_basis=rights_basis, attestation=attestation,
        tags=[x.strip() for x in tags.split(",") if x.strip()],
    )
    tmp.unlink(missing_ok=True)
    return record.model_dump()


@app.get("/projects/{project_name}/assets")
def assets(project_name: str):
    return [x.model_dump() for x in AssetLibrary(_project(project_name)).list()]


@app.post("/projects/{project_name}/sample/analyze")
def sample_analyze(project_name: str, asset_id: str):
    project = _project(project_name)
    record = AssetLibrary(project).get(asset_id)
    if record.kind != "audio":
        raise HTTPException(400, "Sample analysis requires audio")
    return analyze_sample(project / record.path).model_dump()


@app.post("/projects/{project_name}/sample/generate")
def sample_generate(project_name: str, request: SampleRequest):
    project = _project(project_name)
    out_dir = project / "output" / "samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"Aura_{request.kind}_{len(list(out_dir.glob('*.wav'))) + 1:03d}.wav"
    generated = generate_sample(request, out)
    return {"path": str(generated), "analysis": analyze_sample(generated).model_dump(), "audio_origin": "neural"}


@app.post("/projects/{project_name}/sample/loop")
def sample_loop(project_name: str, asset_id: str, bars: int, bpm: float):
    project = _project(project_name)
    record = AssetLibrary(project).get(asset_id)
    if record.kind != "audio":
        raise HTTPException(400, "Loop creation requires audio")
    out = project / "output" / "samples" / f"{Path(record.name).stem}_{bars}bar_{bpm:g}bpm.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    make_loop(project / record.path, out, bars=bars, bpm=bpm)
    return {"path": str(out), "analysis": analyze_sample(out).model_dump()}


@app.post("/projects/{project_name}/style-blend")
def style_blend(project_name: str, blend: StyleBlend):
    project = _project(project_name)
    dna = build_style_dna(blend)
    dest = project / "work" / "style_dna.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(dna, indent=2), encoding="utf-8")
    return {"style_dna": dna, "prompt": style_prompt(dna), "path": str(dest)}


@app.post("/projects/{project_name}/separate")
def separate(project_name: str, asset_id: str, mode: str = "six_stems"):
    project = _project(project_name)
    library = AssetLibrary(project)
    record = library.get(asset_id)
    if record.kind != "audio":
        raise HTTPException(400, "Stem separation requires an audio asset")
    stems = StemSeparator(project / "work" / "separation").separate(project / record.path, mode=mode)
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
    reference = project / library.get(reference_asset_id).path if reference_asset_id else None
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
        name=name, owner_label=owner_label, reference_files=[target], consent_statement=consent_statement,
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
