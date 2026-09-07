from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import __version__
from .access_control import MembershipAccessMiddleware
from .account_recovery import router as account_recovery_router
from .admin_portal import router as admin_portal_router
from .assets import AUDIO_EXTS, AssetLibrary
from .aura_live_guardian import router as aura_live_guardian_router
from .aura_live_guardian_policy_ui import router as aura_live_guardian_policy_router
from .aura_live_overlay_advanced import router as aura_live_overlay_advanced_router
from .aura_live_overlay_engine import router as aura_live_overlay_engine_router
from .aura_live_overlay_interactives import router as aura_live_overlay_interactives_router
from .aura_live_overlay_orchestration import router as aura_live_overlay_orchestration_router
from .aura_live_overlay_pro_source import router as aura_live_overlay_pro_source_router
from .aura_live_overlay_studio import router as aura_live_overlay_studio_router
from .aura_live_post_show_report import router as aura_live_post_show_report_router
from .aura_live_trend_coach import router as aura_live_trend_coach_router
from .aura_live_prompter import router as aura_live_prompter_router
from .aura_live_run_engine import router as aura_live_run_engine_router
from .aura_live_runtime_intelligence import router as aura_live_runtime_intelligence_router
from .aura_live_show_control import router as aura_live_show_control_router
from .aura_support_center import router as aura_support_center_router
from .branding import PRODUCT_FULL_NAME, PRODUCT_NAME, TAGLINE
from .cosmic_economy_api import router as cosmic_economy_router
from .cosmic_economy_legacy_bridge import router as cosmic_economy_legacy_router
from .cosmic_economy_owner_api import router as cosmic_economy_owner_router
from .creation import CreateSongRequest, build_song_project
from .doctor import system_report
from .engine_manager import EngineManager
from .engineering_api import router as engineering_router
from .esp_public_network import router as esp_public_network_router
from .job_api import router as job_api_router
from .mastering import master, translation_report
from .membership_api import router as membership_router
from .mixer import render_session
from .owner_commerce_member_communications import router as owner_commerce_member_communications_router
from .owner_feature_workshop import router as owner_feature_workshop_router
from .pipeline import AuraPipeline
from .producer import llm_plan
from .revisions import create_revision
from .rights import RightsLedger
from .samples import SampleRequest, analyze_sample, generate_sample, make_loop
from .security import StudioSecurityMiddleware
from .separation import StemSeparator
from .session import StudioSession
from .speech_api import router as speech_api_router
from .speech_portal import router as speech_portal_router
from .studio_portal import router as studio_portal_router
from .styles import StyleBlend, build_style_dna, style_prompt
from .tenant_storage import list_project_dirs, project_path, projects_root
from .transcription import audio_to_midi
from .upload_security import (
    UploadTooLargeError,
    asset_upload_limit,
    safe_upload_filename,
    save_bounded_upload,
    voice_upload_limit,
)
from .voice import create_voice_profile
from .web_api import router as web_api_router
from .web_portal import billing_history_json, billing_history_page, router as web_portal_router

app = FastAPI(
    title=f"{PRODUCT_NAME} API",
    version=__version__,
    description=f"{PRODUCT_FULL_NAME} — {TAGLINE}. Real-audio-first autonomous generative music studio API, powered by Aura.",
)
# Membership middleware establishes the authenticated member/tenant context. Security middleware
# is added last so it is the outer public-web envelope around every route.
app.add_middleware(MembershipAccessMiddleware)
app.add_middleware(StudioSecurityMiddleware)
app.include_router(web_portal_router)
# Keep the customer billing-history endpoints deterministic on the canonical application even
# when another importer has already snapshotted the shared web router. The handlers retain their
# own server-session/account scoping; this only guarantees production reachability.
_mounted_paths = {getattr(route, "path", None) for route in app.routes}
if "/auth/me/billing-history" not in _mounted_paths:
    app.add_api_route("/auth/me/billing-history", billing_history_json, methods=["GET"])
if "/auth/billing-history" not in _mounted_paths:
    app.add_api_route(
        "/auth/billing-history",
        billing_history_page,
        methods=["GET"],
        response_class=HTMLResponse,
    )
