from __future__ import annotations

import re
import secrets
from pathlib import Path

from . import aura_agent_tools as tools
from .creative_project import CreativeDirective, CreativeElement, CreativeProjectStore
from .creative_renderers import renderer_for, renderer_states
from .job_api import queue as studio_job_queue
from .project import ProjectWorkspace
from .rights import RightsLedger
from .song_dna import SongDNAStore

_INSTALLED = False


def _spec(name: str, description: str, arguments: dict[str, str], *, write: bool = False, web: bool = False):
    return tools.ToolSpec(name=name, description=description, arguments=arguments, write=write, web=web)


EXTRA_SPECS = [
    _spec(
        "creative_renderer_status",
        "Report whether the self-hosted image/video renderer workflows are configured; optionally probe the renderer host. Internal network addresses are never returned.",
        {"probe": "Optional boolean. True performs a live renderer connectivity check."},
    ),
    _spec(
        "plan_creative_directive",
        "Create a non-destructive cross-media Creative DNA directive for image, video, audio, music, voice or text while preserving named elements/references.",
        {
            "project_name": "Project name/slug.",
            "target_kind": "image|video|audio|music|voice|text",
            "operation": "create|revise|replace|extend|transform|arrange|mix|master|sync|storyboard|style|analyze",
            "instruction": "The requested creative change.",
            "target_element_ids": "Optional list of element ids to change.",
            "preserve_element_ids": "Optional list of elements Aura must preserve.",
            "reference_ids": "Optional rights-confirmed creative reference ids.",
        },
        write=True,
    ),
    _spec(
        "create_visual",
        "Create/queue an image or video through the project's Creative DNA and configured ComfyUI renderer. If the renderer is not configured, save the directive truthfully as planned instead of pretending output exists.",
        {
            "project_name": "Project name/slug.",
            "kind": "image or video",
            "prompt": "Creation/edit instruction.",
            "negative_prompt": "Optional unwanted traits.",
            "width": "Optional width (64-4096).",
            "height": "Optional height (64-4096).",
            "frames": "Video frame count, optional.",
            "fps": "Video fps, optional.",
            "target_element_ids": "Optional existing element ids to revise/replace.",
            "preserve_element_ids": "Optional element ids that must not change.",
            "reference_ids": "Optional rights-confirmed Creative DNA reference ids.",
        },
        write=True,
    ),
    _spec(
        "creative_render_status",
        "Check a queued image/video Aura directive and return renderer state/output descriptors without exposing internal renderer addresses.",
        {"project_name": "Project name/slug.", "directive_id": "Creative directive id."},
    ),
    _spec(
        "sync_creative_outputs",
        "Import completed image/video renderer outputs into the member's project and register them as editable Creative DNA elements.",
        {"project_name": "Project name/slug.", "directive_id": "Completed/queued creative directive id."},
        write=True,
    ),
    _spec(
        "list_voice_profiles",
        "List consent-controlled Voice Profiles for the pinned project without exposing raw reference paths.",
        {"project_name": "Project name/slug."},
    ),
    _spec(
        "list_production_jobs",
        "List the signed-in member's recent Aura production jobs and statuses.",
        {"limit": "Optional 1-50."},
    ),
    _spec(
        "production_job_status",
        "Read one production job status owned by the signed-in member.",
        {"job_id": "Production job id."},
    ),
]


def _clean_public_job(job: dict) -> dict:
    value = dict(job)
    value.pop("payload_json", None)
    value.pop("result_json", None)
    return value


def _creative_store(registry, args: dict) -> tuple[str, Path, CreativeProjectStore]:
    name = tools._project_name(args, registry.pinned_project)
    project = tools._safe_project(name)
    store = CreativeProjectStore(project)
    if not store.exists():
        try:
            manifest = ProjectWorkspace(project).load_manifest()
            title = manifest.title
        except Exception:
            title = name
        store.initialize(project_name=name, title=title, project_intent="Managed by Aura Core")
    return name, project, store


def _creative_explicit(tool_name: str, text: str) -> bool:
    lower = (text or "").lower()
    action = any(x in lower for x in ("create", "make", "generate", "render", "design", "edit", "change", "revise", "replace", "transform", "plan", "storyboard"))
    visual = any(x in lower for x in ("image", "picture", "poster", "cover", "art", "video", "clip", "visual", "scene"))
    if tool_name == "create_visual":
        return action and visual
    if tool_name == "plan_creative_directive":
        return action
    if tool_name == "sync_creative_outputs":
        return any(x in lower for x in ("sync", "import", "save the output", "import the output", "bring the output"))
    return True


