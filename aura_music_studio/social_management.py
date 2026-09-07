from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .request_context import current_user_id

SocialPlatform = Literal[
    "instagram",
    "facebook",
    "tiktok",
    "youtube",
    "linkedin",
    "pinterest",
    "threads",
    "x",
    "podcast",
    "google_business",
    "custom",
]
ContentStatus = Literal[
    "idea",
    "draft",
    "in_production",
    "pending_approval",
    "approved",
    "scheduled",
    "publishing",
    "published",
    "failed",
    "archived",
]
TaskStatus = Literal["todo", "in_progress", "blocked", "review", "done", "archived"]
ConnectionState = Literal["not_connected", "connected", "expired", "error"]
PublishState = Literal["not_requested", "planned", "blocked", "queued", "publishing", "published", "failed"]

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


PLATFORM_CAPABILITIES: dict[str, dict] = {
    "instagram": {
        "content_types": ["post", "reel", "story", "trial_reel"],
        "caption_limit": 2200,
        "max_media": 10,
        "planning": True,
        "auto_publish": "adapter_required",
        "analytics": "adapter_required",
        "inbox": "adapter_required",
    },
    "facebook": {
        "content_types": ["post", "reel", "story"],
        "caption_limit": 63206,
        "max_media": 10,
        "planning": True,
        "auto_publish": "adapter_required",
        "analytics": "adapter_required",
        "inbox": "adapter_required",
    },
    "tiktok": {
        "content_types": ["video", "photo"],
        "caption_limit": 2200,
        "max_media": 35,
        "planning": True,
        "auto_publish": "adapter_required",
        "analytics": "adapter_required",
        "inbox": "adapter_required",
    },
    "youtube": {
        "content_types": ["video", "short"],
        "caption_limit": 5000,
        "max_media": 1,
        "planning": True,
        "auto_publish": "adapter_required",
        "analytics": "adapter_required",
        "inbox": "adapter_required",
    },
    "linkedin": {
        "content_types": ["post", "document"],
        "caption_limit": 3000,
        "max_media": 20,
        "planning": True,
        "auto_publish": "adapter_required",
        "analytics": "adapter_required",
        "inbox": False,
    },
    "pinterest": {
        "content_types": ["pin"],
        "caption_limit": 500,
        "max_media": 5,
        "planning": True,
        "auto_publish": "adapter_required",
        "analytics": "adapter_required",
        "inbox": False,
    },
    "threads": {
        "content_types": ["post"],
        "caption_limit": 500,
        "max_media": 10,
        "planning": True,
        "auto_publish": "adapter_required",
        "analytics": "adapter_required",
        "inbox": False,
    },
    "x": {
        "content_types": ["post"],
        "caption_limit": 280,
        "max_media": 4,
        "planning": True,
        "auto_publish": "adapter_required",
        "analytics": "adapter_required",
        "inbox": "adapter_required",
    },
    "podcast": {
        "content_types": ["episode", "clip"],
        "caption_limit": None,
        "max_media": 1,
        "planning": True,
        "auto_publish": "adapter_required",
        "analytics": "adapter_required",
        "inbox": False,
    },
    "google_business": {
        "content_types": ["post", "event", "offer"],
        "caption_limit": 1500,
        "max_media": 1,
        "planning": True,
        "auto_publish": "adapter_required",
        "analytics": "adapter_required",
        "inbox": False,
    },
    "custom": {
        "content_types": ["custom"],
        "caption_limit": None,
        "max_media": None,
        "planning": True,
        "auto_publish": False,
        "analytics": False,
        "inbox": False,
    },
}


class BrandPersona(BaseModel):
    brand_name: str = ""
    niche: str = ""
    audience: str = ""
    goals: list[str] = Field(default_factory=list)
    voice: str = ""
    preferred_vocabulary: list[str] = Field(default_factory=list)
    prohibited_language: list[str] = Field(default_factory=list)
    content_pillars: list[str] = Field(default_factory=list)
    visual_guidelines: str = ""
    cta_rules: str = ""
    hashtag_banks: dict[str, list[str]] = Field(default_factory=dict)
    platform_priorities: list[SocialPlatform] = Field(default_factory=list)
    posting_cadence: str = ""
    memory: dict = Field(default_factory=dict)


