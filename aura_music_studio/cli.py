from __future__ import annotations

import json
from pathlib import Path

import typer
from rich import print

from .autopilot import AuraAutopilot
from .branding import AI_PRODUCER_NAME, PRODUCT_FULL_NAME, PRODUCT_NAME, TAGLINE
from .creation import CreateSongRequest, build_song_project
from .doctor import system_report
from .engine_manager import EngineManager
from .pipeline import AuraPipeline
from .producer import llm_plan

app = typer.Typer(help=f"{PRODUCT_FULL_NAME} — {TAGLINE}. Powered by {AI_PRODUCER_NAME}.")


@app.command()
def run(project: Path = typer.Argument(..., exists=True, file_okay=False)):
    """Run analysis -> arrangement -> REAL neural audio -> QC -> stems -> mastering -> export."""
    result = AuraPipeline(project).run()
    print(json.dumps(result, indent=2, default=str))


@app.command()
def analyze(project: Path = typer.Argument(..., exists=True, file_okay=False)):
    """Analyze and plan a project without rendering final audio."""
    result = AuraPipeline(project).analyze_only()
    print(json.dumps(result, indent=2, default=str))


@app.command("create-song")
def create_song(
    title: str = typer.Option(..., "--title"),
    concept: str = typer.Option("", "--concept"),
    lyrics_file: Path | None = typer.Option(None, "--lyrics-file"),
    genre: str = typer.Option("pop", "--genre"),
    mood: str = typer.Option("uplifting", "--mood"),
    bpm: float | None = typer.Option(None, "--bpm"),
    key: str | None = typer.Option(None, "--key"),
    duration: int = typer.Option(210, "--duration"),
    instrumental: bool = typer.Option(False, "--instrumental"),
    generate_lyrics: bool = typer.Option(False, "--generate-lyrics"),
    projects_root: Path = typer.Option(Path("projects"), "--projects-root"),
):
    """Create a new original-song project ready for neural audio generation."""
    lyrics = lyrics_file.read_text(encoding="utf-8") if lyrics_file else ""
    request = CreateSongRequest(
        title=title, concept=concept, lyrics=lyrics, generate_lyrics=generate_lyrics,
        genre=genre, mood=mood, bpm=bpm, key=key, duration_seconds=duration,
        vocal_mode="instrumental" if instrumental else "ai_vocal",
    )
    project = build_song_project(request, projects_root)
    print(f"[bold green]Created {PRODUCT_NAME} project: {project}[/bold green]")


@app.command("producer-plan")
def producer_plan(request: str = typer.Argument(...)):
    """Translate a natural-language producer request into safe non-destructive studio actions."""
    plan = llm_plan(request)
    print(plan.model_dump_json(indent=2))


@app.command("engines")
def engines(
    bootstrap: bool = typer.Option(False, "--bootstrap", help="Clone supported local engines into ./engines"),
    install_packages: bool = typer.Option(False, "--install-packages", help="Install supported pip audio tools"),
    update: bool = typer.Option(False, "--update", help="Pull cloned engine repositories"),
):
    """Inspect or bootstrap Aura's local/open-source AI engine stack."""
    manager = EngineManager()
    if bootstrap:
        result = manager.bootstrap(clone_engines=True, install_packages=install_packages)
        print(json.dumps(result, indent=2, default=str))
        return
    if update:
        actions = []
        for item in manager.status():
            if item.get("repo") and item.get("installed"):
                try:
                    path = manager.clone(item["name"], update=True)
                    actions.append({"engine": item["name"], "updated": True, "path": str(path)})
                except Exception as exc:
                    actions.append({"engine": item["name"], "updated": False, "error": str(exc)})
        print(json.dumps(actions, indent=2))
        return
    print(json.dumps(manager.status(), indent=2, default=str))


@app.command()
def doctor():
    """Check GPU, audio tools and configured neural engines."""
    print(json.dumps(system_report(), indent=2, default=str))


@app.command()
def autopilot(
    inbox: Path = typer.Option(Path("projects"), "--inbox"),
    once: bool = typer.Option(False, "--once"),
    force: bool = typer.Option(False, "--force"),
    poll_seconds: int = typer.Option(60, "--poll-seconds"),
):
    """Process project folders automatically."""
    worker = AuraAutopilot(inbox, poll_seconds=poll_seconds)
    if once:
        print(json.dumps(worker.run_once(force=force), indent=2, default=str))
    else:
        print(f"[bold green]{AI_PRODUCER_NAME} Autopilot watching {worker.inbox}[/bold green]")
        worker.serve_forever()


@app.command()
def ui(host: str = "0.0.0.0", port: int = 7860):
    """Launch The Live Sound Studio web UI."""
    from .ui import build_ui
    build_ui().launch(server_name=host, server_port=port)


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000, reload: bool = False):
    """Launch The Live Sound Studio REST API for desktop/web/mobile front ends."""
    import uvicorn
    uvicorn.run("aura_music_studio.api:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
