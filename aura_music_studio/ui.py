from __future__ import annotations

import json
from pathlib import Path

import gradio as gr

from .advanced_ui import add_advanced_tabs
from .assets import AssetLibrary
from .autopilot import AuraAutopilot
from .branding import AI_PRODUCER_NAME, PRODUCT_FULL_NAME, PRODUCT_NAME, TAGLINE
from .creation import CreateSongRequest, build_song_project
from .doctor import system_report
from .mastering import master, translation_report
from .pipeline import AuraPipeline
from .producer import llm_plan
from .rights import RightsLedger
from .separation import StemSeparator
from .voice import create_voice_profile


def _run_project(project_path: str):
    if not project_path.strip():
        return "Select a project folder.", None, None
    try:
        result = AuraPipeline(Path(project_path)).run()
        exports = result.get("exports", {})
        return json.dumps(result, indent=2, default=str), exports.get("master_mp3") or exports.get("master_wav"), exports.get("bandlab_pack")
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}", None, None


def _analyze(project_path: str):
    try:
        return json.dumps(AuraPipeline(Path(project_path)).analyze_only(), indent=2, default=str)
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"


def _producer(request: str, project_path: str):
    try:
        summary = {"project": project_path}
        status = Path(project_path) / "aura_status.json"
        if status.exists():
            summary["status"] = json.loads(status.read_text(encoding="utf-8"))
        plan = llm_plan(request, summary)
        return plan.model_dump_json(indent=2)
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"


def _create_song(
    title,
    concept,
    lyrics,
    generate_lyrics,
    lyrics_rights_confirmed,
    genre,
    subgenre,
    mood,
    instruments,
    energy,
    bpm,
    key,
    duration,
    vocal_mode,
    reference_file,
    reference_rights_confirmed,
    extra_prompt,
):
    try:
        request = CreateSongRequest(
            title=title,
            concept=concept,
            lyrics=lyrics,
            generate_lyrics=bool(generate_lyrics),
            lyrics_rights_confirmed=bool(lyrics_rights_confirmed),
            genre=genre,
            subgenre=subgenre,
            mood=mood,
            instruments=[x.strip() for x in (instruments or "").split(",") if x.strip()],
            energy=float(energy),
            bpm=float(bpm) if bpm else None,
            key=key or None,
            duration_seconds=int(duration),
            vocal_mode=vocal_mode,
            reference_audio=str(reference_file) if reference_file else None,
            reference_audio_rights_confirmed=bool(reference_rights_confirmed),
            extra_prompt=extra_prompt or "",
        )
        project = build_song_project(request, Path("projects"))
        if reference_file:
            record = AssetLibrary(project).ingest(
                Path(reference_file),
                kind="audio",
                rights_basis="user_owned_or_licensed",
                attestation="I confirm I own or have permission/license to use this reference audio in this project.",
                tags=["song_reference", "rights_confirmed"],
            )
            import yaml
            manifest_path = project / "project.yaml"
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            manifest["reference_audio"] = record.path
            manifest.setdefault("rights_clearance", {})["reference_asset"] = {
                "asset_id": record.id,
                "rights_record_id": record.rights_record_id,
                "sha256": record.sha256,
            }
            manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return f"Created: {project}", str(project)
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}", ""


def _upload(project_path, file_path, kind, rights_basis, attestation, tags):
    try:
        if not file_path:
            return "Choose a file first."
        record = AssetLibrary(Path(project_path)).ingest(
            Path(file_path), kind=kind, rights_basis=rights_basis, attestation=attestation,
            tags=[x.strip() for x in (tags or "").split(",") if x.strip()],
        )
        return json.dumps(record.model_dump(), indent=2)
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"


def _list_assets(project_path):
    try:
        return json.dumps([x.model_dump() for x in AssetLibrary(Path(project_path)).list()], indent=2)
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"


def _separate(project_path, audio_file, mode):
    try:
        if not audio_file:
            return "Choose an audio file.", None
        root = Path(project_path)
        stems = StemSeparator(root / "work" / "manual_separation").separate(Path(audio_file), mode=mode)
        archive = root / "output" / "Aura_Separated_Stems.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        import zipfile
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for role, path in stems.items():
                z.write(path, arcname=f"{role}{path.suffix}")
        return json.dumps({k: str(v) for k, v in stems.items()}, indent=2), str(archive)
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}", None


def _master(project_path, audio_file, preset, reference_file):
    try:
        if not audio_file:
            return "Choose an audio file.", None
        root = Path(project_path)
        output = root / "output" / f"{Path(audio_file).stem}_AuraMaster.wav"
        mastered, report = master(Path(audio_file), output, preset=preset, reference=Path(reference_file) if reference_file else None)
        report["translation"] = translation_report(mastered)
        return json.dumps(report, indent=2), str(mastered)
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}", None