class SocialConnection(BaseModel):
    id: str = Field(default_factory=lambda: new_id("conn"))
    platform: SocialPlatform
    account_label: str = ""
    account_external_id: str | None = None
    state: ConnectionState = "not_connected"
    supports_auto_publish: bool = False
    supports_analytics: bool = False
    supports_inbox: bool = False
    token_secret_ref: str | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class SocialProject(BaseModel):
    id: str = Field(default_factory=lambda: new_id("campaign"))
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    status: str = "active"
    start_at: str | None = None
    end_at: str | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class SocialTask(BaseModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    status: TaskStatus = "todo"
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    due_at: str | None = None
    assignee_ids: list[str] = Field(default_factory=list)
    project_id: str | None = None
    content_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class SocialNote(BaseModel):
    id: str = Field(default_factory=lambda: new_id("note"))
    title: str = Field(min_length=1, max_length=300)
    body: str = ""
    project_id: str | None = None
    content_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class PlatformVariant(BaseModel):
    platform: SocialPlatform
    content_type: str
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    first_comment: str = ""
    scheduled_at: str | None = None
    timezone: str | None = None
    all_day: bool = False
    media_refs: list[str] = Field(default_factory=list)
    cover_ref: str | None = None
    aspect_ratio: str | None = None
    auto_publish: bool = False
    publish_state: PublishState = "not_requested"
    external_post_id: str | None = None
    external_post_url: str | None = None
    failure_reason: str | None = None
    metadata: dict = Field(default_factory=dict)


class SocialContent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("post"))
    title: str = Field(min_length=1, max_length=300)
    status: ContentStatus = "idea"
    project_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    content_pillars: list[str] = Field(default_factory=list)
    assignee_ids: list[str] = Field(default_factory=list)
    variants: list[PlatformVariant] = Field(default_factory=list)
    source_creative_project: str | None = None
    source_creative_element_ids: list[str] = Field(default_factory=list)
    approval_required: bool = False
    approved_by: list[str] = Field(default_factory=list)
    approval_at: str | None = None
    notes: str = ""
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class ActivityEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("evt"))
    actor: str = "Aura"
    action: str
    entity_type: str
    entity_id: str
    detail: str = ""
    public: bool = False
    created_at: str = Field(default_factory=utc_now)


class SocialHouse(BaseModel):
    schema_version: int = 1
    id: str = Field(default_factory=lambda: new_id("space"))
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    avatar_ref: str | None = None
    persona: BrandPersona = Field(default_factory=BrandPersona)
    statuses: list[str] = Field(
        default_factory=lambda: [
            "Idea",
            "Draft",
            "In Production",
            "Pending Approval",
            "Approved",
            "Scheduled",
            "Published",
        ]
    )
    connections: list[SocialConnection] = Field(default_factory=list)
    projects: list[SocialProject] = Field(default_factory=list)
    content: list[SocialContent] = Field(default_factory=list)
    tasks: list[SocialTask] = Field(default_factory=list)
    notes: list[SocialNote] = Field(default_factory=list)
    activity: list[ActivityEvent] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class SocialHouseIndex(BaseModel):
    schema_version: int = 1
    spaces: list[dict] = Field(default_factory=list)


def _root() -> Path:
    user_id = current_user_id()
    if not user_id:
        # CLI/test fallback. Web requests should always have member context here.
        user_id = "local"
    root = Path(os.getenv("AURA_SOCIAL_ROOT", "data/social")).resolve() / user_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_id(value: str) -> str:
    value = (value or "").strip()
    if not _SAFE_ID.fullmatch(value):
        raise ValueError("Invalid Social House id")
    return value


