from __future__ import annotations

import os
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .esp_command_center import esp
from .esp_niche import require_esp_hub_member
from .owner_identity import owner_session_authorized
from .shared_sky_battles import BattleDomainError, SharedSkyBattleStore

router = APIRouter(tags=["Shared Sky Multi-Host & Battles"])


def _creator_eligibility(user_id: str) -> tuple[bool, str]:
    membership = esp.membership(user_id)
    if not membership or membership.get("status") not in {"active", "owner"}:
        return False, "Active Elevate Souls Productions creator eligibility is required"
    if membership.get("status") == "owner":
        return True, "owner"
    role = str(membership.get("roles") or "").strip().lower()
    if role not in {"creator", "both"}:
        return False, "An active creator role is required for multi-host participation"
    return True, "creator"


def _transport_capacity(live_session_id: str) -> int:
    """Compatibility boundary for Chat 2 / media-plane capacity.

    Chat 2 currently exposes broadcast transport state but no participant-capacity method.
    If a future canonical transport object supplies ``participant_capacity`` we consume it.
    Until then production stays fail-closed unless deployment explicitly sets a measured cap.
    """
    try:
        from .shared_sky_transport_domain import transport  # type: ignore

        provider = getattr(transport, "participant_capacity", None)
        if callable(provider):
            return int(provider(live_session_id))
    except (ImportError, AttributeError, TypeError, ValueError):
        pass
    try:
        return int(os.getenv("SHARED_SKY_MULTIHOST_MAX_PARTICIPANTS", "0") or 0)
    except ValueError:
        return 0


def _reconnect_grace() -> int:
    try:
        return int(os.getenv("SHARED_SKY_PARTICIPANT_RECONNECT_GRACE_SECONDS", "45") or 45)
    except ValueError:
        return 45


battle_store = SharedSkyBattleStore(
    esp.db_path,
    transport_capacity=_transport_capacity,
    participant_eligibility=_creator_eligibility,
    reconnect_grace_seconds=_reconnect_grace(),
)


def _correlation(request: Request) -> str:
    value = str(request.headers.get("x-correlation-id") or "").strip()[:160]
    return value or f"battle-{uuid4().hex}"


def _member(request: Request):
    return require_esp_hub_member(request)


def _creator_member(request: Request):
    member, membership = _member(request)
    allowed, reason = _creator_eligibility(str(member.user_id))
    if not allowed:
        raise HTTPException(403, detail={"code": "creator_ineligible", "message": reason})
    return member, membership


def _owner(request: Request) -> None:
    if not owner_session_authorized(request):
        raise HTTPException(401, detail={"code": "unauthenticated", "message": "Owner authentication required"})


def _run(request: Request, operation):
    correlation_id = _correlation(request)
    try:
        return operation(correlation_id)
    except BattleDomainError as exc:
        raise HTTPException(
            exc.status_code,
            detail={"code": exc.code, "message": exc.message, "correlation_id": correlation_id},
        ) from exc


class InvitationCreate(BaseModel):
    invited_user_id: str = Field(min_length=1, max_length=160)
    message: str = Field(default="", max_length=500)
    ttl_seconds: int = Field(default=900, ge=60, le=86400)


class InvitationResponse(BaseModel):
    invite_token: str = Field(min_length=20, max_length=512)
    accept: bool


class JoinRequestCreate(BaseModel):
    message: str = Field(default="", max_length=500)
    ttl_seconds: int = Field(default=600, ge=60, le=3600)


class JoinRequestDecision(BaseModel):
    approve: bool


class ReadinessUpdate(BaseModel):
    terms_accepted: bool
    camera_ready: bool
    microphone_ready: bool
    audio_available: bool | None = None
    video_available: bool | None = None
    connection_state: Literal["connected", "degraded", "reconnecting", "unavailable"] = "connected"
    media_ref: str = Field(default="", max_length=240)


class StageUpdate(BaseModel):
    stage_state: Literal["backstage", "stage"]
    expected_version: int | None = Field(default=None, ge=1)


class ParticipantControls(BaseModel):
    muted: bool | None = None
    camera_enabled: bool | None = None
    expected_version: int | None = Field(default=None, ge=1)


