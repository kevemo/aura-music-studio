from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .accounts import AccountStore
from .aura_companion import AuraCompanionError, AuraCompanionService
from .creation import CreateSongRequest, build_song_project
from .jobs import StudioJobQueue
from .live_translation import AuraLiveTranslator
from .music_video_orchestrator import AuraMusicVideoDirector
from .pipeline import AuraPipeline
from .plans import (
    APPROVED_VOICE_DUPLICATION,
    AURA_SPEECH,
    BASIC_CREATE,
    FULL_TRACK,
    PRIORITY_QUEUE,
    VIDEO_DIRECTOR,
)
from .tenant_storage import list_project_dirs, project_path, projects_root


class AuraSystemCompanionService(AuraCompanionService):
    """Aura Companion plus higher-level workflows available across the site.

    The base companion owns general chat/web/image/video/FX tools. This layer adds workflows
    that connect those primitives into the actual Live Sound Studio product while preserving
    the same membership and tenant boundaries.
    """

    def __init__(self, db_path: str | Path | None = None):
        super().__init__(db_path)
        self.accounts = AccountStore(self.store.db_path)
        self.jobs = StudioJobQueue(self.accounts)
        self.translator = AuraLiveTranslator()
        self.music_video = AuraMusicVideoDirector(self.store.db_path)

    def _tool_definitions(self, member) -> list[dict[str, Any]]:
        tools = list(super()._tool_definitions(member))
        if not tools:
            return tools

        tools.append(
            {
                "type": "function",
                "name": "list_my_projects",
                "description": "List the signed-in user's own Live Sound Studio projects.",
                "strict": True,
                "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            }
        )

        if member.plan.has(BASIC_CREATE):
            vocal_modes = ["instrumental", "ai_vocal"]
            if member.plan.has(APPROVED_VOICE_DUPLICATION):
                vocal_modes.append("approved_voice")
            tools.extend(
                [
                    {
                        "type": "function",
                        "name": "create_song_project",
                        "description": (
                            "Create a new multilingual Live Sound Studio song project. This creates the project/lyrics/manifest; "
                            "use start_song_render separately to queue the real finished audio."
                        ),
                        "strict": True,
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "concept": {"type": "string"},
                                "lyrics": {"type": "string"},
                                "generate_lyrics": {"type": "boolean"},
                                "genre": {"type": "string"},
                                "subgenre": {"type": "string"},
                                "mood": {"type": "string"},
                                "language": {"type": "string"},
                                "lyrics_source_language": {"type": ["string", "null"]},
                                "adapt_supplied_lyrics_to_song_language": {"type": "boolean"},
                                "duration_seconds": {"type": "integer", "minimum": 10, "maximum": 600},
                                "vocal_mode": {"type": "string", "enum": vocal_modes},
                                "voice_profile_id": {"type": ["string", "null"]},
                                "voice_similarity": {"type": "number", "minimum": 0, "maximum": 1},
                                "voice_pitch_shift": {"type": "integer", "minimum": -24, "maximum": 24},
                                "instruments": {"type": "array", "items": {"type": "string"}},
                                "bpm": {"type": ["number", "null"]},
                                "key": {"type": ["string", "null"]},
                                "meter": {"type": "string"},
                                "structure": {"type": "string"},
                                "extra_prompt": {"type": "string"},
                            },
                            "required": [
                                "title", "concept", "lyrics", "generate_lyrics", "genre", "subgenre", "mood", "language",
                                "lyrics_source_language", "adapt_supplied_lyrics_to_song_language", "duration_seconds", "vocal_mode",
                                "voice_profile_id", "voice_similarity", "voice_pitch_shift", "instruments", "bpm", "key", "meter",
                                "structure", "extra_prompt"
                            ],
                            "additionalProperties": False,
                        },
                    },
                    {
                        "type": "function",
                        "name": "analyze_song_project",
                        "description": "Analyze one of the signed-in user's song projects and return Aura's arrangement/technical analysis.",
                        "strict": True,
                        "parameters": {
                            "type": "object",
                            "properties": {"project_name": {"type": "string"}},
                            "required": ["project_name"],
                            "additionalProperties": False,
                        },
                    },
                ]
            )

        if member.plan.has(FULL_TRACK):
            tools.extend(
                [
                    {
                        "type": "function",
                        "name": "start_song_render",
                        "description": "Queue a real full-song render for one of the signed-in user's existing projects.",
                        "strict": True,
                        "parameters": {
                            "type": "object",
                            "properties": {"project_name": {"type": "string"}},
                            "required": ["project_name"],
                            "additionalProperties": False,
                        },
                    },
                    {
                        "type": "function",
                        "name": "get_song_render_status",
                        "description": "Read the signed-in user's production render-job status.",
                        "strict": True,
                        "parameters": {
                            "type": "object",
                            "properties": {"job_id": {"type": "string"}},
                            "required": ["job_id"],
                            "additionalProperties": False,
                        },
                    },
                ]
            )

        if member.plan.has(AURA_SPEECH):
            tools.append(
                {
                    "type": "function",
                    "name": "live_translate_text",
                    "description": "Translate text between languages for Aura's interpreter/live-translation workflow.",
                    "strict": True,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "source_locale": {"type": "string"},
                            "target_locale": {"type": "string"},
                        },
                        "required": ["text", "source_locale", "target_locale"],
                        "additionalProperties": False,
                    },
                }
            )

        if member.plan.has(VIDEO_DIRECTOR):
            tools.append(
                {
                    "type": "function",
                    "name": "create_music_video",
                    "description": (
                        "Start Aura Music Video Director for a completed song project. It storyboards the song, generates tracked shots, "
                        "then assembles them against the original mastered audio when all shots complete."
                    ),
                    "strict": True,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "project_name": {"type": "string"},
                            "title": {"type": "string"},
                            "concept": {"type": "string"},
                            "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16", "1:1"]},
                            "provider": {"type": "string", "enum": ["auto", "local", "openai", "runway"]},
                            "quality": {"type": "string", "enum": ["standard", "high", "professional"]},
                            "continuity": {"type": "string"},
                        },
                        "required": ["project_name", "title", "concept", "aspect_ratio", "provider", "quality", "continuity"],
                        "additionalProperties": False,
                    },
                }
            )

        return tools

    def _execute_tool(self, member, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        allowed = {item["name"] for item in self._tool_definitions(member)}
        if name not in allowed:
            raise AuraCompanionError(f"Aura tool is not permitted for this account: {name}")

        if name == "list_my_projects":
            return {
                "projects": [
                    {
                        "name": project.name,
                        "has_manifest": (project / "project.yaml").is_file(),
                        "has_final_master": (project / "output" / "Aura_Final_Master.wav").is_file(),
                    }
                    for project in list_project_dirs()
                ]
            }

        if name == "create_song_project":
            if not member.plan.has(BASIC_CREATE):
                raise AuraCompanionError("Song creation is not available on this membership")
            if arguments.get("vocal_mode") == "approved_voice" and not member.plan.has(APPROVED_VOICE_DUPLICATION):
                raise AuraCompanionError("Consent-approved voice duplication requires Pro")
            request = CreateSongRequest(**arguments)
            project = build_song_project(request, projects_root())
            return {
                "project": project.name,
                "created": True,
                "song_locale": request.language,
                "vocal_mode": request.vocal_mode,
                "next_action": "start_song_render" if member.plan.has(FULL_TRACK) else "upgrade_for_full_track",
            }

        if name == "analyze_song_project":
            if not member.plan.has(BASIC_CREATE):
                raise AuraCompanionError("Project analysis is not available on this membership")
            project = project_path(str(arguments.get("project_name") or ""), must_exist=True)
            return AuraPipeline(project).analyze_only()

        if name == "start_song_render":
            if not member.plan.has(FULL_TRACK):
                raise AuraCompanionError("Full-song rendering requires Base or Pro")
            project_name = str(arguments.get("project_name") or "").strip()
            project_path(project_name, must_exist=True)
            try:
                slot = self.accounts.start_song_slot(
                    member.user_id,
                    project_name,
                    datetime.now(timezone.utc).date().isoformat(),
                )
            except (PermissionError, ValueError) as exc:
                raise AuraCompanionError(str(exc)) from exc
            if slot.get("state") == "confirmed":
                raise AuraCompanionError("This track is already confirmed. Start a new project for another finished song.")
            priority = 100 if member.plan.has(PRIORITY_QUEUE) else 20
            job = self.jobs.submit(member.user_id, project_name, job_type="produce", priority=priority)
            return {
                "job_id": job["id"],
                "project_name": project_name,
                "status": job["status"],
                "priority": job["priority"],
            }

        if name == "get_song_render_status":
            if not member.plan.has(FULL_TRACK):
                raise AuraCompanionError("Full-song rendering is not available on this membership")
            job = self.jobs.get(str(arguments.get("job_id") or ""), user_id=member.user_id)
            if not job:
                raise AuraCompanionError("Render job not found")
            result = {k: v for k, v in job.items() if k not in {"payload_json", "result_json"}}
            if job.get("result_json"):
                import json
                try:
                    result["result"] = json.loads(job["result_json"])
                except Exception:
                    result["result"] = None
            return result

        if name == "live_translate_text":
            if not member.plan.has(AURA_SPEECH):
                raise AuraCompanionError("Aura live translation is not available on this membership")
            return self.translator.translate_text(
                str(arguments.get("text") or ""),
                source_locale=str(arguments.get("source_locale") or "auto"),
                target_locale=str(arguments.get("target_locale") or "en"),
            )

        if name == "create_music_video":
            if not member.plan.has(VIDEO_DIRECTOR):
                raise AuraCompanionError("Aura Music Video Director requires Pro")
            project = project_path(str(arguments.get("project_name") or ""), must_exist=True)
            return self.music_video.start(
                user_id=member.user_id,
                source_project=project,
                title=str(arguments.get("title") or project.name),
                concept=str(arguments.get("concept") or "cinematic music video"),
                aspect_ratio=str(arguments.get("aspect_ratio") or "16:9"),
                provider=str(arguments.get("provider") or "auto"),
                quality=str(arguments.get("quality") or "standard"),
                continuity=str(arguments.get("continuity") or "consistent cinematic visual language"),
            )

        return super()._execute_tool(member, name, arguments)
