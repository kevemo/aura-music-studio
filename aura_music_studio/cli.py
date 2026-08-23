from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import typer
from rich import print

from .autopilot import AuraAutopilot
from .backup import StudioBackupManager
from .branding import AI_PRODUCER_NAME, PRODUCT_FULL_NAME, PRODUCT_NAME, TAGLINE
from .compute_node_agent import ESPComputeNodeAgent, collect_hardware, collect_software, enroll_node
from .creation import CreateSongRequest, build_song_project
from .doctor import system_report
from .engine_manager import EngineManager
from .jobs import AuraJobWorker
from .pipeline import AuraPipeline
from .producer import llm_plan
from .public_address import PublicAddressManager
from .self_host_setup import initialize_self_host

app = typer.Typer(help=f"{PRODUCT_FULL_NAME} — {TAGLINE}. Powered by {AI_PRODUCER_NAME}.")


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()


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
        title=title,
        concept=concept,
        lyrics=lyrics,
        generate_lyrics=generate_lyrics,
        genre=genre,
        mood=mood,
        bpm=bpm,
        key=key,
        duration_seconds=duration,
        vocal_mode="instrumental" if instrumental else "ai_vocal",
    )
    project = build_song_project(request, projects_root)
    print(f"[bold green]Created {PRODUCT_NAME} project: {project}[/bold green]")


@app.command("producer-plan")
def producer_plan(request: str = typer.Argument(...)):
    """Translate natural-language producer direction into safe Studio actions."""
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
        print(json.dumps(manager.bootstrap(clone_engines=True, install_packages=install_packages), indent=2, default=str))
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
    """Check the complete Studio: engines, queue, web, speech, security and provenance."""
    print(json.dumps(system_report(), indent=2, default=str))


@app.command("self-host-init")
def self_host_init(
    provider: str = typer.Option("direct", "--provider", help="none, direct, freedns or duckdns"),
    hostname: str | None = typer.Option(None, "--hostname", help="Public free hostname for FreeDNS/other DNS"),
    duckdns_subdomain: str | None = typer.Option(None, "--duckdns-subdomain", help="DuckDNS subdomain without .duckdns.org"),
    env_path: Path = typer.Option(Path(".env"), "--env", help="Deployment environment file to create/update"),
):
    """Generate safe self-host settings and owner/provenance secrets without collecting DDNS tokens on CLI."""
    result = initialize_self_host(
        provider=provider,
        hostname=hostname,
        duckdns_subdomain=duckdns_subdomain,
        env_path=env_path,
    )
    payload = result.__dict__.copy()
    generated_key = payload.pop("admin_key", None)
    print(json.dumps(payload, indent=2, default=str))
    if generated_key:
        print("\n[bold yellow]New ESP owner admin key — store this safely; it is shown once here:[/bold yellow]")
        print(generated_key)
    if result.missing_private_settings:
        print("\n[yellow]Private settings still required in .env:[/yellow]")
        for item in result.missing_private_settings:
            print(f"- {item}")
    print(f"\n[bold green]Next: {result.next_command}[/bold green]")


@app.command("public-address")
def public_address_command(
    refresh: bool = typer.Option(True, "--refresh/--status-only", help="Detect address and update configured DDNS before printing status"),
    update_ddns: bool = typer.Option(True, "--update-ddns/--no-update-ddns", help="Allow the configured free-DDNS record to be refreshed"),
    serve: bool = typer.Option(False, "--serve", help="Run Aura's continuous DDNS/public-address manager"),
):
    """Inspect or continuously maintain the self-hosted Studio's public address."""
    manager = PublicAddressManager()
    if serve:
        print("[bold green]Aura Public Address Manager online[/bold green]")
        manager.serve_forever()
        return
    value = manager.check(update_ddns=update_ddns) if refresh else manager.read_status()
    payload = value.__dict__ if hasattr(value, "__dict__") else value
    print(json.dumps(payload, indent=2, default=str))


@app.command("backup")
def backup_studio(
    output: Path | None = typer.Option(None, "--output", help="Optional .zip output path"),
    include_outputs: bool = typer.Option(True, "--include-outputs/--no-outputs", help="Include rendered masters/stems"),
    include_work: bool = typer.Option(True, "--include-work/--no-work", help="Include project work/revision files"),
    age_recipient: str | None = typer.Option(None, "--age-recipient", help="Optional age public recipient for standard encrypted backup"),
    keep_plain: bool = typer.Option(False, "--keep-plain", help="Keep the unencrypted .zip when age encryption is used"),
):
    """Create an owner-controlled portable backup of accounts/billing/jobs and private projects."""
    result = StudioBackupManager().create(
        output=output,
        include_outputs=include_outputs,
        include_work=include_work,
        age_recipient=age_recipient,
        keep_plain_when_encrypted=keep_plain,
    )
    print(json.dumps(result, indent=2, default=str))