class HostTransfer(BaseModel):
    target_participant_id: str = Field(min_length=1, max_length=160)


class ParticipantRemoval(BaseModel):
    outcome: Literal["removed", "withdrawn", "forfeited", "disqualified", "technical_failure"] = "removed"
    reason: str = Field(default="", max_length=500)
    prevent_rejoin: bool = False


class RulesetCreate(BaseModel):
    ruleset_key: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1, le=1_000_000)
    name: str = Field(min_length=1, max_length=120)
    explanation: str = Field(default="", max_length=1000)
    config: dict
    activate: bool = False


class BattleCreate(BaseModel):
    ruleset_id: str = Field(min_length=1, max_length=160)
    mode: Literal["1v1", "2v2", "3v3", "4v4", "multi_team", "free_for_all", "host_challengers", "collaborative"]
    participant_ids: list[str] | None = Field(default=None, min_length=2, max_length=8)
    team_count: int | None = Field(default=None, ge=2, le=4)


class BattleStart(BaseModel):
    command_id: str = Field(min_length=1, max_length=160)


class TeamAssign(BaseModel):
    participant_id: str = Field(min_length=1, max_length=160)
    team_id: str = Field(min_length=1, max_length=160)
    expected_version: int | None = Field(default=None, ge=1)


class ParticipantAdjustment(BaseModel):
    participant_id: str = Field(min_length=1, max_length=160)
    score_delta: int = Field(ge=-1_000_000_000, le=1_000_000_000)
    reason: str = Field(min_length=1, max_length=500)


