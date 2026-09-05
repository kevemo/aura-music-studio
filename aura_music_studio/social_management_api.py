from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .content_safety import enforce_creation_policy, public_policy_summary
from .esp_niche import require_esp_social_member
from .esp_social_facebook_oauth import (
    facebook_oauth_callback,
    facebook_oauth_capability,
    facebook_oauth_disconnect,
    facebook_oauth_start,
)
from .esp_social_oauth import (
    oauth_callback,
    oauth_disconnect,
    oauth_providers,
    oauth_start,
)
from .esp_social_publish_queue_routes import router as publish_queue_router
from .esp_social_secret_refs import valid_social_token_ref
from .esp_social_threads_oauth import (
    threads_oauth_callback,
    threads_oauth_capability,
    threads_oauth_disconnect,
    threads_oauth_start,
)
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


def _reject_client_publish_runtime_state(variants: list[PlatformVariant]) -> None:
    for variant in variants:
        if (
            variant.publish_state != "not_requested"
            or variant.external_post_id is not None
            or variant.external_post_url is not None
            or variant.failure_reason is not None
            or "published_via_adapter" in variant.metadata
            or "published_confirmed_at" in variant.metadata
        ):
            raise HTTPException(
                400,
                "Publishing runtime state is provider-managed and cannot be supplied when creating content.",
            )


_SENSITIVE_CONNECTION_METADATA_KEYS = {
    "access_token",
    "refresh_token",
    "oauth_token",
    "token",
    "secret",
    "client_secret",
    "password",
    "api_key",
    "authorization",
    "bearer",
}


def _metadata_contains_raw_secret(value) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _SENSITIVE_CONNECTION_METADATA_KEYS:
                return True
            if _metadata_contains_raw_secret(item):
                return True
    elif isinstance(value, list):
        return any(_metadata_contains_raw_secret(item) for item in value)
    return False


def _reject_connection_credentials(body: ConnectionRequest) -> None:
    if body.token_secret_ref is not None and not valid_social_token_ref(body.token_secret_ref):
        raise HTTPException(
            400,
            "Social provider credentials must use a social-token://<alias> or social-oauth://<credential-id> reference; raw tokens and arbitrary secret references cannot be stored in Social House data.",
        )
    if _metadata_contains_raw_secret(body.metadata):
        raise HTTPException(
            400,
            "Raw provider credentials cannot be stored in SocialConnection metadata. Use the encrypted OAuth vault or a deployment-held social-token alias.",
        )


@router.get("/platforms")
def social_platforms(request: Request):
    _member(request)
    return {
        "capabilities": platform_capabilities(),
        "scope": "private_esp_creator_agent_hub",
        "content_safety": public_policy_summary(),
        "truthful_state": (
            "Planning, approvals, the production queue, provider adapters, and the private OAuth connection layer are implemented. "
            "Real publishing becomes active only for a provider app that is configured, approved where required, and explicitly authorised by the member."
        ),
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
        house = _store().update_persona(
            space_id,
            BrandPersona.model_validate(body.model_dump()),
        )
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
    return {
        "project": project.model_dump(mode="json"),
        "house": house.model_dump(mode="json"),
    }


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
    _reject_client_publish_runtime_state(body.variants)
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
def update_status(
    space_id: str,
    content_id: str,
    body: UpdateStatusRequest,
    request: Request,
):
    member = _member(request)
    try:
        house = _store().update_content_status(
            space_id,
            content_id,
            body.status,
            actor=member.user_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except KeyError as exc:
        raise HTTPException(404, "Content not found") from exc
    return house.model_dump(mode="json")


@router.post("/spaces/{space_id}/content/{content_id}/approve")
def approve_content(
    space_id: str,
    content_id: str,
    body: ApproveRequest,
    request: Request,
):
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
def register_connection_state(
    space_id: str,
    body: ConnectionRequest,
    request: Request,
):
    _member(request)
    _enforce(body.account_label)
    _reject_connection_credentials(body)
    # This endpoint stores only connection/capability state. OAuth access/refresh tokens
    # remain in the encrypted member vault or the deployment's dedicated social-token namespace.
    try:
        connection = SocialConnection.model_validate(body.model_dump())
        house = _store().connect_placeholder(space_id, connection)
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "connection": connection.model_dump(mode="json"),
        "house": house.model_dump(mode="json"),
    }


# Nested private routes inherit /command-center/api/social and the same server-side ESP gates.
router.include_router(publish_queue_router)

# Register provider-specific OAuth endpoints directly on the canonical Social router. FastAPI
# copies child-router routes at include time, so OAuth routes that must precede a generic wildcard
# are kept here explicitly. Every endpoint still independently rechecks the ESP membership gate.
router.add_api_route(
    "/oauth/facebook/capability",
    facebook_oauth_capability,
    methods=["GET"],
)
router.add_api_route(
    "/oauth/facebook/start",
    facebook_oauth_start,
    methods=["GET"],
    include_in_schema=False,
)
router.add_api_route(
    "/oauth/facebook/callback",
    facebook_oauth_callback,
    methods=["GET"],
    response_class=HTMLResponse,
    include_in_schema=False,
)
router.add_api_route(
    "/oauth/facebook/disconnect",
    facebook_oauth_disconnect,
    methods=["POST"],
)
router.add_api_route(
    "/oauth/threads/capability",
    threads_oauth_capability,
    methods=["GET"],
)
router.add_api_route(
    "/oauth/threads/start",
    threads_oauth_start,
    methods=["GET"],
    include_in_schema=False,
)
router.add_api_route(
    "/oauth/threads/callback",
    threads_oauth_callback,
    methods=["GET"],
    response_class=HTMLResponse,
    include_in_schema=False,
)
router.add_api_route(
    "/oauth/threads/disconnect",
    threads_oauth_disconnect,
    methods=["POST"],
)

# Register the existing TikTok/Instagram/YouTube OAuth endpoints directly as well. Keep every
# provider-specific route above these wildcards so an exact provider flow can never be shadowed.
router.add_api_route("/oauth/providers", oauth_providers, methods=["GET"])
router.add_api_route(
    "/oauth/{provider}/start",
    oauth_start,
    methods=["GET"],
    include_in_schema=False,
)
router.add_api_route(
    "/oauth/{provider}/callback",
    oauth_callback,
    methods=["GET"],
    response_class=HTMLResponse,
    include_in_schema=False,
)
router.add_api_route(
    "/oauth/{provider}/disconnect",
    oauth_disconnect,
    methods=["POST"],
)
