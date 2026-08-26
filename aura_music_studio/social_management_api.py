from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .content_safety import enforce_creation_policy, public_policy_summary
from .esp_niche import require_esp_social_member
from .esp_social_publish_queue_routes import router as publish_queue_router
from .social_management import (
    BrandPersona,
    ContentStatus,
    PlatformVariant,
    SocialConnection,
    SocialContent,
    SocialHouseStore,
    SocialNote,
    SocialProject,
    SocialTask,
    platform_capabilities,
)

# Social management is deliberately nested under the private ESP Command Center.
# It is not a general Pulsar-Frequency House / Creative Studio API.
router = APIRouter(prefix="/command-center/api/social", tags=["esp-social-management"])


class CreateSpaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    start_at: str | None = None
    end_at: str | None = None


class CreateTaskRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    status: str = "todo"
    priority: str = "normal"
    due_at: str | None = None
    assignee_ids: list[str] = Field(default_factory=list)
    project_id: str | None = None
    content_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class CreateNoteRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = ""
    project_id: str | None = None
    content_id: str | None = None
    tags: list[str] = Field(default_factory=list)


class CreateContentRequest(BaseModel):
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
    notes: str = ""


class UpdateStatusRequest(BaseModel):
    status: ContentStatus


class ApproveRequest(BaseModel):
    approver: str = Field(min_length=1, max_length=200)


class PersonaRequest(BrandPersona):
    pass


class ConnectionRequest(BaseModel):
    platform: str
    account_label: str = ""
    account_external_id: str | None = None
    state: str = "not_connected"
    supports_auto_publish: bool = False
    supports_analytics: bool = False
    supports_inbox: bool = False
    token_secret_ref: str | None = None
    metadata: dict = Field(default_factory=dict)


def _member(request: Request):
    """Return the signed-in member only after all ESP social gates pass.

    Required gates are enforced independently of the user interface:
    - normal authenticated site membership;
    - active ESP Creator/Agent/Owner membership;
    - completed ESP niche profile;
    - affiliation attestation that the account is not represented by another Creator Network.
    """
    member, _esp_membership, _profile = require_esp_social_member(request)
    return member


def _store() -> SocialHouseStore:
    return SocialHouseStore()


def _load(space_id: str):
    try:
        return _store().load(space_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _enforce(*texts: str | None) -> None:
    try:
        enforce_creation_policy(*texts, context="ESP social content")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/platforms")
def social_platforms(request: Request):
    _member(request)
    return {
        "capabilities": platform_capabilities(),
        "scope": "private_esp_creator_agent_hub",
        "content_safety": public_policy_summary(),
        "truthful_state": "Planning is active. Publishing/analytics/inbox require official platform adapters and authorised connections.",
    }


@router.get("/spaces")
def list_spaces(request: Request):
    member = _member(request)
    return {"plan": member.plan.id, "scope": "ESP", "spaces": _store().list_spaces()}


@router.post("/spaces")
def create_space(body: CreateSpaceRequest, request: Request):
    _member(request)
    _enforce(body.name, body.description)
    house = _store().create_space(body.name, body.description)
    return house.model_dump(mode="json")


@router.get("/spaces/{space_id}")
def get_space(space_id: str, request: Request):
    _member(request)
    return _load(space_id).model_dump(mode="json")


@router.put("/spaces/{space_id}/persona")
def update_persona(space_id: str, body: PersonaRequest, request: Request):
    _member(request)
    _enforce(
        body.brand_name,
        body.niche,
        body.audience,
        body.voice,
        body.visual_guidelines,
        body.cta_rules,
        *body.goals,
        *body.content_pillars,
    )
    try:
        house = _store().update_persona(space_id, BrandPersona.model_validate(body.model_dump()))
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    return house.model_dump(mode="json")


@router.post("/spaces/{space_id}/projects")
def create_project(space_id: str, body: CreateProjectRequest, request: Request):
    _member(request)
    _enforce(body.name, body.description, *body.tags)
    project = SocialProject(**body.model_dump())
    try:
        house = _store().add_project(space_id, project)
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    return {"project": project.model_dump(mode="json"), "house": house.model_dump(mode="json")}


@router.post("/spaces/{space_id}/tasks")
def create_task(space_id: str, body: CreateTaskRequest, request: Request):
    _member(request)
    _enforce(body.title, body.description, *body.tags)
    try:
        task = SocialTask.model_validate(body.model_dump())
        house = _store().add_task(space_id, task)
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"task": task.model_dump(mode="json"), "house": house.model_dump(mode="json")}


@router.post("/spaces/{space_id}/notes")
def create_note(space_id: str, body: CreateNoteRequest, request: Request):
    _member(request)
    _enforce(body.title, body.body, *body.tags)
    note = SocialNote(**body.model_dump())
    try:
        house = _store().add_note(space_id, note)
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    return {"note": note.model_dump(mode="json"), "house": house.model_dump(mode="json")}


@router.post("/spaces/{space_id}/content")
def create_content(space_id: str, body: CreateContentRequest, request: Request):
    _member(request)
    variant_texts: list[str] = []
    for variant in body.variants:
        variant_texts.extend([variant.caption, variant.first_comment, *variant.hashtags])
    _enforce(body.title, body.notes, *body.tags, *body.content_pillars, *variant_texts)
    content = SocialContent(**body.model_dump())
    try:
        house = _store().add_content(space_id, content)
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"content": content.model_dump(mode="json"), "house": house.model_dump(mode="json")}


@router.patch("/spaces/{space_id}/content/{content_id}/status")
def update_status(space_id: str, content_id: str, body: UpdateStatusRequest, request: Request):
    member = _member(request)
    try:
        house = _store().update_content_status(space_id, content_id, body.status, actor=member.user_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except KeyError as exc:
        raise HTTPException(404, "Content not found") from exc
    return house.model_dump(mode="json")


@router.post("/spaces/{space_id}/content/{content_id}/approve")
def approve_content(space_id: str, content_id: str, body: ApproveRequest, request: Request):
    _member(request)
    try:
        house = _store().approve_content(space_id, content_id, body.approver)
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except KeyError as exc:
        raise HTTPException(404, "Content not found") from exc
    return house.model_dump(mode="json")


@router.get("/spaces/{space_id}/content/{content_id}/publishing-readiness")
def publishing_readiness(space_id: str, content_id: str, request: Request):
    _member(request)
    try:
        return _store().publishing_readiness(space_id, content_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except KeyError as exc:
        raise HTTPException(404, "Content not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/spaces/{space_id}/connections")
def register_connection_state(space_id: str, body: ConnectionRequest, request: Request):
    _member(request)
    _enforce(body.account_label)
    # This endpoint stores only connection/capability state. OAuth access tokens belong in
    # deployment secret storage and must be referenced indirectly through token_secret_ref.
    try:
        connection = SocialConnection.model_validate(body.model_dump())
        house = _store().connect_placeholder(space_id, connection)
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"connection": connection.model_dump(mode="json"), "house": house.model_dump(mode="json")}


# Queue routes inherit the private ESP social prefix and the same server-side member gates.
router.include_router(publish_queue_router)
