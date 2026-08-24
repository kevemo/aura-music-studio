from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

CreativeKind = Literal["music", "audio", "video", "image", "voice", "text", "reference"]
ElementStatus = Literal["draft", "planned", "queued", "rendering", "ready", "failed", "archived"]
DirectiveStatus = Literal["planned", "ready_for_renderer", "queued", "running", "completed", "failed"]
DirectiveOperation = Literal[
    "create",
    "revise",
    "replace",
    "extend",
    "transform",
    "arrange",
    "mix",
    "master",
    "sync",
    "storyboard",
    "style",
    "analyze",
]
InputMode = Literal["text", "voice", "upload", "mixed"]

MANIFEST_FILENAME = "creative_manifest.json"

# This registry deliberately distinguishes connected production paths from integration
# slots. The public API must never claim video/image output exists before a renderer is
# actually wired into the project.
CREATIVE_CAPABILITIES: dict[str, dict[str, str]] = {
    "music": {"state": "connected", "renderer_route": "music_audio_stack"},
    "audio": {"state": "connected", "renderer_route": "music_audio_stack"},
    "voice": {"state": "partial", "renderer_route": "voice_performance_stack"},
    "video": {"state": "integration_slot", "renderer_route": "video_renderer_adapter"},
    "image": {"state": "integration_slot", "renderer_route": "image_renderer_adapter"},
    "text": {"state": "connected", "renderer_route": "aura_text_orchestrator"},
    "reference": {"state": "connected", "renderer_route": "project_context"},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class CreativeElement(BaseModel):
    id: str = Field(default_factory=lambda: new_id("el"))
    kind: CreativeKind
    label: str = Field(min_length=1, max_length=200)
    role: str = Field(default="", max_length=120)
    status: ElementStatus = "draft"
    source_type: Literal["generated", "uploaded", "recorded", "reference", "derived", "legacy"] = "generated"
    source_ref: str | None = Field(default=None, max_length=1000)
    parent_ids: list[str] = Field(default_factory=list, max_length=100)
    prompt: str = Field(default="", max_length=6000)
    metadata: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class CreativeReference(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ref"))
    kind: CreativeKind
    label: str = Field(min_length=1, max_length=200)
    source_ref: str = Field(min_length=1, max_length=1000)
    usage: str = Field(default="creative reference", max_length=500)
    rights_confirmed: bool = False
    metadata: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def require_rights_confirmation(self):
        if not self.rights_confirmed:
            raise ValueError(
                "rights_confirmed must be true before a reference is attached to a creative project"
            )
        return self


class CreativeDirective(BaseModel):
    id: str = Field(default_factory=lambda: new_id("dir"))
    instruction: str = Field(min_length=1, max_length=6000)
    input_mode: InputMode = "text"
    operation: DirectiveOperation = "revise"
    target_kind: CreativeKind | None = None
    target_element_ids: list[str] = Field(default_factory=list, max_length=100)
    reference_ids: list[str] = Field(default_factory=list, max_length=100)
    preserve_element_ids: list[str] = Field(default_factory=list, max_length=100)
    status: DirectiveStatus = "planned"
    renderer_route: str | None = None
    capability_state: str = "integration_slot"
    metadata: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class CreativeManifest(BaseModel):
    schema_version: int = 1
    project_name: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    project_intent: str = Field(default="", max_length=4000)
    elements: list[CreativeElement] = Field(default_factory=list)
    references: list[CreativeReference] = Field(default_factory=list)
    directives: list[CreativeDirective] = Field(default_factory=list)
    active_element_ids: list[str] = Field(default_factory=list, max_length=200)
    metadata: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class CreativeProjectStore:
    """Persistent, non-destructive cross-media project metadata.

    The store intentionally lives beside the legacy music manifest rather than replacing
    it. Existing music projects therefore gain cross-media context without a migration of
    their proven audio pipeline, while image/video-only projects can use the same layer.
    """

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / MANIFEST_FILENAME

    def exists(self) -> bool:
        return self.path.is_file()

    def initialize(
        self,
        *,
        project_name: str,
        title: str,
        project_intent: str = "",
        metadata: dict | None = None,
    ) -> CreativeManifest:
        self.project_dir.mkdir(parents=True, exist_ok=True)
        if self.exists():
            return self.load()
        manifest = CreativeManifest(
            project_name=project_name,
            title=title,
            project_intent=project_intent,
            metadata=metadata or {},
        )
        self.save(manifest)
        return manifest

    def load(self) -> CreativeManifest:
        if not self.exists():
            raise FileNotFoundError(self.path)
        return CreativeManifest.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, manifest: CreativeManifest) -> CreativeManifest:
        self.project_dir.mkdir(parents=True, exist_ok=True)
        manifest.updated_at = utc_now()
        payload = json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=False)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(payload + "\n", encoding="utf-8")
        temporary.replace(self.path)
        return manifest

    def add_element(self, element: CreativeElement) -> CreativeManifest:
        manifest = self.load()
        existing = {item.id for item in manifest.elements}
        if element.id in existing:
            raise ValueError(f"Creative element already exists: {element.id}")
        missing_parents = [parent for parent in element.parent_ids if parent not in existing]
        if missing_parents:
            raise ValueError(f"Unknown parent element(s): {', '.join(missing_parents)}")
        manifest.elements.append(element)
        if element.status != "archived" and element.id not in manifest.active_element_ids:
            manifest.active_element_ids.append(element.id)
        return self.save(manifest)

    def update_element(
        self,
        element_id: str,
        *,
        label: str | None = None,
        role: str | None = None,
        status: ElementStatus | None = None,
        source_ref: str | None = None,
        prompt: str | None = None,
        metadata: dict | None = None,
    ) -> CreativeManifest:
        manifest = self.load()
        element = next((item for item in manifest.elements if item.id == element_id), None)
        if element is None:
            raise KeyError(element_id)
        if label is not None:
            element.label = label
        if role is not None:
            element.role = role
        if status is not None:
            element.status = status
        if source_ref is not None:
            element.source_ref = source_ref
        if prompt is not None:
            element.prompt = prompt
        if metadata is not None:
            element.metadata = {**element.metadata, **metadata}
        element.updated_at = utc_now()
        if element.status == "archived":
            manifest.active_element_ids = [value for value in manifest.active_element_ids if value != element_id]
        elif element_id not in manifest.active_element_ids:
            manifest.active_element_ids.append(element_id)
        return self.save(manifest)

    def add_reference(self, reference: CreativeReference) -> CreativeManifest:
        manifest = self.load()
        if any(item.id == reference.id for item in manifest.references):
            raise ValueError(f"Creative reference already exists: {reference.id}")
        manifest.references.append(reference)
        return self.save(manifest)

    def add_directive(self, directive: CreativeDirective) -> CreativeManifest:
        manifest = self.load()
        element_ids = {item.id for item in manifest.elements}
        reference_ids = {item.id for item in manifest.references}

        missing_targets = [value for value in directive.target_element_ids if value not in element_ids]
        missing_preserve = [value for value in directive.preserve_element_ids if value not in element_ids]
        missing_references = [value for value in directive.reference_ids if value not in reference_ids]
        if missing_targets:
            raise ValueError(f"Unknown target element(s): {', '.join(missing_targets)}")
        if missing_preserve:
            raise ValueError(f"Unknown preserved element(s): {', '.join(missing_preserve)}")
        if missing_references:
            raise ValueError(f"Unknown reference(s): {', '.join(missing_references)}")

        if directive.target_kind is not None:
            capability = CREATIVE_CAPABILITIES[directive.target_kind]
        elif directive.target_element_ids:
            target = next(item for item in manifest.elements if item.id == directive.target_element_ids[0])
            directive.target_kind = target.kind
            capability = CREATIVE_CAPABILITIES[target.kind]
        else:
            capability = {"state": "integration_slot", "renderer_route": "aura_orchestrator"}

        directive.capability_state = capability["state"]
        directive.renderer_route = capability["renderer_route"]
        directive.status = "ready_for_renderer" if capability["state"] == "connected" else "planned"
        directive.updated_at = utc_now()
        manifest.directives.append(directive)
        return self.save(manifest)

    def update_directive(
        self,
        directive_id: str,
        *,
        status: DirectiveStatus | None = None,
        renderer_route: str | None = None,
        capability_state: str | None = None,
        metadata: dict | None = None,
    ) -> CreativeManifest:
        manifest = self.load()
        directive = next((item for item in manifest.directives if item.id == directive_id), None)
        if directive is None:
            raise KeyError(directive_id)
        if status is not None:
            directive.status = status
        if renderer_route is not None:
            directive.renderer_route = renderer_route
        if capability_state is not None:
            directive.capability_state = capability_state
        if metadata is not None:
            directive.metadata = {**directive.metadata, **metadata}
        directive.updated_at = utc_now()
        return self.save(manifest)


def public_capabilities() -> dict[str, dict[str, str]]:
    return {key: dict(value) for key, value in CREATIVE_CAPABILITIES.items()}