app.include_router(studio_portal_router)
app.include_router(speech_portal_router)
app.include_router(admin_portal_router)
app.include_router(membership_router)
app.include_router(cosmic_economy_router)
app.include_router(cosmic_economy_legacy_router)
app.include_router(cosmic_economy_owner_router)
app.include_router(owner_commerce_member_communications_router)
app.include_router(esp_public_network_router)
app.include_router(aura_support_center_router)
app.include_router(owner_feature_workshop_router)
app.include_router(aura_live_overlay_studio_router)
app.include_router(aura_live_overlay_advanced_router)
app.include_router(aura_live_overlay_pro_source_router)
app.include_router(aura_live_overlay_engine_router)
app.include_router(aura_live_overlay_interactives_router)
app.include_router(aura_live_overlay_orchestration_router)
app.include_router(aura_live_post_show_report_router)
app.include_router(aura_live_trend_coach_router)
app.include_router(aura_live_prompter_router)
app.include_router(aura_live_run_engine_router)
app.include_router(aura_live_runtime_intelligence_router)
app.include_router(aura_live_show_control_router)
app.include_router(aura_live_guardian_router)
app.include_router(aura_live_guardian_policy_router)
app.include_router(account_recovery_router)
app.include_router(engineering_router)
app.include_router(speech_api_router)
app.include_router(web_api_router)
app.include_router(job_api_router)


class ProducerRequest(BaseModel):
    request: str


def _project(name: str) -> Path:
    try:
        return project_path(name, must_exist=True)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc


def _session_path(project: Path) -> Path:
    return project / "aura_session.json"


def _upload_limit(factory) -> int:
    try:
        return int(factory())
    except ValueError as exc:
        raise HTTPException(503, "Upload security policy is unavailable") from exc


def _upload_name(filename: str | None, *, default: str) -> str:
    try:
        return safe_upload_filename(filename, default=default)
    except ValueError as exc:
        raise HTTPException(400, "Upload filename is invalid") from exc


async def _save_member_upload(upload: UploadFile, destination: Path, *, max_bytes: int, label: str) -> None:
    try:
        await save_bounded_upload(upload, destination, max_bytes=max_bytes)
    except UploadTooLargeError as exc:
        raise HTTPException(413, f"{label} exceeds the configured size limit") from exc
    except ValueError as exc:
        if str(exc) == "Upload is empty":
            raise HTTPException(400, f"{label} is empty") from exc
        raise


@app.get("/health")
def health():
    return {
        "ok": True,
        "product": PRODUCT_FULL_NAME,
        "tagline": TAGLINE,
        "ai_producer": "Aura",
        "real_audio_only_final": True,
        "membership_tiers": ["free", "base", "pro"],
        "customer_portal": True,
        "studio_workspace": True,
        "owner_portal": True,
        "official_esp_brand_theme": True,
        "spoken_aura": True,
        "spoken_aura_browser_control_room": True,
        "advanced_engineering_api": True,
        "background_engineering_jobs": True,
        "controlled_web_gateway": True,
        "per_member_project_isolation": True,
        "async_production_jobs": True,
        "project_revision_history": True,
        "pro_take_manager": True,
        "phrase_level_take_comping": True,
        "browser_recording_studio": True,
        "signed_provenance_manifests": True,
        "self_hosted_public_address_manager": True,
        "free_ddns_adapters": ["freedns", "duckdns"],
        "direct_ip_self_host_mode": True,
        "optional_local_caddy_gateway": True,
        "cloudflare_required": False,
        "paid_domain_required": False,
        "public_discovery_pages": True,
        "seo_sitemap_and_robots": True,
        "installable_pwa_manifest": True,
        "public_service_worker_private_data_cache": False,
        "security_headers": True,
        "cookie_write_origin_protection": True,
        "auth_rate_limiting": True,
        "bounded_legacy_uploads": True,
        "api_version": __version__,
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
        {
            "name": p.name,
            "has_manifest": (p / "project.yaml").exists(),
            "has_session": _session_path(p).exists(),
        }
        for p in list_project_dirs()
    ]