def _find_directive(store: CreativeProjectStore, directive_id: str):
    manifest = store.load()
    directive = next((item for item in manifest.directives if item.id == directive_id), None)
    if directive is None:
        raise KeyError(directive_id)
    return manifest, directive


def _same_directive(manifest, *, kind: str | None, operation: str, instruction: str, targets: list[str], preserves: list[str], references: list[str]):
    normalized = " ".join(instruction.split()).lower()
    for item in reversed(manifest.directives[-30:]):
        if item.status in {"completed", "failed"}:
            continue
        if (
            item.target_kind == kind
            and item.operation == operation
            and " ".join(item.instruction.split()).lower() == normalized
            and item.target_element_ids == targets
            and item.preserve_element_ids == preserves
            and item.reference_ids == references
        ):
            return item
    return None


def _safe_output_name(filename: str, index: int) -> str:
    name = Path(filename).name
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:180]
    return f"{index:02d}_{clean or 'creative_output'}"


def _media_kind(filename: str, fallback: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}:
        return "image"
    if suffix in {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}:
        return "video"
    if suffix in {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}:
        return "audio"
    return fallback


def _music_idempotent(registry, call: tools.ToolCall, args: dict):
    """Prevent answer regeneration from duplicating already-planned Song DNA edits."""
    if call.name not in {"plan_lyric_change", "plan_instrument_replacement", "plan_section_regeneration"}:
        return None
    name = tools._project_name(args, registry.pinned_project)
    project = tools._safe_project(name)
    store = SongDNAStore(project)
    if not store.path.is_file():
        return None
    dna = store.load()
    if call.name == "plan_lyric_change":
        line_id = str(args.get("line_id") or "")
        new_text = str(args.get("new_text") or "").strip()
        line = next((x for x in dna.lyric_lines if x.id == line_id), None)
        if line and line.text.strip() == new_text:
            directive = next((x for x in reversed(dna.directives) if x.action == "replace_lyric_line" and line_id in x.target_ids and x.status not in {"complete", "cancelled"}), None)
            return {"idempotent": True, "already_applied_to_song_dna": True, "directive": directive.model_dump(mode="json") if directive else None, "song_dna_version": dna.version}
    if call.name == "plan_instrument_replacement":
        target = str(args.get("layer_id") or "")
        replacement = str(args.get("replacement") or "").strip().lower()
        for directive in reversed(dna.directives[-30:]):
            if directive.action == "replace_instrument" and target in directive.target_ids and str(directive.metadata.get("replacement") or "").strip().lower() == replacement and directive.status not in {"complete", "cancelled"}:
                return {"idempotent": True, "directive": directive.model_dump(mode="json"), "song_dna_version": dna.version}
    if call.name == "plan_section_regeneration":
        target = str(args.get("section_id") or "")
        instruction = " ".join(str(args.get("instruction") or "").split()).lower()
        for directive in reversed(dna.directives[-30:]):
            if directive.action == "regenerate_section" and target in directive.target_ids and " ".join(directive.instruction.split()).lower() == instruction and directive.status not in {"complete", "cancelled"}:
                return {"idempotent": True, "directive": directive.model_dump(mode="json"), "song_dna_version": dna.version}
    return None


