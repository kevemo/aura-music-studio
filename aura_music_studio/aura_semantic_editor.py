from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .content_safety import enforce_creation_policy
from .creative_project import (
    CreativeDirective,
    CreativeKind,
    CreativeManifest,
    CreativeProjectStore,
    DirectiveOperation,
)
from .plans import DEEP_REVISION_HISTORY, REVISION_HISTORY
from .project import ProjectWorkspace
from .revisions import create_revision
from .song_dna import SongDNAStore, SongEditDirective, ensure_song_dna_from_manifest
from .tenant_storage import project_path

router = APIRouter(tags=["Aura Semantic Editing"])

SEMANTIC_LOG = "aura_semantic_edits.json"
_SUPPORTED_KINDS: tuple[CreativeKind, ...] = (
    "music",
    "audio",
    "voice",
    "video",
    "image",
    "text",
    "reference",
)
_SCOPE_KEYWORDS: dict[CreativeKind, tuple[str, ...]] = {
    "music": ("music", "song", "track", "arrangement", "instrument", "melody", "harmony"),
    "audio": ("audio", "sound", "mix", "master", "stem", "loudness", "eq", "compression"),
    "voice": ("voice", "vocal", "singer", "narration", "speech"),
    "video": ("video", "film", "clip", "scene", "footage", "camera", "cinematic"),
    "image": ("image", "poster", "art", "artwork", "photo", "picture", "thumbnail", "cover"),
    "text": ("text", "copy", "caption", "description", "script", "lyrics", "lyric", "title"),
    "reference": ("reference", "source material"),
}
_BROAD_TERMS = (
    "everything",
    "entire project",
    "whole project",
    "project-wide",
    "project wide",
    "all media",
    "across the project",
    "across everything",
)
_PRESERVE_PATTERN = re.compile(
    r"(?:do\s+not\s+change|don't\s+change|dont\s+change|keep|preserve|leave)\s+(?:the\s+)?([^,.;]+)",
    re.I,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _project(name: str) -> Path:
    try:
        return project_path(name, must_exist=True)
    except ValueError as exc:
        raise ValueError("Invalid project path") from exc
    except FileNotFoundError as exc:
        raise FileNotFoundError("Project not found") from exc


def _load_or_initialize_creative(project: Path, project_name: str) -> CreativeProjectStore:
    store = CreativeProjectStore(project)
    if store.exists():
        return store
    title = project_name
    intent = "Cross-media project controlled through Aura semantic editing"
    try:
        manifest = ProjectWorkspace(project).load_manifest()
        title = manifest.title
    except Exception:
        pass
    store.initialize(project_name=project_name, title=title, project_intent=intent)
    return store


def _load_song(project: Path):
    store = SongDNAStore(project)
    if store.path.is_file():
        return store, store.load()
    try:
        manifest = ProjectWorkspace(project).load_manifest().model_dump(mode="json")
    except Exception:
        return store, None
    try:
        return store, ensure_song_dna_from_manifest(project, manifest)
    except Exception:
        return store, None


def _operation(instruction: str) -> DirectiveOperation:
    text = instruction.lower()
    if any(word in text for word in ("replace", "swap")):
        return "replace"
    if any(word in text for word in ("extend", "longer", "continue")):
        return "extend"
    if any(word in text for word in ("storyboard", "shot list")):
        return "storyboard"
    if any(word in text for word in ("master", "loudness")):
        return "master"
    if any(word in text for word in ("mix", "eq", "compress", "reverb", "balance")):
        return "mix"
    if any(word in text for word in ("arrange", "arrangement", "reorder")):
        return "arrange"
    if any(word in text for word in ("sync", "align", "lip sync", "lip-sync")):
        return "sync"
    if any(word in text for word in ("style", "look", "cinematic", "colour", "color", "grade")):
        return "style"
    if any(word in text for word in ("transform", "convert", "turn into")):
        return "transform"
    if any(word in text for word in ("analyze", "analyse", "inspect", "review")):
        return "analyze"
    if any(word in text for word in ("create", "make", "add", "generate")):
        return "create"
    return "revise"


def _normalise_scope(values: Iterable[str] | None) -> list[CreativeKind]:
    out: list[CreativeKind] = []
    for raw in values or []:
        value = str(raw or "").strip().lower()
        if not value:
            continue
        aliases = {
            "song": "music",
            "track": "music",
            "sound": "audio",
            "vocal": "voice",
            "poster": "image",
            "art": "image",
            "copy": "text",
            "lyrics": "text",
        }
        value = aliases.get(value, value)
        if value not in _SUPPORTED_KINDS:
            raise ValueError(f"Unsupported semantic edit scope: {raw}")
        if value not in out:
            out.append(value)  # type: ignore[arg-type]
    return out


def _detect_scope(instruction: str, requested: Iterable[str] | None, manifest: CreativeManifest, has_song: bool) -> list[CreativeKind]:
    explicit = _normalise_scope(requested)
    if explicit:
        return explicit
    text = instruction.lower()
    active = {item.kind for item in manifest.elements if item.id in manifest.active_element_ids and item.status != "archived"}
    if any(term in text for term in _BROAD_TERMS):
        kinds = [kind for kind in _SUPPORTED_KINDS if kind in active]
        if has_song and "music" not in kinds:
            kinds.insert(0, "music")
        return kinds
    detected: list[CreativeKind] = []
    for kind, keywords in _SCOPE_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            detected.append(kind)
    if detected:
        return detected
    kinds = [kind for kind in _SUPPORTED_KINDS if kind in active]
    if has_song and "music" not in kinds:
        kinds.insert(0, "music")
    return kinds


def _preserve_terms(instruction: str, requested: Iterable[str] | None) -> list[str]:
    values = [str(value).strip() for value in requested or [] if str(value).strip()]
    for match in _PRESERVE_PATTERN.finditer(instruction):
        value = match.group(1).strip()
        if value and value not in values:
            values.append(value)
    return values[:80]


def _matches_term(value: str, term: str) -> bool:
    value_l = value.lower().strip()
    term_l = term.lower().strip()
    return bool(term_l and (term_l == value_l or term_l in value_l or value_l in term_l))


def _preserved_creative_ids(manifest: CreativeManifest, terms: list[str], explicit_ids: Iterable[str] | None) -> list[str]:
    out = {str(value).strip() for value in explicit_ids or [] if str(value).strip()}
    for element in manifest.elements:
        hay = " ".join((element.id, element.label, element.role, element.kind)).strip()
        if any(_matches_term(hay, term) for term in terms):
            out.add(element.id)
    known = {item.id for item in manifest.elements}
    return [value for value in out if value in known][:100]


def _song_action(instruction: str) -> str:
    text = instruction.lower()
    if "tempo" in text or "bpm" in text or "faster" in text or "slower" in text:
        return "change_tempo"
    if "key" in text or "transpose" in text:
        return "change_key"
    if any(term in text for term in ("voice", "vocal", "singer")):
        return "change_voice"
    if any(term in text for term in ("master", "loudness")):
        return "remaster"
    return "remix"


def _song_preserve_ids(dna, terms: list[str]) -> list[str]:
    out: set[str] = set()
    rows = [*dna.sections, *dna.lyric_lines, *dna.instruments]
    for item in rows:
        if getattr(item, "locked", False):
            out.add(item.id)
            continue
        hay = " ".join(
            str(value or "")
            for value in (
                item.id,
                getattr(item, "name", ""),
                getattr(item, "text", ""),
                getattr(item, "label", ""),
                getattr(item, "role", ""),
            )
        )
        if any(_matches_term(hay, term) for term in terms):
            out.add(item.id)
    return sorted(out)[:300]


def _snapshot(member, project: Path, transaction_id: str) -> dict | None:
    plan = getattr(member, "plan", None)
    if plan is None or not hasattr(plan, "has") or not plan.has(REVISION_HISTORY):
        return None
    keep = 200 if plan.has(DEEP_REVISION_HISTORY) else 20
    try:
        return create_revision(
            project,
            label="Before Aura project-wide semantic edit",
            reason="aura_semantic_edit",
            actor="Aura Embodied Companion",
            keep=keep,
            metadata={"semantic_transaction_id": transaction_id},
        )
    except TypeError:
        # Compatibility with older revision helpers that do not yet accept metadata.
        try:
            return create_revision(
                project,
                label="Before Aura project-wide semantic edit",
                reason="aura_semantic_edit",
                actor="Aura Embodied Companion",
                keep=keep,
            )
        except Exception:
            return None
    except Exception:
        return None


def _log_path(project: Path) -> Path:
    return project / SEMANTIC_LOG


def _load_log(project: Path) -> list[dict]:
    path = _log_path(project)
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return list(value) if isinstance(value, list) else []


def _append_log(project: Path, row: dict) -> None:
    rows = _load_log(project)
    rows.append(row)
    rows = rows[-250:]
    path = _log_path(project)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


class SemanticEditRequest(BaseModel):
    instruction: str = Field(min_length=2, max_length=6000)
    scope: list[str] = Field(default_factory=list, max_length=20)
    preserve: list[str] = Field(default_factory=list, max_length=80)
    preserve_element_ids: list[str] = Field(default_factory=list, max_length=100)
    reference_ids: list[str] = Field(default_factory=list, max_length=100)
    input_mode: Literal["text", "voice", "upload", "mixed"] = "text"


def semantic_edit_plan(
    project_name: str,
    instruction: str,
    *,
    scope: Iterable[str] | None = None,
    preserve: Iterable[str] | None = None,
    preserve_element_ids: Iterable[str] | None = None,
    reference_ids: Iterable[str] | None = None,
) -> dict:
    enforce_creation_policy(instruction, context="Aura semantic project edit")
    project = _project(project_name)
    creative = _load_or_initialize_creative(project, project_name)
    manifest = creative.load()
    song_store, dna = _load_song(project)
    del song_store
    terms = _preserve_terms(instruction, preserve)
    kinds = _detect_scope(instruction, scope, manifest, dna is not None)
    protected = _preserved_creative_ids(manifest, terms, preserve_element_ids)
    active = [item for item in manifest.elements if item.id in manifest.active_element_ids and item.status != "archived"]
    operation = _operation(instruction)
    groups: list[dict] = []
    missing: list[str] = []
    for kind in kinds:
        if kind == "music" and dna is not None:
            continue
        targets = [item.id for item in active if item.kind == kind and item.id not in protected]
        if not targets:
            missing.append(kind)
            continue
        groups.append(
            {
                "kind": kind,
                "operation": operation,
                "target_element_ids": targets[:100],
                "preserve_element_ids": protected[:100],
            }
        )
    song = None
    if dna is not None and any(kind in {"music", "audio", "voice", "text"} for kind in kinds):
        song = {
            "action": _song_action(instruction),
            "preserve_ids": _song_preserve_ids(dna, terms),
            "song_dna_version": dna.version,
        }
    return {
        "project_name": project_name,
        "instruction": instruction.strip(),
        "scope": kinds,
        "operation": operation,
        "preserve_terms": terms,
        "preserve_element_ids": protected,
        "creative_groups": groups,
        "song_edit": song,
        "reference_ids": [str(value) for value in reference_ids or []][:100],
        "missing_or_preserved_kinds": missing,
        "writes_planned": len(groups) + (1 if song else 0),
        "non_destructive": True,
    }


def apply_semantic_edit(
    member,
    project_name: str,
    instruction: str,
    *,
    scope: Iterable[str] | None = None,
    preserve: Iterable[str] | None = None,
    preserve_element_ids: Iterable[str] | None = None,
    reference_ids: Iterable[str] | None = None,
    input_mode: str = "text",
) -> dict:
    plan = semantic_edit_plan(
        project_name,
        instruction,
        scope=scope,
        preserve=preserve,
        preserve_element_ids=preserve_element_ids,
        reference_ids=reference_ids,
    )
    if not plan["writes_planned"]:
        return {
            "transaction_id": None,
            "applied": False,
            "plan": plan,
            "detail": "Aura found no editable project elements in the requested semantic scope.",
        }

    project = _project(project_name)
    creative = _load_or_initialize_creative(project, project_name)
    manifest = creative.load()
    known_references = {item.id for item in manifest.references}
    selected_references = [value for value in plan["reference_ids"] if value in known_references]
    transaction_id = f"semantic_{uuid4().hex}"
    revision = _snapshot(member, project, transaction_id)
    creative_directives: list[dict] = []

    for group in plan["creative_groups"]:
        directive = CreativeDirective(
            instruction=instruction.strip(),
            input_mode=input_mode if input_mode in {"text", "voice", "upload", "mixed"} else "text",  # type: ignore[arg-type]
            operation=group["operation"],
            target_kind=group["kind"],
            target_element_ids=group["target_element_ids"],
            preserve_element_ids=group["preserve_element_ids"],
            reference_ids=selected_references,
            metadata={
                "semantic_transaction_id": transaction_id,
                "semantic_scope": plan["scope"],
                "preserve_terms": plan["preserve_terms"],
                "project_wide": len(plan["scope"]) > 1,
                "source": "aura_embodied_companion",
            },
        )
        manifest = creative.add_directive(directive)
        saved = next(item for item in manifest.directives if item.id == directive.id)
        creative_directives.append(saved.model_dump(mode="json"))

    song_directive = None
    if plan["song_edit"] is not None:
        song_store, dna = _load_song(project)
        if dna is not None:
            directive = SongEditDirective(
                action=plan["song_edit"]["action"],
                instruction=instruction.strip(),
                target_ids=[],
                preserve_ids=plan["song_edit"]["preserve_ids"],
                status="planned",
                metadata={
                    "semantic_transaction_id": transaction_id,
                    "semantic_scope": plan["scope"],
                    "preserve_terms": plan["preserve_terms"],
                    "project_wide": len(plan["scope"]) > 1,
                    "source": "aura_embodied_companion",
                },
            )
            dna.directives.append(directive)
            dna.version += 1
            song_store.save(dna)
            song_directive = directive.model_dump(mode="json")

    states = [row.get("status") for row in creative_directives]
    ready = sum(1 for value in states if value == "ready_for_renderer")
    planned = sum(1 for value in states if value == "planned") + (1 if song_directive else 0)
    log_row = {
        "id": transaction_id,
        "created_at": _now(),
        "member_user_id": str(getattr(member, "user_id", ""))[:120],
        "instruction": instruction.strip(),
        "scope": plan["scope"],
        "operation": plan["operation"],
        "creative_directive_ids": [row["id"] for row in creative_directives],
        "song_directive_id": song_directive["id"] if song_directive else None,
        "preserve_element_ids": plan["preserve_element_ids"],
        "revision_id": revision.get("id") if isinstance(revision, dict) else None,
    }
    _append_log(project, log_row)

    return {
        "transaction_id": transaction_id,
        "applied": True,
        "plan": plan,
        "creative_directives": creative_directives,
        "song_directive": song_directive,
        "revision_snapshot": revision,
        "execution_summary": {
            "directives_ready_for_connected_renderer": ready,
            "directives_planned_or_waiting": planned,
            "media_output_generated": False,
            "note": "Aura recorded one non-destructive semantic transaction. Connected renderers can execute eligible directives; unavailable renderers remain truthfully planned.",
        },
    }


@router.post("/aura-intelligence/api/projects/{project_name}/semantic-edit/preview")
def preview_semantic_edit(project_name: str, body: SemanticEditRequest, request: Request):
    _member(request)
    try:
        return semantic_edit_plan(
            project_name,
            body.instruction,
            scope=body.scope,
            preserve=body.preserve,
            preserve_element_ids=body.preserve_element_ids,
            reference_ids=body.reference_ids,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/aura-intelligence/api/projects/{project_name}/semantic-edit/apply")
def apply_semantic_edit_route(project_name: str, body: SemanticEditRequest, request: Request):
    member = _member(request)
    try:
        return apply_semantic_edit(
            member,
            project_name,
            body.instruction,
            scope=body.scope,
            preserve=body.preserve,
            preserve_element_ids=body.preserve_element_ids,
            reference_ids=body.reference_ids,
            input_mode=body.input_mode,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/aura-intelligence/api/projects/{project_name}/semantic-edits")
def semantic_edit_history(project_name: str, request: Request, limit: int = 50):
    _member(request)
    try:
        project = _project(project_name)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    limit = max(1, min(int(limit), 200))
    return {"project_name": project_name, "transactions": _load_log(project)[-limit:]}


__all__ = [
    "SemanticEditRequest",
    "apply_semantic_edit",
    "router",
    "semantic_edit_plan",
]
