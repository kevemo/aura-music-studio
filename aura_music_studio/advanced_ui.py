from __future__ import annotations

import json
from pathlib import Path

import gradio as gr

from .assets import AssetLibrary
from .samples import SampleRequest, analyze_sample, generate_sample
from .styles import StyleBlend, StyleReference, build_style_dna, style_prompt


def _analyze_sample_ui(audio_file):
    try:
        if not audio_file:
            return "Choose an audio sample."
        return analyze_sample(Path(audio_file)).model_dump_json(indent=2)
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"


def _generate_sample_ui(project_path, kind, prompt, duration, bpm, key, instrument, bars):
    try:
        root = Path(project_path)
        out_dir = root / "output" / "samples"
        out_dir.mkdir(parents=True, exist_ok=True)
        number = len(list(out_dir.glob("Aura_*.wav"))) + 1
        out = out_dir / f"Aura_{kind}_{number:03d}.wav"
        request = SampleRequest(
            kind=kind,
            prompt=prompt,
            duration_seconds=float(duration),
            bpm=float(bpm) if bpm else None,
            key=key or None,
            instrument=instrument or None,
            bars=int(bars) if bars else None,
        )
        generated = generate_sample(request, out)
        report = analyze_sample(generated)
        return json.dumps({"path": str(generated), "analysis": report.model_dump(), "audio_origin": "neural"}, indent=2), str(generated)
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}", None


def _style_blend_ui(project_path, ref1, weight1, role1, ref2, weight2, role2, description, attestation):
    try:
        root = Path(project_path)
        library = AssetLibrary(root)
        refs = []
        if ref1:
            r1 = library.ingest(
                Path(ref1), kind="audio", rights_basis="user_owned_or_licensed",
                attestation=attestation, tags=["style_reference", role1],
            )
            refs.append(StyleReference(path=str(root / r1.path), weight=float(weight1), role=role1))
        if ref2:
            r2 = library.ingest(
                Path(ref2), kind="audio", rights_basis="user_owned_or_licensed",
                attestation=attestation, tags=["style_reference", role2],
            )
            refs.append(StyleReference(path=str(root / r2.path), weight=float(weight2), role=role2))
        blend = StyleBlend(references=refs, preserve_originality=True, description=description or "")
        dna = build_style_dna(blend)
        destination = root / "work" / "style_dna.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(dna, indent=2), encoding="utf-8")
        return json.dumps(dna, indent=2), style_prompt(dna)
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}", ""


def add_advanced_tabs():
    with gr.Tab("🥁 Sample Lab"):
        sample_project = gr.Textbox(label="Project folder", value="projects/nothings-gonna-stop-us-now")
        with gr.Row():
            sample_upload = gr.Audio(label="Analyze an uploaded real-audio sample", type="filepath")
            sample_analysis = gr.Code(label="Sample analysis", language="json")
        analyze_btn = gr.Button("Analyze Sample")
        analyze_btn.click(_analyze_sample_ui, sample_upload, sample_analysis)

        gr.Markdown("### Generate a new neural waveform sample")
        sample_kind = gr.Dropdown(["loop", "one_shot", "texture", "fill", "riff", "transition"], value="loop", label="Type")
        sample_prompt = gr.Textbox(label="Describe the sound", lines=3, placeholder="Live rock drum fill with natural room ambience and tom run")
        with gr.Row():
            sample_duration = gr.Number(value=8, label="Length seconds")
            sample_bpm = gr.Number(label="BPM")
            sample_key = gr.Textbox(label="Key")
            sample_bars = gr.Number(label="Bars (optional)")
        sample_instrument = gr.Textbox(label="Instrument / source")
        generate_btn = gr.Button("Generate REAL Audio Sample", variant="primary")
        sample_result = gr.Code(label="Generation result", language="json")
        sample_audio = gr.Audio(label="Generated sample")
        generate_btn.click(
            _generate_sample_ui,
            [sample_project, sample_kind, sample_prompt, sample_duration, sample_bpm, sample_key, sample_instrument, sample_bars],
            [sample_result, sample_audio],
        )
        gr.Markdown("Sample Lab refuses MIDI/SoundFont output as a generated sample. It needs a neural waveform renderer.")

    with gr.Tab("🧬 Style DNA"):
        style_project = gr.Textbox(label="Project folder", value="projects/nothings-gonna-stop-us-now")
        gr.Markdown("Blend high-level production characteristics from authorized references while keeping the new composition original.")
        with gr.Row():
            with gr.Column():
                style_ref1 = gr.Audio(label="Reference 1", type="filepath")
                style_weight1 = gr.Slider(0, 1, value=.65, step=.05, label="Influence 1")
                style_role1 = gr.Dropdown(["overall", "rhythm", "harmony", "instrumentation", "production", "vocal"], value="overall", label="Role 1")
            with gr.Column():
                style_ref2 = gr.Audio(label="Reference 2", type="filepath")
                style_weight2 = gr.Slider(0, 1, value=.35, step=.05, label="Influence 2")
                style_role2 = gr.Dropdown(["overall", "rhythm", "harmony", "instrumentation", "production", "vocal"], value="production", label="Role 2")
        style_description = gr.Textbox(label="Extra style direction", lines=3)
        style_attestation = gr.Textbox(
            label="Rights confirmation",
            value="I confirm I have the right to use these audio references for this project.",
        )
        style_btn = gr.Button("Build Aura Style DNA", variant="primary")
        style_dna = gr.Code(label="Weighted Style DNA", language="json")
        style_prompt_box = gr.Textbox(label="Generated production direction", lines=5)
        style_btn.click(
            _style_blend_ui,
            [style_project, style_ref1, style_weight1, style_role1, style_ref2, style_weight2, style_role2, style_description, style_attestation],
            [style_dna, style_prompt_box],
        )