def install_aura_tool_extensions() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    existing_names = {item.name for item in tools.TOOL_SPECS}
    for spec in EXTRA_SPECS:
        if spec.name not in existing_names:
            tools.TOOL_SPECS.append(spec)
            tools._SPEC_BY_NAME[spec.name] = spec
    original_execute = tools.AuraToolRegistry.execute

    def execute(self, call: tools.ToolCall, *, latest_user_message: str):
        args = dict(call.arguments or {})
        duplicate = _music_idempotent(self, call, args)
        if duplicate is not None:
            return duplicate

        extra = {spec.name for spec in EXTRA_SPECS}
        if call.name not in extra:
            return original_execute(self, call, latest_user_message=latest_user_message)
        if not self.tools_enabled:
            raise PermissionError("Aura tools are disabled for this conversation")
        spec = tools._SPEC_BY_NAME[call.name]
        if spec.write and not _creative_explicit(call.name, latest_user_message):
            raise PermissionError(f"Aura did not execute {call.name}: the member's latest message did not explicitly authorize this creative change")

        if call.name == "creative_renderer_status":
            states = renderer_states(probe=bool(args.get("probe", False)))
            for state in states.values():
                state.pop("base_url", None)
            return states

        if call.name == "list_production_jobs":
            limit = max(1, min(int(args.get("limit") or 20), 50))
            return [_clean_public_job(row) for row in studio_job_queue.list_for_user(self.member.user_id, limit=limit)]
        if call.name == "production_job_status":
            row = studio_job_queue.get(str(args.get("job_id") or ""), user_id=self.member.user_id)
            if not row:
                raise KeyError("Production job not found")
            return _clean_public_job(row)

        name, project, store = _creative_store(self, args)
        if call.name == "list_voice_profiles":
            ledger = RightsLedger(project / ".aura_rights")
            return [
                {
                    "id": p.id,
                    "name": p.name,
                    "owner_label": p.owner_label,
                    "verification_state": p.verification_state,
                    "allowed_uses": p.allowed_uses,
                    "similarity_limit": p.similarity_limit,
                    "active": p.active,
                    "revoked_at": p.revoked_at,
                }
                for p in ledger.list_voices()
            ]

        if call.name == "plan_creative_directive":
            manifest = store.load()
            kind = str(args.get("target_kind") or "").strip().lower() or None
            operation = str(args.get("operation") or "revise").strip().lower()
            instruction = str(args.get("instruction") or "").strip()
            targets = [str(x) for x in (args.get("target_element_ids") or [])]
            preserves = [str(x) for x in (args.get("preserve_element_ids") or [])]
            refs = [str(x) for x in (args.get("reference_ids") or [])]
            existing = _same_directive(manifest, kind=kind, operation=operation, instruction=instruction, targets=targets, preserves=preserves, references=refs)
            if existing:
                return {"project_name": name, "directive": existing.model_dump(mode="json"), "idempotent": True}
            directive = CreativeDirective(
                instruction=instruction,
                input_mode="text",
                operation=operation,
                target_kind=kind,
                target_element_ids=targets,
                preserve_element_ids=preserves,
                reference_ids=refs,
                metadata={"created_by": "Aura Core chat"},
            )
            manifest = store.add_directive(directive)
            return {
                "project_name": name,
                "directive": next(x for x in manifest.directives if x.id == directive.id).model_dump(mode="json"),
                "rendered": False,
            }

        if call.name == "create_visual":
            manifest = store.load()
            kind = str(args.get("kind") or "image").strip().lower()
            if kind not in {"image", "video"}:
                raise ValueError("create_visual kind must be image or video")
            prompt = str(args.get("prompt") or "").strip()
            if not prompt:
                raise ValueError("Visual creation prompt is required")
            targets = [str(x) for x in (args.get("target_element_ids") or [])]
            preserves = [str(x) for x in (args.get("preserve_element_ids") or [])]
            refs = [str(x) for x in (args.get("reference_ids") or [])]
            operation = "revise" if targets else "create"
            existing = _same_directive(manifest, kind=kind, operation=operation, instruction=prompt, targets=targets, preserves=preserves, references=refs)
            if existing and existing.status in {"queued", "running"}:
                return {"project_name": name, "directive": existing.model_dump(mode="json"), "idempotent": True, "queued": True}
            if existing is None:
                directive = CreativeDirective(
                    instruction=prompt,
                    input_mode="text",
                    operation=operation,
                    target_kind=kind,
                    target_element_ids=targets,
                    preserve_element_ids=preserves,
                    reference_ids=refs,
                    metadata={"created_by": "Aura Core chat"},
                )
                manifest = store.add_directive(directive)
            else:
                directive = existing
            renderer = renderer_for(kind)
            if not renderer.configured:
                current = next(x for x in store.load().directives if x.id == directive.id)
                return {
                    "project_name": name,
                    "directive": current.model_dump(mode="json"),
                    "queued": False,
                    "renderer_configured": False,
                    "detail": f"The {kind} directive is saved in Creative DNA, but the deployment has no configured {kind} renderer workflow yet.",
                }
            width = max(64, min(int(args.get("width") or (1024 if kind == "image" else 1080)), 4096))
            height = max(64, min(int(args.get("height") or (1024 if kind == "image" else 1920)), 4096))
            frames = max(1, min(int(args.get("frames") or 121), 10000))
            fps = max(1.0, min(float(args.get("fps") or 24.0), 120.0))
            seed = secrets.randbelow(2**31 - 1)
            variables = {
                "prompt": prompt,
                "negative_prompt": str(args.get("negative_prompt") or ""),
                "seed": seed,
                "width": width,
                "height": height,
                "frames": frames,
                "fps": fps,
                "project_name": manifest.project_name,
                "project_title": manifest.title,
                "directive_id": directive.id,
                "operation": directive.operation,
            }
            try:
                submission = renderer.submit(variables)
            except Exception as exc:
                store.update_directive(directive.id, status="failed", metadata={"last_renderer_error": f"{type(exc).__name__}: {exc}"})
                raise
            manifest = store.update_directive(
                directive.id,
                status="queued",
                capability_state="connected",
                renderer_route=f"comfyui:{submission.workflow_name}",
                metadata={
                    "creative_renderer": {
                        "provider": submission.provider,
                        "kind": submission.kind,
                        "prompt_id": submission.prompt_id,
                        "client_id": submission.client_id,
                        "workflow_name": submission.workflow_name,
                        "seed": seed,
                        "width": width,
                        "height": height,
                        "frames": frames,
                        "fps": fps,
                    }
                },
            )
            current = next(x for x in manifest.directives if x.id == directive.id)
            return {"project_name": name, "directive": current.model_dump(mode="json"), "queued": True, "renderer_configured": True}

        if call.name in {"creative_render_status", "sync_creative_outputs"}:
            directive_id = str(args.get("directive_id") or "").strip()
            manifest, directive = _find_directive(store, directive_id)
            if directive.target_kind not in {"image", "video"}:
                raise ValueError("Directive is not assigned to an image/video renderer")
            meta = directive.metadata.get("creative_renderer")
            if not isinstance(meta, dict) or not meta.get("prompt_id"):
                raise RuntimeError("Creative directive has not been submitted to a renderer")
            renderer = renderer_for(directive.target_kind)
            prompt_id = str(meta["prompt_id"])
            history = renderer.history(prompt_id)
            outputs = renderer.collect_outputs(history, prompt_id)
            if call.name == "creative_render_status":
                entry = history.get(prompt_id) if isinstance(history, dict) else None
                status = "completed" if outputs else ("running" if isinstance(entry, dict) else "queued")
                return {
                    "project_name": name,
                    "directive_id": directive.id,
                    "status": status,
                    "outputs": [x.model_dump(mode="json") for x in outputs],
                }
            if not outputs:
                raise RuntimeError("Creative render is not complete yet")
            output_dir = project / "output" / "creative" / str(directive.target_kind) / directive.id
            output_dir.mkdir(parents=True, exist_ok=True)
            manifest = store.load()
            existing_by_ref = {x.source_ref: x for x in manifest.elements if x.source_ref}
            imported = []
            for index, output in enumerate(outputs, 1):
                filename = _safe_output_name(output.filename, index)
                destination = output_dir / filename
                relative = destination.relative_to(project).as_posix()
                element = existing_by_ref.get(relative)
                if element is None:
                    if not destination.is_file():
                        renderer.download_output(output, destination)
                    kind = _media_kind(filename, str(directive.target_kind))
                    element = CreativeElement(
                        kind=kind,
                        label=f"{manifest.title} — {kind.title()} output {index}",
                        role=f"Generated from Aura directive {directive.id}",
                        status="ready",
                        source_type="generated",
                        source_ref=relative,
                        parent_ids=list(directive.target_element_ids),
                        prompt=directive.instruction,
                        metadata={"directive_id": directive.id, "renderer": "comfyui", "renderer_output": output.model_dump(mode="json")},
                    )
                    manifest = store.add_element(element)
                    existing_by_ref[relative] = element
                imported.append(element.model_dump(mode="json"))
            store.update_directive(
                directive.id,
                status="completed",
                capability_state="connected",
                metadata={"creative_renderer": {**meta, "local_outputs": [x.get("source_ref") for x in imported], "synced": True}},
            )
            return {"project_name": name, "directive_id": directive.id, "imported_elements": imported, "completed": True}

        raise ValueError(f"Unsupported extended Aura tool: {call.name}")

    tools.AuraToolRegistry.execute = execute
    _INSTALLED = True


__all__ = ["install_aura_tool_extensions"]
