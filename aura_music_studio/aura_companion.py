from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests

from .image_generation import ImageGenerationRequest, ImageGenerationService
from .image_jobs import ImageJobStore
from .plans import (
    ADVANCED_IMAGE_GENERATION,
    ADVANCED_VIDEO_GENERATION,
    IMAGE_GENERATION,
    IMAGE_HIGH_QUALITY,
    IMAGE_PROVIDER_CONTROL,
    IMAGE_TRANSPARENT_BACKGROUND,
    PRODUCER_CHAT,
    VIDEO_EXTENDED_DURATION,
    VIDEO_GENERATION,
    VIDEO_HIGH_QUALITY,
    VIDEO_PROVIDER_CONTROL,
    VIDEO_TO_VIDEO,
    VISUAL_FX_STUDIO,
)
from .producer import llm_plan
from .video_generation import VideoGenerationRequest, VideoGenerationService
from .video_jobs import VideoJobStore
from .visual_fx import VisualFxStore
from .web_access import AuraWebGateway


class AuraCompanionError(RuntimeError):
    pass


class AuraCompanionStore:
    """Tenant-bound conversation and explicit memory storage for Aura."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS aura_companion_threads (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    project_id TEXT,
                    scope TEXT NOT NULL DEFAULT 'creative',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_aura_threads_user_updated
                    ON aura_companion_threads(user_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS aura_companion_messages (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(thread_id) REFERENCES aura_companion_threads(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_aura_messages_thread_created
                    ON aura_companion_messages(thread_id, created_at ASC);

                CREATE TABLE IF NOT EXISTS aura_companion_memories (
                    user_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    memory_key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, scope, memory_key)
                );
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_thread(self, user_id: str, *, title: str, project_id: str | None = None, scope: str = "creative") -> dict:
        thread_id = uuid4().hex
        now = self._now()
        with self._connect() as con:
            con.execute(
                "INSERT INTO aura_companion_threads(id,user_id,title,project_id,scope,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (thread_id, user_id, (title or "Aura conversation")[:200], project_id, scope, now, now),
            )
        return self.get_thread(user_id, thread_id)

    def list_threads(self, user_id: str, limit: int = 50) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM aura_companion_threads WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
                (user_id, max(1, min(limit, 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_thread(self, user_id: str, thread_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_companion_threads WHERE user_id=? AND id=?", (user_id, thread_id)
            ).fetchone()
        if not row:
            raise AuraCompanionError("Aura conversation not found")
        return dict(row)

    def messages(self, user_id: str, thread_id: str, limit: int = 80) -> list[dict]:
        self.get_thread(user_id, thread_id)
        with self._connect() as con:
            rows = con.execute(
                """SELECT * FROM (
                       SELECT * FROM aura_companion_messages
                       WHERE user_id=? AND thread_id=? ORDER BY created_at DESC LIMIT ?
                   ) ORDER BY created_at ASC""",
                (user_id, thread_id, max(1, min(limit, 200))),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            except Exception:
                item["metadata"] = {}
                item.pop("metadata_json", None)
            items.append(item)
        return items

    def add_message(
        self,
        user_id: str,
        thread_id: str,
        role: str,
        content: str,
        *,
        metadata: dict | None = None,
    ) -> dict:
        self.get_thread(user_id, thread_id)
        if role not in {"user", "assistant", "tool"}:
            raise AuraCompanionError("Unsupported companion message role")
        now = self._now()
        message_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                "INSERT INTO aura_companion_messages(id,thread_id,user_id,role,content,metadata_json,created_at) VALUES (?,?,?,?,?,?,?)",
                (message_id, thread_id, user_id, role, content, json.dumps(metadata or {}), now),
            )
            con.execute(
                "UPDATE aura_companion_threads SET updated_at=? WHERE id=? AND user_id=?", (now, thread_id, user_id)
            )
        return {"id": message_id, "role": role, "content": content, "metadata": metadata or {}, "created_at": now}

    def set_memory(self, user_id: str, scope: str, key: str, value: Any) -> dict:
        key = (key or "").strip()[:120]
        if not key:
            raise AuraCompanionError("Memory key is required")
        scope = (scope or "personal").strip()[:80]
        now = self._now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO aura_companion_memories(user_id,scope,memory_key,value_json,updated_at)
                   VALUES (?,?,?,?,?) ON CONFLICT(user_id,scope,memory_key)
                   DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
                (user_id, scope, key, json.dumps(value), now),
            )
        return {"scope": scope, "key": key, "value": value, "updated_at": now}

    def memories(self, user_id: str, scopes: list[str] | None = None, limit: int = 100) -> list[dict]:
        with self._connect() as con:
            if scopes:
                marks = ",".join("?" for _ in scopes)
                rows = con.execute(
                    f"SELECT * FROM aura_companion_memories WHERE user_id=? AND scope IN ({marks}) ORDER BY updated_at DESC LIMIT ?",
                    (user_id, *scopes, max(1, min(limit, 250))),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM aura_companion_memories WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
                    (user_id, max(1, min(limit, 250))),
                ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["value"] = json.loads(item.pop("value_json"))
            except Exception:
                item["value"] = item.pop("value_json", "")
            result.append(item)
        return result

    def esp_access(self, user_id: str) -> dict:
        try:
            with self._connect() as con:
                row = con.execute(
                    "SELECT status,roles,tiktok_handle,region FROM esp_memberships WHERE user_id=?", (user_id,)
                ).fetchone()
        except sqlite3.OperationalError:
            row = None
        if not row:
            return {"status": "none", "roles": []}
        item = dict(row)
        raw = (item.get("roles") or "").replace(";", ",")
        roles = {part.strip().lower() for part in raw.split(",") if part.strip()}
        if "both" in roles:
            roles.update({"creator", "agent"})
        if item.get("status") == "owner":
            roles.update({"owner", "creator", "agent"})
        item["roles"] = sorted(roles)
        return item


class AuraCompanionService:
    """Role-aware AI companion and tool orchestrator for the entire Live Sound Studio system."""

    def __init__(self, db_path: str | Path | None = None):
        self.store = AuraCompanionStore(db_path)
        self.web = AuraWebGateway()
        self.images = ImageGenerationService()
        self.image_jobs = ImageJobStore(self.store.db_path)
        self.videos = VideoGenerationService()
        self.video_jobs = VideoJobStore(self.store.db_path)
        self.visual_fx = VisualFxStore(self.store.db_path)

    def access_profile(self, member) -> dict:
        esp = self.store.esp_access(member.user_id)
        roles = set(esp.get("roles") or []) if esp.get("status") in {"active", "owner"} else set()
        owner = "owner" in roles or member.user.get("billing_status") == "owner_comped"
        if owner:
            roles.update({"owner", "creator", "agent"})
        scopes = ["creative"]
        if "creator" in roles:
            scopes.append("esp_creator")
        if "agent" in roles:
            scopes.append("esp_agent")
        if owner:
            scopes.append("owner")
        return {
            "user_id": member.user_id,
            "plan": member.plan.id,
            "esp_status": esp.get("status", "none"),
            "roles": sorted(roles),
            "scopes": scopes,
            "owner": owner,
        }

    def capabilities(self, member) -> dict:
        access = self.access_profile(member)
        tools = [tool["name"] for tool in self._tool_definitions(member)]
        return {
            **access,
            "companion": True,
            "text_chat": True,
            "voice_interface": True,
            "persistent_threads": True,
            "explicit_memory": True,
            "project_context": True,
            "web_research": "web_search" in tools,
            "tools": tools,
            "provider_order": ["openai", "ollama", "deterministic"],
            "openai_model": self._openai_model(member) if os.getenv("OPENAI_API_KEY") else None,
            "role_boundary": "Aura receives only tools permitted for the signed-in member's plan and ESP role.",
        }

    def _openai_model(self, member) -> str:
        if member.plan.id == "pro" or self.access_profile(member)["owner"]:
            return os.getenv("AURA_OPENAI_CHAT_PRO_MODEL", "gpt-5.6-sol")
        return os.getenv("AURA_OPENAI_CHAT_MODEL", "gpt-5.6-terra")

    def _tool_definitions(self, member) -> list[dict[str, Any]]:
        if not member.plan.has(PRODUCER_CHAT):
            return []
        access = self.access_profile(member)
        tools: list[dict[str, Any]] = [
            {
                "type": "function",
                "name": "plan_music_change",
                "description": "Translate a music production request into safe non-destructive Live Sound Studio operations.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {"request": {"type": "string"}, "project_context": {"type": "object"}},
                    "required": ["request", "project_context"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "web_search",
                "description": "Search the public web through Aura's protected search gateway when fresh research is required.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 10}},
                    "required": ["query", "limit"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "remember",
                "description": "Save an explicit, useful user preference or project fact for future Aura conversations.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scope": {"type": "string"},
                        "key": {"type": "string"},
                        "value": {},
                    },
                    "required": ["scope", "key", "value"],
                    "additionalProperties": False,
                },
            },
        ]
        if member.plan.has(IMAGE_GENERATION):
            tools.append(
                {
                    "type": "function",
                    "name": "generate_image",
                    "description": "Generate a real image, poster, cover artwork, social graphic or thumbnail.",
                    "strict": True,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string"},
                            "mode": {"type": "string", "enum": ["image", "poster", "cover_art", "social_graphic", "thumbnail"]},
                            "aspect_ratio": {"type": "string", "enum": ["1:1", "4:5", "3:2", "2:3", "16:9", "9:16"]},
                            "quality": {"type": "string", "enum": ["standard", "high", "professional"]},
                            "provider": {"type": "string", "enum": ["auto", "local", "openai"]},
                            "background": {"type": "string", "enum": ["opaque", "transparent", "auto"]},
                            "project_id": {"type": ["string", "null"]},
                            "title_text": {"type": ["string", "null"]},
                            "subtitle_text": {"type": ["string", "null"]},
                            "call_to_action": {"type": ["string", "null"]},
                            "brand_direction": {"type": ["string", "null"]},
                        },
                        "required": ["prompt", "mode", "aspect_ratio", "quality", "provider", "background", "project_id", "title_text", "subtitle_text", "call_to_action", "brand_direction"],
                        "additionalProperties": False,
                    },
                }
            )
        if member.plan.has(VIDEO_GENERATION):
            tools.append(
                {
                    "type": "function",
                    "name": "generate_video",
                    "description": "Generate a real video through the configured Live Sound Studio video renderer.",
                    "strict": True,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string"},
                            "mode": {"type": "string", "enum": ["text_to_video", "image_to_video", "video_to_video"]},
                            "aspect_ratio": {"type": "string", "enum": ["9:16", "16:9", "1:1"]},
                            "duration_seconds": {"type": "integer", "minimum": 1, "maximum": 60},
                            "provider": {"type": "string", "enum": ["auto", "local", "openai", "runway"]},
                            "quality": {"type": "string", "enum": ["standard", "high", "professional"]},
                            "reference_url": {"type": ["string", "null"]},
                            "negative_prompt": {"type": ["string", "null"]},
                            "project_id": {"type": ["string", "null"]},
                            "target_platform": {"type": ["string", "null"]},
                        },
                        "required": ["prompt", "mode", "aspect_ratio", "duration_seconds", "provider", "quality", "reference_url", "negative_prompt", "project_id", "target_platform"],
                        "additionalProperties": False,
                    },
                }
            )
        if member.plan.has(VISUAL_FX_STUDIO):
            tools.append(
                {
                    "type": "function",
                    "name": "create_visual_fx_project",
                    "description": "Create a Pro non-destructive visual FX/timeline project.",
                    "strict": True,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "width": {"type": "integer", "minimum": 256, "maximum": 7680},
                            "height": {"type": "integer", "minimum": 256, "maximum": 7680},
                            "fps": {"type": "number", "minimum": 1, "maximum": 240},
                            "duration_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 21600},
                            "background": {"type": "string"},
                        },
                        "required": ["name", "width", "height", "fps", "duration_seconds", "background"],
                        "additionalProperties": False,
                    },
                }
            )
        if access["roles"]:
            tools.append(
                {
                    "type": "function",
                    "name": "esp_access_summary",
                    "description": "Return the signed-in member's ESP roles and authorized ESP system areas without exposing unauthorized content.",
                    "strict": True,
                    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                }
            )
        return tools

    def _system_prompt(self, member, *, project_context: dict | None = None) -> str:
        access = self.access_profile(member)
        return (
            "You are Aura, the persistent AI producer and companion for Elevate Souls Productions Presents: The Live Sound Studio. "
            "You can converse, write, explain, brainstorm and coordinate tools across the creative system. You are not a generic chat shell: "
            "when the member asks for a concrete creative operation and an allowed tool exists, use that tool. Never claim a render or edit "
            "completed unless the tool returned success. Never invent access to unavailable files, accounts, private services or ESP areas. "
            "Respect copyright, consent, reference-audio rights and approved-voice requirements. Never bypass plan or ESP role gates. "
            f"Current access profile: {json.dumps(access, ensure_ascii=False)}. "
            f"Current project context: {json.dumps(project_context or {}, ensure_ascii=False)[:12000]}. "
            "Ordinary Studio users may use only public creative features. ESP Creator/Agent tools are available only when those roles are in the "
            "access profile. Owner access may span the whole system, but sensitive changes still require explicit user intent. "
            "Be concise when the request is simple and detailed when the task is complex."
        )

    @staticmethod
    def _extract_output_text(payload: dict) -> str:
        direct = payload.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        texts: list[str] = []
        for item in payload.get("output") or []:
            if item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if content.get("type") == "output_text" and content.get("text"):
                    texts.append(str(content["text"]))
        return "\n".join(texts).strip()

    def _execute_tool(self, member, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        allowed = {item["name"] for item in self._tool_definitions(member)}
        if name not in allowed:
            raise AuraCompanionError(f"Aura tool is not permitted for this account: {name}")

        if name == "plan_music_change":
            plan = llm_plan(str(arguments.get("request") or ""), session_summary=arguments.get("project_context") or {})
            return plan.model_dump()

        if name == "web_search":
            return {"results": self.web.search(str(arguments.get("query") or ""), limit=int(arguments.get("limit") or 5))}

        if name == "remember":
            return self.store.set_memory(
                member.user_id,
                str(arguments.get("scope") or "personal"),
                str(arguments.get("key") or ""),
                arguments.get("value"),
            )

        if name == "generate_image":
            quality = str(arguments.get("quality") or "standard")
            provider = str(arguments.get("provider") or "auto")
            background = str(arguments.get("background") or "opaque")
            advanced = any(arguments.get(k) for k in ("subtitle_text", "call_to_action", "brand_direction"))
            if quality != "standard" and not member.plan.has(IMAGE_HIGH_QUALITY):
                raise AuraCompanionError("High-quality image rendering requires Pro")
            if provider != "auto" and not member.plan.has(IMAGE_PROVIDER_CONTROL):
                raise AuraCompanionError("Manual image provider control requires Pro")
            if background == "transparent" and not member.plan.has(IMAGE_TRANSPARENT_BACKGROUND):
                raise AuraCompanionError("Transparent image generation requires Pro")
            if advanced and not member.plan.has(ADVANCED_IMAGE_GENERATION):
                raise AuraCompanionError("Advanced image/poster direction requires Pro")
            request = ImageGenerationRequest(**arguments)
            result = self.images.generate(request)
            provenance = self.images.provenance_hash(result)
            self.image_jobs.save(
                user_id=member.user_id,
                result=result.to_dict(),
                mode=request.mode,
                prompt=request.prompt,
                project_id=request.project_id,
                provenance_hash=provenance,
            )
            return {**result.to_dict(), "provenance_hash": provenance}

        if name == "generate_video":
            mode = str(arguments.get("mode") or "text_to_video")
            duration = int(arguments.get("duration_seconds") or 8)
            quality = str(arguments.get("quality") or "standard")
            provider = str(arguments.get("provider") or "auto")
            negative = arguments.get("negative_prompt")
            if mode == "video_to_video" and not member.plan.has(VIDEO_TO_VIDEO):
                raise AuraCompanionError("Video-to-video requires Pro")
            if duration > 12 and not member.plan.has(VIDEO_EXTENDED_DURATION):
                raise AuraCompanionError("Video generations longer than 12 seconds require Pro")
            if quality != "standard" and not member.plan.has(VIDEO_HIGH_QUALITY):
                raise AuraCompanionError("High-quality video rendering requires Pro")
            if provider != "auto" and not member.plan.has(VIDEO_PROVIDER_CONTROL):
                raise AuraCompanionError("Manual video provider control requires Pro")
            if negative and not member.plan.has(ADVANCED_VIDEO_GENERATION):
                raise AuraCompanionError("Advanced video prompt controls require Pro")
            request = VideoGenerationRequest(**arguments)
            result = self.videos.generate(request)
            provenance = self.videos.provenance_hash(result)
            self.video_jobs.save(
                user_id=member.user_id,
                result=result.to_dict(),
                mode=request.mode,
                prompt=request.prompt,
                project_id=request.project_id,
                provenance_hash=provenance,
            )
            return {**result.to_dict(), "provenance_hash": provenance}

        if name == "create_visual_fx_project":
            if not member.plan.has(VISUAL_FX_STUDIO):
                raise AuraCompanionError("Visual FX Studio requires Pro")
            return self.visual_fx.create_project(user_id=member.user_id, **arguments)

        if name == "esp_access_summary":
            access = self.access_profile(member)
            return {
                "esp_status": access["esp_status"],
                "roles": access["roles"],
                "authorized_areas": [scope for scope in access["scopes"] if scope != "creative"],
            }

        raise AuraCompanionError(f"Unknown Aura tool: {name}")

    def _openai_chat(
        self,
        member,
        history: list[dict],
        *,
        project_context: dict | None,
        execute_tools: bool,
    ) -> tuple[str, list[dict]]:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise AuraCompanionError("OPENAI_API_KEY is not configured")
        model = self._openai_model(member)
        input_items = [
            {"role": item["role"], "content": item["content"]}
            for item in history
            if item.get("role") in {"user", "assistant"}
        ]
        payload = {
            "model": model,
            "instructions": self._system_prompt(member, project_context=project_context),
            "input": input_items,
            "tools": self._tool_definitions(member),
            "tool_choice": "auto" if execute_tools else "none",
            "parallel_tool_calls": False,
            "reasoning": {"effort": "medium" if member.plan.id == "pro" else "low"},
            "max_output_tokens": int(os.getenv("AURA_COMPANION_MAX_OUTPUT_TOKENS", "4096")),
            "store": False,
            "truncation": "auto",
        }
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=int(os.getenv("AURA_COMPANION_TIMEOUT", "180")),
        )
        if response.status_code >= 300:
            raise AuraCompanionError(f"OpenAI companion request failed ({response.status_code}): {response.text[:500]}")
        data = response.json()
        tool_events: list[dict] = []
        calls = [item for item in data.get("output") or [] if item.get("type") == "function_call"]
        if calls and execute_tools:
            outputs = []
            for call in calls[:8]:
                name = str(call.get("name") or "")
                try:
                    args = json.loads(call.get("arguments") or "{}")
                    result = self._execute_tool(member, name, args)
                    event = {"tool": name, "status": "completed", "arguments": args, "result": result}
                except Exception as exc:
                    event = {"tool": name, "status": "failed", "error": str(exc)}
                tool_events.append(event)
                outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.get("call_id"),
                        "output": json.dumps(event, ensure_ascii=False, default=str),
                    }
                )
            follow = requests.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "instructions": self._system_prompt(member, project_context=project_context),
                    "input": input_items + (data.get("output") or []) + outputs,
                    "tools": self._tool_definitions(member),
                    "tool_choice": "none",
                    "reasoning": {"effort": "low"},
                    "max_output_tokens": int(os.getenv("AURA_COMPANION_MAX_OUTPUT_TOKENS", "4096")),
                    "store": False,
                    "truncation": "auto",
                },
                timeout=int(os.getenv("AURA_COMPANION_TIMEOUT", "180")),
            )
            if follow.status_code >= 300:
                raise AuraCompanionError(f"OpenAI companion follow-up failed ({follow.status_code}): {follow.text[:500]}")
            data = follow.json()
        text = self._extract_output_text(data)
        if not text:
            text = "I completed the available Aura tool work, but the language model returned no final text response."
        return text, tool_events

    def _ollama_chat(self, member, history: list[dict], *, project_context: dict | None) -> str:
        base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        model = os.getenv("AURA_COMPANION_OLLAMA_MODEL", os.getenv("AURA_OLLAMA_MODEL", "qwen3:4b"))
        messages = [{"role": "system", "content": self._system_prompt(member, project_context=project_context)}]
        messages.extend({"role": item["role"], "content": item["content"]} for item in history if item.get("role") in {"user", "assistant"})
        response = requests.post(
            f"{base}/api/chat",
            json={"model": model, "stream": False, "messages": messages, "options": {"temperature": 0.35}},
            timeout=int(os.getenv("AURA_COMPANION_TIMEOUT", "180")),
        )
        response.raise_for_status()
        return str(response.json().get("message", {}).get("content") or "").strip()

    def chat(
        self,
        member,
        *,
        message: str,
        thread_id: str | None = None,
        project_id: str | None = None,
        project_context: dict | None = None,
        execute_tools: bool = True,
    ) -> dict:
        if not member.plan.has(PRODUCER_CHAT):
            raise AuraCompanionError("Aura Companion is not available on this membership")
        text = (message or "").strip()
        if not text:
            raise AuraCompanionError("Message is empty")
        access = self.access_profile(member)
        if thread_id:
            thread = self.store.get_thread(member.user_id, thread_id)
        else:
            title = text.replace("\n", " ")[:80]
            thread = self.store.create_thread(
                member.user_id,
                title=title,
                project_id=project_id,
                scope="owner" if access["owner"] else ("esp_agent" if "agent" in access["roles"] else ("esp_creator" if "creator" in access["roles"] else "creative")),
            )
        self.store.add_message(member.user_id, thread["id"], "user", text, metadata={"project_id": project_id})
        history = self.store.messages(member.user_id, thread["id"], limit=60)
        memories = self.store.memories(member.user_id, scopes=["personal", "creative", thread.get("scope", "creative")], limit=60)
        context = dict(project_context or {})
        if memories:
            context["explicit_aura_memories"] = memories

        provider = "deterministic"
        tool_events: list[dict] = []
        answer = ""
        if os.getenv("OPENAI_API_KEY"):
            try:
                answer, tool_events = self._openai_chat(
                    member, history, project_context=context, execute_tools=execute_tools
                )
                provider = "openai"
            except Exception as exc:
                tool_events.append({"tool": "openai_companion", "status": "failed", "error": str(exc)})
        if not answer and os.getenv("AURA_COMPANION_USE_OLLAMA", "true").lower() in {"1", "true", "yes", "on"}:
            try:
                answer = self._ollama_chat(member, history, project_context=context)
                provider = "ollama"
            except Exception as exc:
                tool_events.append({"tool": "ollama_companion", "status": "failed", "error": str(exc)})
        if not answer:
            plan = llm_plan(text, session_summary=context)
            answer = (
                "Aura is running in deterministic fallback mode. I mapped your request into the available Studio plan: "
                + json.dumps(plan.model_dump(), ensure_ascii=False)
            )
        assistant_message = self.store.add_message(
            member.user_id,
            thread["id"],
            "assistant",
            answer,
            metadata={"provider": provider, "tool_events": tool_events},
        )
        return {
            "thread": thread,
            "message": assistant_message,
            "provider": provider,
            "tool_events": tool_events,
            "access": access,
        }
