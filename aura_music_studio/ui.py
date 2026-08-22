from __future__ import annotations

import json
from pathlib import Path

import gradio as gr

from .autopilot import AuraAutopilot
from .pipeline import AuraPipeline


def _run_project(project_path: str):
    if not project_path.strip():
        return "Select a project folder.", None, None
    try:
        result = AuraPipeline(Path(project_path)).run()
        exports = result.get("exports", {})
        return (
            json.dumps(result, indent=2, default=str),
            exports.get("master_mp3") or exports.get("master_wav"),
            exports.get("bandlab_pack"),
        )
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}", None, None


def _analyze(project_path: str):
    try:
        result = AuraPipeline(Path(project_path)).analyze_only()
        return json.dumps(result, indent=2, default=str)
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"


def _scan(inbox: str):
    worker = AuraAutopilot(inbox or "projects")
    return "\n".join(str(p) for p in worker.discover()) or "No projects found."


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Aura Music Studio") as app:
        gr.Markdown(
            "# 🎵 Aura Music Studio\n"
            "Score/reference analysis → arrangement → neural rendering → stems → mastering → BandLab export."
        )
        with gr.Tab("Produce"):
            project = gr.Textbox(label="Project folder", value="projects/nothings-gonna-stop-us-now")
            with gr.Row():
                analyze_btn = gr.Button("Analyze & Plan")
                run_btn = gr.Button("Produce Full Track", variant="primary")
            status = gr.Code(label="Aura status", language="json")
            audio = gr.Audio(label="Final master")
            pack = gr.File(label="BandLab stem pack")
            analyze_btn.click(_analyze, inputs=project, outputs=status)
            run_btn.click(_run_project, inputs=project, outputs=[status, audio, pack])

        with gr.Tab("Autopilot"):
            inbox = gr.Textbox(label="Projects inbox", value="projects")
            scan_btn = gr.Button("Scan Projects")
            discovered = gr.Textbox(label="Discovered projects", lines=10)
            scan_btn.click(_scan, inputs=inbox, outputs=discovered)

        with gr.Tab("System"):
            gr.Markdown(
                "**Renderer priority:** deAPI → ACE-Step hosted Spaces → local/self-hosted GPU.\n\n"
                "For reliable high-quality rendering, set `DEAPI_API_KEY` or `AURA_LOCAL_RENDER_CMD`. "
                "Public ZeroGPU Spaces are retained as a fallback, not the primary production backend."
            )
    return app
