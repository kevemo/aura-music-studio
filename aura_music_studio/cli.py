from __future__ import annotations

import json
from pathlib import Path

import typer
from rich import print

from .autopilot import AuraAutopilot
from .doctor import system_report
from .pipeline import AuraPipeline

app = typer.Typer(help="Aura Music Studio — autonomous AI music production")


@app.command()
def run(project: Path = typer.Argument(..., exists=True, file_okay=False)):
    """Run analysis -> arrangement -> neural render -> QC -> stems -> mastering -> export."""
    result = AuraPipeline(project).run()
    print(json.dumps(result, indent=2, default=str))


@app.command()
def analyze(project: Path = typer.Argument(..., exists=True, file_okay=False)):
    """Analyze and plan a project without rendering audio."""
    result = AuraPipeline(project).analyze_only()
    print(json.dumps(result, indent=2, default=str))


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
        print(f"[bold green]Aura Autopilot watching {worker.inbox}[/bold green]")
        worker.serve_forever()


@app.command()
def ui(host: str = "0.0.0.0", port: int = 7860):
    """Launch the local Aura Music Studio web UI."""
    from .ui import build_ui
    build_ui().launch(server_name=host, server_port=port)


if __name__ == "__main__":
    app()