class SocialHouseStore:
    def __init__(self):
        self.root = _root()
        self.index_path = self.root / "index.json"

    def _space_path(self, space_id: str) -> Path:
        target = (self.root / f"{_safe_id(space_id)}.json").resolve()
        if self.root not in target.parents:
            raise ValueError("Invalid Social House path")
        return target

    def _load_index(self) -> SocialHouseIndex:
        if not self.index_path.is_file():
            return SocialHouseIndex()
        return SocialHouseIndex.model_validate_json(self.index_path.read_text(encoding="utf-8"))

    def _save_index(self, index: SocialHouseIndex) -> None:
        self._atomic_write(self.index_path, index.model_dump(mode="json"))

    @staticmethod
    def _atomic_write(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)

    def list_spaces(self) -> list[dict]:
        return list(self._load_index().spaces)

    def create_space(self, name: str, description: str = "") -> SocialHouse:
        house = SocialHouse(name=name, description=description, persona=BrandPersona(brand_name=name))
        self.save(house)
        index = self._load_index()
        index.spaces.append({
            "id": house.id,
            "name": house.name,
            "description": house.description,
            "updated_at": house.updated_at,
        })
        self._save_index(index)
        return house

    def load(self, space_id: str) -> SocialHouse:
        path = self._space_path(space_id)
        if not path.is_file():
            raise FileNotFoundError(space_id)
        return SocialHouse.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, house: SocialHouse) -> SocialHouse:
        house.updated_at = utc_now()
        self._atomic_write(self._space_path(house.id), house.model_dump(mode="json"))
        index = self._load_index()
        for row in index.spaces:
            if row.get("id") == house.id:
                row.update({"name": house.name, "description": house.description, "updated_at": house.updated_at})
                self._save_index(index)
                break
        return house

    def update_persona(self, space_id: str, persona: BrandPersona) -> SocialHouse:
        house = self.load(space_id)
        house.persona = persona
        house.activity.append(ActivityEvent(action="persona_updated", entity_type="space", entity_id=house.id))
        return self.save(house)

    def add_project(self, space_id: str, project: SocialProject) -> SocialHouse:
        house = self.load(space_id)
        house.projects.append(project)
        house.activity.append(ActivityEvent(action="project_created", entity_type="project", entity_id=project.id, detail=project.name))
        return self.save(house)

    def add_task(self, space_id: str, task: SocialTask) -> SocialHouse:
        house = self.load(space_id)
        house.tasks.append(task)
        house.activity.append(ActivityEvent(action="task_created", entity_type="task", entity_id=task.id, detail=task.title))
        return self.save(house)

    def add_note(self, space_id: str, note: SocialNote) -> SocialHouse:
        house = self.load(space_id)
        house.notes.append(note)
        house.activity.append(ActivityEvent(action="note_created", entity_type="note", entity_id=note.id, detail=note.title))
        return self.save(house)

    def add_content(self, space_id: str, content: SocialContent) -> SocialHouse:
        house = self.load(space_id)
        self.validate_content(content)
        house.content.append(content)
        house.activity.append(ActivityEvent(action="content_created", entity_type="content", entity_id=content.id, detail=content.title))
        return self.save(house)

    def update_content_status(self, space_id: str, content_id: str, status: ContentStatus, *, actor: str = "Aura") -> SocialHouse:
        house = self.load(space_id)
        content = next((item for item in house.content if item.id == content_id), None)
        if content is None:
            raise KeyError(content_id)
        content.status = status
        content.updated_at = utc_now()
        house.activity.append(ActivityEvent(actor=actor, action="content_status", entity_type="content", entity_id=content.id, detail=status))
        return self.save(house)

    def approve_content(self, space_id: str, content_id: str, approver: str) -> SocialHouse:
        house = self.load(space_id)
        content = next((item for item in house.content if item.id == content_id), None)
        if content is None:
            raise KeyError(content_id)
        if approver not in content.approved_by:
            content.approved_by.append(approver)
        content.approval_at = utc_now()
        content.status = "approved"
        content.updated_at = utc_now()
        house.activity.append(ActivityEvent(actor=approver, action="content_approved", entity_type="content", entity_id=content.id))
        return self.save(house)

    def connect_placeholder(self, space_id: str, connection: SocialConnection) -> SocialHouse:
        """Register integration state without storing raw access tokens in Social House JSON."""
        house = self.load(space_id)
        existing = next((item for item in house.connections if item.id == connection.id), None)
        if existing:
            house.connections = [connection if item.id == connection.id else item for item in house.connections]
        else:
            house.connections.append(connection)
        house.activity.append(ActivityEvent(action="connection_state", entity_type="connection", entity_id=connection.id, detail=f"{connection.platform}:{connection.state}"))
        return self.save(house)

    @staticmethod
    def validate_content(content: SocialContent) -> None:
        from .esp_social_publish_capabilities import implemented_content_types

        for variant in content.variants:
            capability = PLATFORM_CAPABILITIES.get(variant.platform)
            if capability is None:
                raise ValueError(f"Unsupported platform: {variant.platform}")
            if variant.content_type not in capability["content_types"] and variant.platform != "custom":
                raise ValueError(f"Unsupported {variant.platform} content type: {variant.content_type}")
            limit = capability.get("caption_limit")
            combined = variant.caption
            if variant.hashtags:
                combined += " " + " ".join(f"#{tag.lstrip('#')}" for tag in variant.hashtags)
            if limit is not None and len(combined) > int(limit):
                raise ValueError(f"{variant.platform} caption/hashtags exceed {limit} characters")
            max_media = capability.get("max_media")
            if max_media is not None and len(variant.media_refs) > int(max_media):
                raise ValueError(f"{variant.platform} variant exceeds {max_media} media items")
            if variant.auto_publish:
                if capability.get("auto_publish") is False:
                    raise ValueError(f"{variant.platform} is planning-only in this deployment")
                if variant.content_type not in implemented_content_types(variant.platform):
                    raise ValueError(
                        f"{variant.platform} {variant.content_type} is planning-only; no runtime publishing adapter implements that content type"
                    )

    def publishing_readiness(self, space_id: str, content_id: str) -> dict:
        from .esp_social_publish_capabilities import resolve_publish_capability

        house = self.load(space_id)
        content = next((item for item in house.content if item.id == content_id), None)
        if content is None:
            raise KeyError(content_id)
        self.validate_content(content)
        connections_by_platform = {item.platform: item for item in house.connections}
        rows = []
        ready = True
        for variant in content.variants:
            if not variant.auto_publish:
                rows.append({
                    "platform": variant.platform,
                    "content_type": variant.content_type,
                    "ready": True,
                    "mode": "planning_only",
                })
                continue
            capability = resolve_publish_capability(
                connections_by_platform.get(variant.platform),
                platform=variant.platform,
                content_type=variant.content_type,
            )
            reasons = list(capability.reasons)
            if not variant.scheduled_at:
                reasons.append("scheduled_at is required")
            if content.approval_required and content.status not in {"approved", "scheduled"}:
                reasons.append("approval gate not satisfied")
            row_ready = not reasons
            ready = ready and row_ready
            rows.append({
                "platform": variant.platform,
                "content_type": variant.content_type,
                "ready": row_ready,
                "reasons": reasons,
                "capability": capability.model_dump(mode="json"),
            })
        return {"content_id": content.id, "ready": ready, "variants": rows}


def platform_capabilities() -> dict:
    from .esp_social_publish_capabilities import implementation_capabilities

    implemented = implementation_capabilities()
    result: dict[str, dict] = {}
    for key, value in PLATFORM_CAPABILITIES.items():
        row = dict(value)
        runtime = implemented.get(key, {})
        row["auto_publish_implemented"] = bool(runtime.get("auto_publish_implemented", False))
        row["auto_publish_content_types"] = list(runtime.get("auto_publish_content_types", []))
        row["publishing_adapters"] = list(runtime.get("publishing_adapters", []))
        result[key] = row
    return result