def _create_voice(project_path, name, owner, voice_file, consent):
    try:
        if not voice_file:
            return "Choose your authorized voice reference recording."
        profile = create_voice_profile(
            RightsLedger(Path(project_path) / ".aura_rights"),
            name=name, owner_label=owner, reference_files=[Path(voice_file)], consent_statement=consent,
        )
        return json.dumps(profile.model_dump(), indent=2)
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"


def _scan(inbox: str):
    worker = AuraAutopilot(inbox or "projects")
    return "\n".join(str(p) for p in worker.discover()) or "No projects found."


def _doctor():
    return json.dumps(system_report(), indent=2, default=str)


def build_ui() -> gr.Blocks:
    with gr.Blocks(title=PRODUCT_NAME) as app:
        gr.Markdown(
            f"# 🎵 {PRODUCT_FULL_NAME}\n"
            f"### {TAGLINE}\n\n"
            f"**{AI_PRODUCER_NAME} is the studio's autonomous AI producer.** Real-audio-first generative production: MIDI/notation may control performances, but symbolic audio is never exported as the final master."
        )

        with gr.Tab("✨ Create Song"):
            with gr.Row():
                title = gr.Textbox(label="Song title")
                concept = gr.Textbox(label="Song idea / concept")
            lyrics = gr.Textbox(label="Lyrics", lines=14, placeholder="Paste lyrics you wrote/have permission to use, or leave empty and enable AI lyrics.")
            with gr.Row():
                generate_lyrics = gr.Checkbox(label="Create lyrics with AI", value=False)
                lyrics_rights = gr.Checkbox(
                    label="I own or have permission/license to use any lyrics I pasted",
                    value=False,
                )
            with gr.Row():
                genre = gr.Dropdown(["pop", "rock", "acoustic", "country", "R&B", "soul", "hip-hop", "EDM", "metal", "folk", "jazz", "blues", "classical", "cinematic", "ambient", "reggae", "latin", "indie"], value="pop", label="Genre", allow_custom_value=True)
                subgenre = gr.Textbox(label="Subgenre / style")
                mood = gr.Textbox(label="Mood", value="uplifting")
            instruments = gr.Textbox(label="Instruments", placeholder="real drums, finger bass, acoustic guitar, electric guitars, piano, strings")
            with gr.Row():
                energy = gr.Slider(0, 1, value=.7, step=.05, label="Energy")
                bpm = gr.Number(label="BPM (optional)")
                key = gr.Textbox(label="Key (optional)")
                duration = gr.Slider(30, 600, value=210, step=10, label="Length (seconds)")
            vocal_mode = gr.Radio(["ai_vocal", "instrumental", "approved_voice"], value="ai_vocal", label="Vocals")
            reference = gr.Audio(label="Optional style/reference audio", type="filepath")
            reference_rights = gr.Checkbox(
                label="I own or have permission/license to use the uploaded reference audio",
                value=False,
            )
            extra = gr.Textbox(
                label="Extra production direction",
                lines=3,
                placeholder="Describe genre, tempo, instrumentation, mood, vocal range and production characteristics. Do not request direct imitation of a real artist or existing song.",
            )
            gr.Markdown(
                "Copyright/IP safety: requests to reproduce existing songs/lyrics or directly imitate a real creator are blocked before generation. "
                "Authorized voice cloning must use a consent-approved Aura Voice Profile."
            )
            create_btn = gr.Button(f"Create {PRODUCT_NAME} Project", variant="primary")
            create_status = gr.Textbox(label="Status")
            created_path = gr.Textbox(label="Project folder")
            create_btn.click(
                _create_song,
                [
                    title, concept, lyrics, generate_lyrics, lyrics_rights, genre, subgenre, mood,
                    instruments, energy, bpm, key, duration, vocal_mode, reference, reference_rights, extra,
                ],
                [create_status, created_path],
            )

        with gr.Tab("💬 Aura Producer"):
            producer_project = gr.Textbox(label="Current project", value="projects/nothings-gonna-stop-us-now")
            producer_request = gr.Textbox(label="Tell Aura what to do", lines=5, placeholder="Make the chorus bigger, add wide guitars and backing harmonies; replace guitar from 1:20 to 1:35; master this like a modern rock record...")
            producer_btn = gr.Button("Plan Studio Action", variant="primary")
            producer_result = gr.Code(label="Aura Producer Plan", language="json")
            producer_btn.click(_producer, [producer_request, producer_project], producer_result)
            gr.Markdown("Aura plans non-destructive operations first. Region replacement/extension is rendered only through configured **real-audio** generation engines.")

        with gr.Tab("🎚️ Produce / Backing Track"):
            project = gr.Textbox(label="Project folder", value="projects/nothings-gonna-stop-us-now")
            with gr.Row():
                analyze_btn = gr.Button("Analyze & Arrange")
                run_btn = gr.Button("Generate REAL Audio", variant="primary")
            status = gr.Code(label="Aura production status", language="json")
            audio = gr.Audio(label="Final real-audio master")
            pack = gr.File(label="BandLab / multitrack pack")
            analyze_btn.click(_analyze, project, status)
            run_btn.click(_run_project, project, [status, audio, pack])

        with gr.Tab("📥 Upload Library"):
            upload_project = gr.Textbox(label="Project folder", value="projects/nothings-gonna-stop-us-now")
            upload_file = gr.File(label="Sample / track / score / MIDI / MusicXML / lyrics", type="filepath")
            with gr.Row():
                upload_kind = gr.Dropdown(["auto", "audio", "score", "symbolic", "text"], value="auto", label="Asset type")
                rights_basis = gr.Textbox(label="Rights basis", value="user_owned_or_licensed")
            attestation = gr.Textbox(label="Rights confirmation", value="I confirm I have the right to use this material in this project.")
            tags = gr.Textbox(label="Tags", placeholder="guitar, sample, reference, verse idea")
            with gr.Row():
                upload_btn = gr.Button("Add to Studio Library", variant="primary")
                list_btn = gr.Button("List Project Assets")
            asset_status = gr.Code(label="Asset record", language="json")
            upload_btn.click(_upload, [upload_project, upload_file, upload_kind, rights_basis, attestation, tags], asset_status)
            list_btn.click(_list_assets, upload_project, asset_status)

        with gr.Tab("🧬 Voice Studio"):
            voice_project = gr.Textbox(label="Project folder", value="projects/nothings-gonna-stop-us-now")
            with gr.Row():
                voice_name = gr.Textbox(label="Voice Profile name")
                voice_owner = gr.Textbox(label="Voice owner")
            voice_file = gr.Audio(label="Authorized voice reference", type="filepath")
            consent = gr.Textbox(label="Consent statement", value="I am the voice owner or have explicit permission to use this voice for this music project.", lines=3)
            voice_btn = gr.Button("Scan & Create Consent-Locked Voice Profile", variant="primary")
            voice_status = gr.Code(label="Voice scan / profile", language="json")
            voice_btn.click(_create_voice, [voice_project, voice_name, voice_owner, voice_file, consent], voice_status)

        with gr.Tab("🧩 Stem Separation"):
            stem_project = gr.Textbox(label="Project folder", value="projects/nothings-gonna-stop-us-now")
            stem_audio = gr.Audio(label="Audio to separate", type="filepath")
            stem_mode = gr.Radio(["six_stems", "two_stems"], value="six_stems", label="Separation")
            stem_btn = gr.Button("Separate Real Audio Stems", variant="primary")
            stem_status = gr.Code(label="Stem results", language="json")
            stem_zip = gr.File(label="Stem ZIP")
            stem_btn.click(_separate, [stem_project, stem_audio, stem_mode], [stem_status, stem_zip])

        with gr.Tab("💿 Mastering"):
            master_project = gr.Textbox(label="Project folder", value="projects/nothings-gonna-stop-us-now")
            master_audio = gr.Audio(label="Mix to master", type="filepath")
            master_reference = gr.Audio(label="Optional reference master", type="filepath")
            preset = gr.Dropdown(["streaming", "pop", "rock", "acoustic", "ballad", "electronic", "hiphop", "cinematic", "karaoke"], value="streaming", label="Master preset")
            master_btn = gr.Button("Master & Translation Check", variant="primary")
            master_status = gr.Code(label="Master report", language="json")
            mastered_file = gr.Audio(label="Mastered WAV")
            master_btn.click(_master, [master_project, master_audio, preset, master_reference], [master_status, mastered_file])

        add_advanced_tabs()

        with gr.Tab("🤖 Autopilot"):
            inbox = gr.Textbox(label="Projects inbox", value="projects")
            scan_btn = gr.Button("Scan Projects")
            discovered = gr.Textbox(label="Discovered projects", lines=12)
            scan_btn.click(_scan, inbox, discovered)

        with gr.Tab("🩺 System"):
            doctor_btn = gr.Button("Run Aura Doctor")
            doctor_report = gr.Code(label="Installed engines / GPU / audio stack", language="json")
            doctor_btn.click(_doctor, outputs=doctor_report)
            gr.Markdown(
                f"{PRODUCT_NAME} routes final music only through **real-audio generators, recorded audio, or hybrid audio paths**. "
                "Score/MIDI/MusicXML remain control layers and can never silently replace the final neural/recorded master."
            )
    return app
