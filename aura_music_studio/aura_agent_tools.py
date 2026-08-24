from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from .assets import AssetLibrary
from .job_api import queue as studio_job_queue, start_full_song_slot
from .project import ProjectWorkspace
from .song_dna import SongDNAStore, ensure_song_dna_from_manifest
from .tenant_storage import list_project_dirs, project_path
from .web_access import AuraWebGateway


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolPlan(BaseModel):
    calls: list[ToolCall] = Field(default_factory=list, max_length=6)
    answer_without_tools: bool = False


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    arguments: dict[str, str]
    write: bool = False
    web: bool = False

    def public(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": self.arguments,
            "write": self.write,
            "web": self.web,
        }


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.skip:
            self.skip -= 1
        if not self.skip and tag.lower() in {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            value = re.sub(r"\s+", " ", data or "").strip()
            if value:
                self.parts.append(value)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", " ".join(self.parts).replace(" \n ", "\n")).strip()


TOOL_SPECS = [
    ToolSpec("list_projects", "List the signed-in member's Pulsar-Frequency House projects.", {}),
    ToolSpec(
        "inspect_project",
        "Read a compact current snapshot of a project: manifest, Song DNA, DAW tracks, creative elements, assets and outputs.",
        {"project_name": "Project name/slug. Omit only when a thread already has a pinned project."},
    ),
    ToolSpec(
        "inspect_song_dna",
        "Read editable Song DNA including sections, lyric lines, instrument layers and pending Aura edit directives.",
        {"project_name": "Project name/slug."},
    ),
    ToolSpec(
        "list_project_assets",
        "List private assets already uploaded to a project, with type and analysis metadata but not raw file bytes.",
        {"project_name": "Project name/slug."},
    ),
    ToolSpec(
        "list_project_outputs",
        "List generated project outputs and file sizes. Download permissions remain enforced by the normal output API.",
        {"project_name": "Project name/slug."},
    ),
    ToolSpec(
        "web_search",
        "Search the public web through Aura's protected SearXNG gateway when current information is needed.",
        {"query": "Search query", "limit": "Optional result count 1-10."},
        web=True,
    ),
    ToolSpec(
        "web_fetch",
        "Fetch and extract readable text from a known public HTTPS URL through Aura's SSRF-protected gateway.",
        {"url": "Public HTTPS URL."},
        web=True,
    ),
    ToolSpec(
        "sync_song_dna",
        "Synchronize an existing Aura DAW session into Song DNA. This is non-destructive project metadata synchronization.",
        {"project_name": "Project name/slug."},
        write=True,
    ),
    ToolSpec(
        "plan_lyric_change",
        "Change one identified lyric line in Song DNA and create a non-destructive local vocal-regeneration directive. Does not generate/commit audio.",
        {"project_name": "Project name/slug.", "line_id": "Stable lyric line id.", "new_text": "Replacement lyric text."},
        write=True,
    ),
    ToolSpec(
        "plan_instrument_replacement",
        "Create a non-destructive Song DNA plan to replace one instrument layer while preserving the rest of the arrangement.",
        {"project_name": "Project name/slug.", "layer_id": "Stable instrument layer id.", "replacement": "New instrument description.", "instruction": "Optional production direction."},
        write=True,
    ),
    ToolSpec(
        "plan_section_regeneration",
        "Create a non-destructive plan to regenerate only one song section.",
        {"project_name": "Project name/slug.", "section_id": "Stable section id.", "instruction": "Requested local change."},
        write=True,
    ),
    ToolSpec(
        "queue_full_production",
        "Queue the pinned/existing project for the normal full-song production pipeline after membership slot checks. Use only when the member explicitly asks Aura to render/produce the project.",
        {"project_name": "Project name/slug."},
        write=True,
    ),
]


_SPEC_BY_NAME = {item.name: item for item in TOOL_SPECS}


def public_tool_specs(*, web_enabled: bool = True, tools_enabled: bool = True) -> list[dict]:
    if not tools_enabled:
        return []
    return [item.public() for item in TOOL_SPECS if web_enabled or not item.web]


def _project_name(arguments: dict, pinned_project: str | None) -> str:
    value = str(arguments.get("project_name") or pinned_project or "").strip()
    if not value:
        raise ValueError("This tool needs a project. Pin a project to the conversation or provide project_name.")
    return value


def _safe_project(name: str) -> Path:
    return project_path(name, must_exist=True)


def _read_json(path: Path) -> dict | list | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _project_outputs(project: Path) -> list[dict]:
    root = project / "output"
    if not root.is_dir():
        return []
    rows = []
    for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.as_posix().lower()):
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "suffix": path.suffix.lower(),
            }
        )
    return rows[-120:]