class BattleVoid(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class BattlePlanCreate(BaseModel):
    ruleset_id: str = Field(min_length=1, max_length=160)
    mode: Literal["1v1", "2v2", "3v3", "4v4", "multi_team", "free_for_all", "host_challengers", "collaborative"]
    participant_user_ids: list[str] = Field(min_length=2, max_length=8)
    start_at: str = Field(min_length=10, max_length=80)
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    visibility: Literal["participants", "unlisted", "public"] = "participants"
    title: str = Field(default="", max_length=180)
    team_count: int | None = Field(default=None, ge=2, le=4)
    series_id: str | None = Field(default=None, max_length=160)


class BattlePlanReschedule(BaseModel):
    start_at: str = Field(min_length=10, max_length=80)
    timezone: str = Field(default="UTC", min_length=1, max_length=80)


class BattleChallengeCreate(BaseModel):
    ruleset_id: str = Field(min_length=1, max_length=160)
    mode: Literal["1v1", "2v2", "3v3", "4v4", "multi_team", "free_for_all", "host_challengers", "collaborative"]
    participant_user_ids: list[str] = Field(min_length=2, max_length=8)
    proposed_start_at: str = Field(min_length=10, max_length=80)
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    visibility: Literal["participants", "unlisted", "public"] = "participants"
    title: str = Field(default="", max_length=180)
    team_count: int | None = Field(default=None, ge=2, le=4)
    expires_seconds: int = Field(default=3600, ge=60, le=604800)


class BattleChallengeResponse(BaseModel):
    accept: bool


class RematchCreate(BaseModel):
    proposed_start_at: str | None = Field(default=None, max_length=80)
    expires_seconds: int = Field(default=3600, ge=60, le=604800)


class BattleSeriesCreate(BaseModel):
    ruleset_id: str = Field(min_length=1, max_length=160)
    mode: Literal["1v1", "free_for_all"]
    participant_user_ids: list[str] = Field(min_length=2, max_length=8)
    best_of: Literal[1, 3, 5, 7, 9]
    title: str = Field(default="", max_length=180)


@router.post("/shared-sky/api/broadcasts/{live_session_id}/participants/host")
def establish_host(live_session_id: str, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: {"participant": battle_store.ensure_host(live_session_id, str(member.user_id), correlation_id=cid)})


@router.get("/shared-sky/api/broadcasts/{live_session_id}/participants")
def participants(live_session_id: str, request: Request):
    member, _ = _member(request)
    return _run(request, lambda _cid: {"live_session_id": live_session_id, "participants": battle_store.participant_control_state(live_session_id, str(member.user_id))})


@router.post("/shared-sky/api/broadcasts/{live_session_id}/invitations")
def invite(live_session_id: str, body: InvitationCreate, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: {"invitation": battle_store.create_invitation(live_session_id, str(member.user_id), body.invited_user_id, message=body.message, ttl_seconds=body.ttl_seconds, correlation_id=cid)})


@router.post("/shared-sky/api/invitations/{invitation_id}/respond")
def respond_invite(invitation_id: str, body: InvitationResponse, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: {"participant": battle_store.respond_invitation(invitation_id, str(member.user_id), invite_token=body.invite_token, accept=body.accept, correlation_id=cid)})


@router.post("/shared-sky/api/invitations/{invitation_id}/revoke")
def revoke_invite(invitation_id: str, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: {"invitation": battle_store.revoke_invitation(invitation_id, str(member.user_id), correlation_id=cid)})


@router.post("/shared-sky/api/broadcasts/{live_session_id}/join-requests")
def request_join(live_session_id: str, body: JoinRequestCreate, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: {"request": battle_store.request_to_join(live_session_id, str(member.user_id), message=body.message, ttl_seconds=body.ttl_seconds, correlation_id=cid)})


@router.post("/shared-sky/api/join-requests/{request_id}/decision")
def decide_join_request(request_id: str, body: JoinRequestDecision, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: {"participant": battle_store.respond_join_request(request_id, str(member.user_id), approve=body.approve, correlation_id=cid)})


@router.put("/shared-sky/api/participants/{participant_id}/readiness")
def readiness(participant_id: str, body: ReadinessUpdate, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: {"participant": battle_store.update_readiness(participant_id, str(member.user_id), terms_accepted=body.terms_accepted, camera_ready=body.camera_ready, microphone_ready=body.microphone_ready, audio_available=body.audio_available, video_available=body.video_available, connection_state=body.connection_state, media_ref=body.media_ref, correlation_id=cid)})


@router.put("/shared-sky/api/participants/{participant_id}/stage")
def stage(participant_id: str, body: StageUpdate, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: {"participant": battle_store.set_stage_state(participant_id, str(member.user_id), body.stage_state, expected_version=body.expected_version, correlation_id=cid)})


@router.put("/shared-sky/api/participants/{participant_id}/controls")
def participant_controls(participant_id: str, body: ParticipantControls, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: {"participant": battle_store.set_participant_controls(
        participant_id, str(member.user_id), muted=body.muted, camera_enabled=body.camera_enabled,
        expected_version=body.expected_version, correlation_id=cid
    )})


@router.post("/shared-sky/api/participants/{participant_id}/disconnect")
def disconnect(participant_id: str, request: Request):
    member, _ = _creator_member(request)
    participant = battle_store.get_participant(participant_id)
    if participant["user_id"] != str(member.user_id):
        raise HTTPException(403, detail={"code": "unauthorised", "message": "Participant belongs to another account"})
    return _run(request, lambda cid: {"participant": battle_store.disconnect(participant_id, correlation_id=cid)})


@router.post("/shared-sky/api/participants/{participant_id}/reconnect")
def reconnect(participant_id: str, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: {"participant": battle_store.reconnect(participant_id, str(member.user_id), correlation_id=cid)})


@router.post("/shared-sky/api/broadcasts/{live_session_id}/transfer-host")
def transfer_host(live_session_id: str, body: HostTransfer, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: {"participant": battle_store.transfer_host(live_session_id, str(member.user_id), body.target_participant_id, correlation_id=cid)})


@router.post("/shared-sky/api/participants/{participant_id}/remove")
def remove_participant(participant_id: str, body: ParticipantRemoval, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: {"participant": battle_store.remove_participant(participant_id, str(member.user_id), outcome=body.outcome, reason=body.reason, prevent_rejoin=body.prevent_rejoin, correlation_id=cid)})


@router.post("/owner/shared-sky/api/battle-rulesets")
def owner_create_ruleset(body: RulesetCreate, request: Request):
    _owner(request)
    owner_member = getattr(request.state, "member", None)
    actor = str(getattr(owner_member, "user_id", "owner"))
    return _run(request, lambda _cid: {"ruleset": battle_store.create_ruleset(body.ruleset_key, body.version, body.name, body.config, actor, activate=body.activate, explanation=body.explanation)})


@router.post("/owner/shared-sky/api/battle-rulesets/{ruleset_id}/activate")
def owner_activate_ruleset(ruleset_id: str, request: Request):
    _owner(request)
    return _run(request, lambda _cid: {"ruleset": battle_store.activate_ruleset(ruleset_id)})


@router.post("/shared-sky/api/broadcasts/{live_session_id}/battles")
def create_battle(live_session_id: str, body: BattleCreate, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: battle_store.create_battle(live_session_id, str(member.user_id), body.ruleset_id, mode=body.mode, participant_ids=body.participant_ids, team_count=body.team_count, correlation_id=cid))


@router.post("/shared-sky/api/battle-plans")
def create_battle_plan(body: BattlePlanCreate, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: {"plan": battle_store.schedule_battle(
        str(member.user_id), body.ruleset_id, mode=body.mode, participant_user_ids=body.participant_user_ids,
        start_at=body.start_at, timezone_name=body.timezone, visibility=body.visibility, title=body.title,
        team_count=body.team_count, series_id=body.series_id, correlation_id=cid
    )})


@router.get("/shared-sky/api/battle-plans")
def list_battle_plans(request: Request, limit: int = 100):
    member, _ = _creator_member(request)
    return _run(request, lambda _cid: {"plans": battle_store.list_battle_plans(str(member.user_id), limit=limit)})


@router.put("/shared-sky/api/battle-plans/{plan_id}/schedule")
def reschedule_battle_plan(plan_id: str, body: BattlePlanReschedule, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: {"plan": battle_store.reschedule_battle_plan(
        plan_id, str(member.user_id), start_at=body.start_at, timezone_name=body.timezone, correlation_id=cid
    )})


@router.post("/shared-sky/api/battle-plans/{plan_id}/cancel")
def cancel_battle_plan(plan_id: str, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: {"plan": battle_store.cancel_battle_plan(plan_id, str(member.user_id), correlation_id=cid)})


@router.post("/shared-sky/api/battle-plans/{plan_id}/activate/{live_session_id}")
def activate_battle_plan(plan_id: str, live_session_id: str, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: battle_store.convert_battle_plan(plan_id, live_session_id, str(member.user_id), correlation_id=cid))


@router.post("/shared-sky/api/battle-challenges")
def create_battle_challenge(body: BattleChallengeCreate, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: {"challenge": battle_store.create_challenge(
        str(member.user_id), body.ruleset_id, mode=body.mode, participant_user_ids=body.participant_user_ids,
        proposed_start_at=body.proposed_start_at, timezone_name=body.timezone, visibility=body.visibility,
        title=body.title, team_count=body.team_count, expires_seconds=body.expires_seconds, correlation_id=cid
    )})


@router.post("/shared-sky/api/battle-challenges/{challenge_id}/respond")
def respond_battle_challenge(challenge_id: str, body: BattleChallengeResponse, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: {"challenge": battle_store.respond_challenge(
        challenge_id, str(member.user_id), accept=body.accept, correlation_id=cid
    )})


@router.post("/shared-sky/api/battles/{battle_id}/rematch")
def create_rematch(battle_id: str, body: RematchCreate, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: {"challenge": battle_store.create_rematch_challenge(
        battle_id, str(member.user_id), proposed_start_at=body.proposed_start_at,
        expires_seconds=body.expires_seconds, correlation_id=cid
    )})


@router.post("/shared-sky/api/battle-series")
def create_battle_series(body: BattleSeriesCreate, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: {"series": battle_store.create_series(
        str(member.user_id), body.ruleset_id, mode=body.mode, participant_user_ids=body.participant_user_ids,
        best_of=body.best_of, title=body.title, correlation_id=cid
    )})


@router.post("/shared-sky/api/battle-series/{series_id}/battles/{battle_id}")
def link_battle_series(series_id: str, battle_id: str, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda _cid: {"series": battle_store.link_series_battle(series_id, battle_id, str(member.user_id))})


@router.get("/shared-sky/api/battle-series/{series_id}")
def get_battle_series(series_id: str, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda _cid: {"series": battle_store.series_snapshot(series_id, str(member.user_id))})


@router.get("/shared-sky/api/battles/{battle_id}")
def get_battle(battle_id: str, request: Request):
    member, _ = _member(request)
    return _run(request, lambda _cid: battle_store.control_snapshot(battle_id, str(member.user_id)))


@router.post("/shared-sky/api/battles/{battle_id}/start")
def start_battle(battle_id: str, body: BattleStart, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: battle_store.start_battle(battle_id, str(member.user_id), command_id=body.command_id, correlation_id=cid))


@router.post("/shared-sky/api/battles/{battle_id}/pause")
def pause_battle(battle_id: str, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: battle_store.pause_battle(battle_id, str(member.user_id), correlation_id=cid))


@router.post("/shared-sky/api/battles/{battle_id}/resume")
def resume_battle(battle_id: str, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: battle_store.resume_battle(battle_id, str(member.user_id), correlation_id=cid))


@router.post("/shared-sky/api/battles/{battle_id}/teams")
def assign_team(battle_id: str, body: TeamAssign, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: battle_store.assign_team(battle_id, body.participant_id, body.team_id, str(member.user_id), expected_version=body.expected_version, correlation_id=cid))


@router.post("/shared-sky/api/battles/{battle_id}/rounds/finalise")
def finalise_round(battle_id: str, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: battle_store.finalize_round(battle_id, force=True, actor_user_id=str(member.user_id), correlation_id=cid))


@router.post("/shared-sky/api/battles/{battle_id}/rounds/next")
def next_round(battle_id: str, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: battle_store.start_next_round(battle_id, str(member.user_id), correlation_id=cid))


@router.post("/owner/shared-sky/api/battles/{battle_id}/adjust")
def owner_adjust_score(battle_id: str, body: ParticipantAdjustment, request: Request):
    _owner(request)
    owner_member = getattr(request.state, "member", None)
    actor = str(getattr(owner_member, "user_id", "owner"))
    return _run(request, lambda cid: {"score_event": battle_store.manual_adjustment(battle_id, actor, body.participant_id, body.score_delta, reason=body.reason, correlation_id=cid)})


@router.post("/shared-sky/api/battles/{battle_id}/void")
def void_battle(battle_id: str, body: BattleVoid, request: Request):
    member, _ = _creator_member(request)
    return _run(request, lambda cid: battle_store.void_battle(battle_id, str(member.user_id), reason=body.reason, correlation_id=cid))


@router.get("/owner/shared-sky/api/battles/{battle_id}/reconciliation")
def owner_reconcile(battle_id: str, request: Request):
    _owner(request)
    return _run(request, lambda _cid: battle_store.reconcile(battle_id))


@router.post("/owner/shared-sky/api/battles/{battle_id}/rebuild-scores")
def owner_rebuild_scores(battle_id: str, request: Request):
    _owner(request)
    return _run(request, lambda _cid: battle_store.rebuild_scores(battle_id))


@router.get("/owner/shared-sky/api/battles/{battle_id}/audit")
def owner_battle_audit(battle_id: str, request: Request):
    _owner(request)
    return _run(request, lambda _cid: {"battle_id": battle_id, "events": battle_store.audit_events(battle_id)})


def viewer_battle_events(battle_id: str, *, after_cursor: int = 0, limit: int = 200) -> list[dict]:
    return battle_store.realtime_events(battle_id, after_cursor=after_cursor, limit=limit)


def viewer_battle_snapshot(battle_id: str) -> dict:
    """Read-only safe contract for Chat 4; no public route is opened here.

    Chat 4 remains responsible for visibility/privacy admission before returning this payload
    to a viewer. Backstage participants, fraud evidence, payment data and internal notes are absent.
    """
    return battle_store.viewer_snapshot(battle_id)


__all__ = ["router", "battle_store", "viewer_battle_snapshot", "viewer_battle_events"]