@app.command("backup-inspect")
def backup_inspect(
    archive: Path = typer.Argument(..., exists=True, dir_okay=False),
    age_identity: Path | None = typer.Option(None, "--age-identity", exists=True, dir_okay=False),
    verify_hashes: bool = typer.Option(True, "--verify/--no-verify"),
):
    """Inspect and optionally verify every file in an ESP Studio backup."""
    result = StudioBackupManager.inspect(archive, age_identity=age_identity, verify_hashes=verify_hashes)
    print(json.dumps(result, indent=2, default=str))


@app.command("restore-backup")
def restore_backup(
    archive: Path = typer.Argument(..., exists=True, dir_okay=False),
    offline: bool = typer.Option(False, "--offline-confirmed", help="Required: confirms web/worker services are stopped"),
    age_identity: Path | None = typer.Option(None, "--age-identity", exists=True, dir_okay=False),
    preserve_existing: bool = typer.Option(True, "--preserve-existing/--replace-existing"),
):
    """Restore a verified Studio backup. Refuses to run unless offline restore is explicitly confirmed."""
    if not offline:
        raise typer.BadParameter("Stop the Studio/web/worker services, then pass --offline-confirmed")
    result = StudioBackupManager().restore(
        archive,
        confirm_offline=True,
        age_identity=age_identity,
        preserve_existing=preserve_existing,
    )
    print(json.dumps(result, indent=2, default=str))


@app.command("node-enroll")
def node_enroll(
    coordinator: str = typer.Option(..., "--coordinator", help="HTTPS URL of the ESP Live Sound Studio coordinator"),
    name: str | None = typer.Option(None, "--name", help="Friendly ESP node name"),
    capabilities: str = typer.Option("music_generation,engineering", "--capabilities"),
    env_path: Path = typer.Option(Path(".env.node"), "--env", help="Permission-restricted node credential file"),
    token: str | None = typer.Option(None, "--token", help="Short-lived one-time enrollment token; omit to be prompted securely"),
):
    """Enroll this machine as a revocable outbound ESP compute node."""
    enrollment_token = token or typer.prompt("ESP one-time enrollment token", hide_input=True)
    result = enroll_node(
        coordinator,
        enrollment_token,
        name=name,
        capabilities=[x.strip() for x in capabilities.split(",") if x.strip()],
        env_path=env_path,
    )
    print(json.dumps(result, indent=2, default=str))
    print(f"[bold green]Node enrolled. Start it with: aura node-worker --env {env_path}[/bold green]")


@app.command("node-doctor")
def node_doctor():
    """Inspect the current machine's hardware/software capabilities before enrolling it."""
    print(json.dumps({"hardware": collect_hardware(), "software": collect_software()}, indent=2, default=str))


@app.command("node-run-once")
def node_run_once(
    env_path: Path = typer.Option(Path(".env.node"), "--env", exists=True, dir_okay=False),
):
    """Heartbeat, claim and execute at most one coordinator job for node testing."""
    _load_env_file(env_path)
    result = ESPComputeNodeAgent().run_once()
    print(json.dumps(result or {"job": None}, indent=2, default=str))


@app.command("node-worker")
def node_worker(
    env_path: Path = typer.Option(Path(".env.node"), "--env", exists=True, dir_okay=False),
):
    """Run this ESP-controlled machine as an outbound Aura compute worker."""
    _load_env_file(env_path)
    print("[bold green]ESP Aura compute node online[/bold green]")
    ESPComputeNodeAgent().serve_forever()


@app.command("render-worker")
def render_worker(
    worker_id: str | None = typer.Option(None, "--worker-id"),
    poll_seconds: float = typer.Option(2.0, "--poll-seconds", min=0.2),
):
    """Run Aura's asynchronous long-form neural production worker."""
    identifier = worker_id or f"{socket.gethostname()}-cli"
    print(f"[bold green]Aura render worker online: {identifier}[/bold green]")
    AuraJobWorker(worker_id=identifier).serve_forever(poll_seconds=poll_seconds)


@app.command()
def autopilot(
    inbox: Path = typer.Option(Path("projects"), "--inbox"),
    once: bool = typer.Option(False, "--once"),
    force: bool = typer.Option(False, "--force"),
    poll_seconds: int = typer.Option(60, "--poll-seconds"),
):
    """Process local project folders automatically."""
    worker = AuraAutopilot(inbox, poll_seconds=poll_seconds)
    if once:
        print(json.dumps(worker.run_once(force=force), indent=2, default=str))
    else:
        print(f"[bold green]{AI_PRODUCER_NAME} Autopilot watching {worker.inbox}[/bold green]")
        worker.serve_forever()


@app.command()
def ui(host: str = "0.0.0.0", port: int = 7860):
    """Launch the legacy Gradio Studio UI."""
    from .ui import build_ui
    build_ui().launch(server_name=host, server_port=port)


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000, reload: bool = False):
    """Launch the complete production web/account/studio service."""
    import uvicorn
    uvicorn.run("app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