def project_snapshot(name: str) -> dict:
    project = _safe_project(name)
    workspace = ProjectWorkspace(project)
    result: dict[str, Any] = {"project_name": name, "path_private": True}
    try:
        manifest = workspace.load_manifest()
        result["manifest"] = {
            "title": manifest.title,
            "mode": manifest.mode,
            "tempo_bpm": manifest.tempo_bpm,
            "key": manifest.key,
            "meter": manifest.meter,
            "target_duration_seconds": manifest.target_duration_seconds,
            "reference_audio": bool(manifest.reference_audio),
            "lyrics_file": bool(manifest.lyrics_file),
            "renderer_preferred": list(manifest.renderer.preferred),
        }
    except Exception as exc:
        result["manifest_error"] = f"{type(exc).__name__}: {exc}"

    song_path = project / "song_dna.json"
    if song_path.is_file():
        try:
            dna = SongDNAStore(project).load()
            result["song_dna"] = {
                "title": dna.title,
                "version": dna.version,
                "genre": dna.genre,
                "mood": dna.mood,
                "bpm": dna.bpm,
                "key": dna.key,
                "meter": dna.meter,
                "sections": [{"id": x.id, "name": x.name, "locked": x.locked} for x in dna.sections],
                "lyric_lines": len(dna.lyric_lines),
                "instrument_layers": [
                    {"id": x.id, "role": x.role, "label": x.label, "locked": x.locked, "track_linked": bool(x.track_id)}
                    for x in dna.instruments
                ],
                "pending_directives": [
                    {"id": x.id, "action": x.action, "status": x.status, "target_ids": x.target_ids}
                    for x in dna.directives[-20:]
                    if x.status not in {"complete", "cancelled"}
                ],
            }
        except Exception as exc:
            result["song_dna_error"] = f"{type(exc).__name__}: {exc}"

    session = _read_json(project / "aura_session.json")
    if isinstance(session, dict):
        result["daw"] = {
            "name": session.get("name"),
            "bpm": session.get("bpm"),
            "key": session.get("key"),
            "meter": session.get("meter"),
            "tracks": [
                {
                    "id": row.get("id"),
                    "name": row.get("name"),
                    "role": row.get("role"),
                    "clips": len(row.get("clips") or []),
                    "effects": len(row.get("effects") or []),
                    "mute": bool(row.get("mute")),
                    "solo": bool(row.get("solo")),
                }
                for row in (session.get("tracks") or [])[:80]
            ],
        }

    creative = _read_json(project / "creative_manifest.json")
    if isinstance(creative, dict):
        result["creative_project"] = {
            "title": creative.get("title"),
            "intent": creative.get("intent"),
            "elements": [
                {"id": x.get("id"), "kind": x.get("kind"), "label": x.get("label"), "status": x.get("status")}
                for x in (creative.get("elements") or [])[-60:]
            ],
            "directives": [
                {"id": x.get("id"), "operation": x.get("operation"), "status": x.get("status"), "target_kind": x.get("target_kind")}
                for x in (creative.get("directives") or [])[-30:]
            ],
        }

    try:
        assets = AssetLibrary(project).list()
        result["assets"] = [
            {"id": x.id, "name": x.name, "kind": x.kind, "analysis": x.analysis, "tags": x.tags}
            for x in assets[-80:]
        ]
    except Exception:
        result["assets"] = []
    result["outputs"] = _project_outputs(project)[-50:]
    return result


def _inspect_song(name: str) -> dict:
    project = _safe_project(name)
    store = SongDNAStore(project)
    if not store.path.is_file():
        workspace = ProjectWorkspace(project)
        dna = ensure_song_dna_from_manifest(project, workspace.load_manifest().model_dump(mode="json"))
    else:
        dna = store.load()
    return {
        "title": dna.title,
        "version": dna.version,
        "genre": dna.genre,
        "mood": dna.mood,
        "bpm": dna.bpm,
        "key": dna.key,
        "meter": dna.meter,
        "sections": [x.model_dump(mode="json") for x in dna.sections],
        "lyric_lines": [x.model_dump(mode="json") for x in dna.lyric_lines],
        "instruments": [x.model_dump(mode="json") for x in dna.instruments],
        "directives": [x.model_dump(mode="json") for x in dna.directives[-40:]],
        "quality_contract": dna.quality_contract,
    }


def _web_text(url: str) -> dict:
    result = AuraWebGateway().fetch_text(url)
    text = result.text
    if "html" in (result.content_type or "").lower() or "<html" in text[:1000].lower():
        parser = _TextExtractor()
        parser.feed(text)
        text = parser.text()
    return {
        "url": result.url,
        "status_code": result.status_code,
        "content_type": result.content_type,
        "cached": result.cached,
        "text": text[:40000],
        "truncated": len(text) > 40000,
    }


def _explicit_write_allowed(tool_name: str, latest_user_message: str) -> bool:
    text = (latest_user_message or "").lower()
    if tool_name == "sync_song_dna":
        return any(x in text for x in ("sync", "refresh", "update song dna", "update the song dna"))
    if tool_name == "plan_lyric_change":
        return any(x in text for x in ("change the lyric", "change lyric", "replace the lyric", "replace lyric", "edit the lyric", "edit lyric", "change this line", "replace this line"))
    if tool_name == "plan_instrument_replacement":
        return any(x in text for x in ("replace the", "replace instrument", "change the instrument", "swap the", "swap instrument"))
    if tool_name == "plan_section_regeneration":
        return any(x in text for x in ("regenerate", "redo the", "change the chorus", "change the verse", "change the bridge", "change this section"))
    if tool_name == "queue_full_production":
        action = any(x in text for x in ("produce", "render", "generate", "create", "make", "build"))
        target = any(x in text for x in ("song", "track", "music", "project", "production", "master"))
        return action and target
    return True