@app.post("/songs")
def create_song(request: CreateSongRequest):
    project = build_song_project(request, projects_root())
    # Keep the legacy `path` response key for compatibility, but expose only the logical
    # tenant-scoped project reference. Public API responses must never reveal host paths.
    return {"project": project.name, "path": project.name}


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
    """Legacy synchronous renderer. Public UI should prefer /render-jobs."""
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
    path = _session_path(project)
    if path.exists():
        try:
            create_revision(
                project,
                label="Before multitrack session save",
                reason="session_save",
                actor="Aura Studio",
                keep=200,
            )
        except Exception:
            pass
    session.save(path)
    return {"saved": True, "session_id": session.id, "revision_snapshot": path.exists()}


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
    safe_name = _upload_name(file.filename, default="upload.bin")
    if AssetLibrary.detect_kind(Path(safe_name)) == "unsupported":
        raise HTTPException(400, "Unsupported asset type")
    incoming = project / "input" / "uploads"
    tmp = incoming / f"{uuid4().hex}_{safe_name}"
    await _save_member_upload(
        file,
        tmp,
        max_bytes=_upload_limit(asset_upload_limit),
        label="Asset upload",
    )
    try:
        record = AssetLibrary(project).ingest(
            tmp,
            kind=kind,
            rights_basis=rights_basis,
            attestation=attestation,
            tags=[x.strip() for x in (tags or "").split(",") if x.strip()],
        )
        return record.model_dump()
    finally:
        tmp.unlink(missing_ok=True)


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
    return {
        "path": str(generated),
        "analysis": analyze_sample(generated).model_dump(),
        "audio_origin": "neural",
    }


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
    """Legacy synchronous splitter. Production clients should prefer background engineering jobs."""
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
def master_asset(
    project_name: str,
    asset_id: str,
    preset: str = "streaming",
    reference_asset_id: str | None = None,
):
    """Legacy synchronous master. Production clients should prefer background engineering jobs."""
    project = _project(project_name)
    library = AssetLibrary(project)
    source_record = library.get(asset_id)
    if source_record.kind != "audio":
        raise HTTPException(400, "Mastering requires an audio asset")
    reference = project / library.get(reference_asset_id).path if reference_asset_id else None
    out = project / "output" / f"{Path(source_record.name).stem}_AuraMaster.wav"
    mastered, report = master(project / source_record.path, out, preset=preset, reference=reference)
    return {
        "path": str(mastered),
        "report": report,
        "translation": translation_report(mastered),
    }


@app.post("/projects/{project_name}/voice-profiles")
async def new_voice_profile(
    project_name: str,
    name: str = Form(...),
    owner_label: str = Form(...),
    consent_statement: str = Form(...),
    reference: UploadFile = File(...),
):
    project = _project(project_name)
    safe_name = _upload_name(reference.filename, default="voice.wav")
    if Path(safe_name).suffix.lower() not in AUDIO_EXTS:
        raise HTTPException(400, "Voice reference must use a supported audio file type")
    voice_dir = project / "input" / "voice_profiles"
    target = voice_dir / f"{uuid4().hex}_{safe_name}"
    await _save_member_upload(
        reference,
        target,
        max_bytes=_upload_limit(voice_upload_limit),
        label="Voice reference",
    )
    try:
        profile = create_voice_profile(
            RightsLedger(project / ".aura_rights"),
            name=name,
            owner_label=owner_label,
            reference_files=[target],
            consent_statement=consent_statement,
        )
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return profile.model_dump()


@app.get("/projects/{project_name}/files")
def output_files(project_name: str):
    project = _project(project_name)
    out = project / "output"
    if not out.exists():
        return []
    return [str(p.relative_to(out)) for p in sorted(out.rglob("*")) if p.is_file()]


@app.get("/projects/{project_name}/download")
def legacy_download(project_name: str, path: str):
    """Legacy output-only download route. New clients should use /outputs/file/... ."""
    project = _project(project_name)
    output_root = (project / "output").resolve()
    target = (output_root / path).resolve()
    if output_root not in target.parents or not target.is_file():
        raise HTTPException(404, "Output file not found")
    return FileResponse(target)