class AuraToolRegistry:
    def __init__(self, *, member, pinned_project: str | None, web_enabled: bool, tools_enabled: bool):
        self.member = member
        self.pinned_project = pinned_project
        self.web_enabled = bool(web_enabled)
        self.tools_enabled = bool(tools_enabled)

    def specs(self) -> list[dict]:
        return public_tool_specs(web_enabled=self.web_enabled, tools_enabled=self.tools_enabled)

    def execute(self, call: ToolCall, *, latest_user_message: str) -> Any:
        if not self.tools_enabled:
            raise PermissionError("Aura tools are disabled for this conversation")
        spec = _SPEC_BY_NAME.get(call.name)
        if spec is None:
            raise ValueError(f"Unknown Aura tool: {call.name}")
        if spec.web and not self.web_enabled:
            raise PermissionError("Web research is disabled for this conversation")
        if spec.write and not _explicit_write_allowed(call.name, latest_user_message):
            raise PermissionError(
                f"Aura did not execute {call.name}: project-changing tools require explicit wording in the member's latest message."
            )

        args = dict(call.arguments or {})
        if call.name == "list_projects":
            rows = []
            for project in list_project_dirs():
                row = {"project_name": project.name}
                try:
                    manifest = ProjectWorkspace(project).load_manifest()
                    row.update({"title": manifest.title, "mode": manifest.mode})
                except Exception:
                    pass
                row["has_song_dna"] = (project / "song_dna.json").is_file()
                row["has_daw_session"] = (project / "aura_session.json").is_file()
                row["has_creative_manifest"] = (project / "creative_manifest.json").is_file()
                rows.append(row)
            return rows

        if call.name == "inspect_project":
            return project_snapshot(_project_name(args, self.pinned_project))
        if call.name == "inspect_song_dna":
            return _inspect_song(_project_name(args, self.pinned_project))
        if call.name == "list_project_assets":
            name = _project_name(args, self.pinned_project)
            project = _safe_project(name)
            return [
                {"id": x.id, "name": x.name, "kind": x.kind, "analysis": x.analysis, "tags": x.tags, "notes": x.notes}
                for x in AssetLibrary(project).list()
            ]
        if call.name == "list_project_outputs":
            name = _project_name(args, self.pinned_project)
            return _project_outputs(_safe_project(name))
        if call.name == "web_search":
            query = str(args.get("query") or "").strip()
            limit = max(1, min(int(args.get("limit") or 8), 10))
            return AuraWebGateway().search(query, limit=limit)
        if call.name == "web_fetch":
            return _web_text(str(args.get("url") or "").strip())

        name = _project_name(args, self.pinned_project)
        project = _safe_project(name)
        if call.name == "sync_song_dna":
            session_path = project / "aura_session.json"
            if not session_path.is_file():
                raise FileNotFoundError("This project does not have an Aura DAW session yet")
            store = SongDNAStore(project)
            if not store.path.is_file():
                ensure_song_dna_from_manifest(project, ProjectWorkspace(project).load_manifest().model_dump(mode="json"))
            dna = store.sync_session(session_path)
            return {"project_name": name, "song_dna_version": dna.version, "layers": len(dna.instruments), "synced": True}
        if call.name == "plan_lyric_change":
            dna = SongDNAStore(project).update_lyric_line(str(args.get("line_id") or ""), str(args.get("new_text") or ""))
            directive = dna.directives[-1]
            return {"directive": directive.model_dump(mode="json"), "song_dna_version": dna.version, "audio_generated": False}
        if call.name == "plan_instrument_replacement":
            dna = SongDNAStore(project).plan_instrument_replacement(
                str(args.get("layer_id") or ""),
                str(args.get("replacement") or ""),
                str(args.get("instruction") or ""),
            )
            return {"directive": dna.directives[-1].model_dump(mode="json"), "song_dna_version": dna.version, "audio_generated": False}
        if call.name == "plan_section_regeneration":
            dna = SongDNAStore(project).plan_section_regeneration(
                str(args.get("section_id") or ""), str(args.get("instruction") or ""), preserve_instruments=True
            )
            return {"directive": dna.directives[-1].model_dump(mode="json"), "song_dna_version": dna.version, "audio_generated": False}
        if call.name == "queue_full_production":
            start_full_song_slot(self.member, name)
            priority = 100 if self.member.plan.has("priority_queue") else 20
            job = studio_job_queue.submit(self.member.user_id, name, job_type="produce", priority=priority)
            return {
                "job_id": job.get("id"),
                "status": job.get("status"),
                "project_name": name,
                "queued": True,
            }
        raise ValueError(f"Aura tool is not implemented: {call.name}")
