from __future__ import annotations

import json
from enum import Enum
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, model_validator

from .rhiannon_voice_contracts import public_voice_contract

RHIANNON_TURN_PROTOCOL = "RhiannonTurn.state/v1"
RHIANNON_MODEL_PROTOCOL = "RhiannonModel.metadata/v1"

router = APIRouter(tags=["Rhiannon Companion"])

_ALLOWED_EXPRESSIONS = frozenset(public_voice_contract({})["allowed_expressions"])


class RhiannonTurnState(str, Enum):
    IDLE = "idle"
    READY = "ready"
    LISTENING = "listening"
    PROCESSING = "processing"
    THINKING = "thinking"
    RESPONDING = "responding"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    AWAITING_PERMISSION = "awaiting_permission"
    DEGRADED = "degraded"
    ERROR = "error"
    RECOVERY = "recovery"


TURN_STATE_TO_AVATAR_STATE: dict[RhiannonTurnState, str] = {
    RhiannonTurnState.IDLE: "idle",
    RhiannonTurnState.READY: "welcoming",
    RhiannonTurnState.LISTENING: "listening",
    RhiannonTurnState.PROCESSING: "thinking",
    RhiannonTurnState.THINKING: "thinking",
    RhiannonTurnState.RESPONDING: "thinking",
    RhiannonTurnState.SPEAKING: "speaking",
    RhiannonTurnState.INTERRUPTED: "warning",
    RhiannonTurnState.AWAITING_PERMISSION: "warning",
    RhiannonTurnState.DEGRADED: "warning",
    RhiannonTurnState.ERROR: "warning",
    RhiannonTurnState.RECOVERY: "thinking",
}

_ALLOWED_TRANSITIONS: dict[RhiannonTurnState, frozenset[RhiannonTurnState]] = {
    RhiannonTurnState.IDLE: frozenset({RhiannonTurnState.READY,RhiannonTurnState.LISTENING,RhiannonTurnState.AWAITING_PERMISSION,RhiannonTurnState.DEGRADED,RhiannonTurnState.ERROR}),
    RhiannonTurnState.READY: frozenset({RhiannonTurnState.IDLE,RhiannonTurnState.LISTENING,RhiannonTurnState.PROCESSING,RhiannonTurnState.THINKING,RhiannonTurnState.RESPONDING,RhiannonTurnState.SPEAKING,RhiannonTurnState.AWAITING_PERMISSION,RhiannonTurnState.DEGRADED,RhiannonTurnState.ERROR}),
    RhiannonTurnState.LISTENING: frozenset({RhiannonTurnState.IDLE,RhiannonTurnState.READY,RhiannonTurnState.PROCESSING,RhiannonTurnState.THINKING,RhiannonTurnState.RESPONDING,RhiannonTurnState.SPEAKING,RhiannonTurnState.INTERRUPTED,RhiannonTurnState.AWAITING_PERMISSION,RhiannonTurnState.DEGRADED,RhiannonTurnState.ERROR}),
    RhiannonTurnState.PROCESSING: frozenset({RhiannonTurnState.IDLE,RhiannonTurnState.THINKING,RhiannonTurnState.RESPONDING,RhiannonTurnState.SPEAKING,RhiannonTurnState.INTERRUPTED,RhiannonTurnState.AWAITING_PERMISSION,RhiannonTurnState.DEGRADED,RhiannonTurnState.ERROR}),
    RhiannonTurnState.THINKING: frozenset({RhiannonTurnState.IDLE,RhiannonTurnState.RESPONDING,RhiannonTurnState.SPEAKING,RhiannonTurnState.INTERRUPTED,RhiannonTurnState.AWAITING_PERMISSION,RhiannonTurnState.DEGRADED,RhiannonTurnState.ERROR}),
    RhiannonTurnState.RESPONDING: frozenset({RhiannonTurnState.IDLE,RhiannonTurnState.READY,RhiannonTurnState.LISTENING,RhiannonTurnState.SPEAKING,RhiannonTurnState.INTERRUPTED,RhiannonTurnState.DEGRADED,RhiannonTurnState.ERROR}),
    RhiannonTurnState.SPEAKING: frozenset({RhiannonTurnState.IDLE,RhiannonTurnState.READY,RhiannonTurnState.LISTENING,RhiannonTurnState.INTERRUPTED,RhiannonTurnState.DEGRADED,RhiannonTurnState.ERROR}),
    RhiannonTurnState.INTERRUPTED: frozenset({RhiannonTurnState.IDLE,RhiannonTurnState.READY,RhiannonTurnState.LISTENING,RhiannonTurnState.RECOVERY,RhiannonTurnState.ERROR}),
    RhiannonTurnState.AWAITING_PERMISSION: frozenset({RhiannonTurnState.IDLE,RhiannonTurnState.READY,RhiannonTurnState.LISTENING,RhiannonTurnState.PROCESSING,RhiannonTurnState.RECOVERY,RhiannonTurnState.ERROR}),
    RhiannonTurnState.DEGRADED: frozenset({RhiannonTurnState.IDLE,RhiannonTurnState.READY,RhiannonTurnState.RECOVERY,RhiannonTurnState.ERROR}),
    RhiannonTurnState.ERROR: frozenset({RhiannonTurnState.IDLE,RhiannonTurnState.RECOVERY}),
    RhiannonTurnState.RECOVERY: frozenset({RhiannonTurnState.IDLE,RhiannonTurnState.READY,RhiannonTurnState.LISTENING,RhiannonTurnState.DEGRADED,RhiannonTurnState.ERROR}),
}


class RhiannonTurnSnapshot(BaseModel):
    protocol: str = RHIANNON_TURN_PROTOCOL
    state: RhiannonTurnState = RhiannonTurnState.IDLE
    revision: int = Field(default=0, ge=0)
    active_speech_job_id: str | None = Field(default=None, max_length=160)
    expression: str = Field(default="neutral", max_length=32)
    reason: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def validate_expression(self):
        if self.expression not in _ALLOWED_EXPRESSIONS:
            raise ValueError(f"unsupported Rhiannon expression: {self.expression}")
        return self

    @property
    def avatar_state(self) -> str:
        return TURN_STATE_TO_AVATAR_STATE[self.state]


class RhiannonTurnStateMachine:
    """Bounded, provider-neutral turn lifecycle with stale speech-job rejection."""

    def __init__(self) -> None:
        self._snapshot = RhiannonTurnSnapshot()

    @property
    def snapshot(self) -> RhiannonTurnSnapshot:
        return self._snapshot.model_copy(deep=True)

    def transition(self, state: RhiannonTurnState | str, *, reason: str = "", expression: str | None = None) -> RhiannonTurnSnapshot:
        target = RhiannonTurnState(state)
        current = self._snapshot.state
        if target != current and target not in _ALLOWED_TRANSITIONS[current]:
            raise ValueError(f"invalid Rhiannon turn transition: {current.value} -> {target.value}")
        next_expression = expression or self._snapshot.expression
        if next_expression not in _ALLOWED_EXPRESSIONS:
            raise ValueError(f"unsupported Rhiannon expression: {next_expression}")
        self._snapshot = self._snapshot.model_copy(update={"state": target,"revision": self._snapshot.revision + 1,"expression": next_expression,"reason": reason[:240]})
        return self.snapshot

    def begin_speech(self, job_id: str, *, reason: str = "speech_started", expression: str | None = None) -> RhiannonTurnSnapshot:
        clean_job_id = str(job_id or "").strip()
        if not clean_job_id or len(clean_job_id) > 160:
            raise ValueError("speech job id must contain 1-160 characters")
        if self._snapshot.active_speech_job_id and self._snapshot.active_speech_job_id != clean_job_id:
            self.interrupt(reason="speech_job_superseded", next_state=RhiannonTurnState.READY)
        self.transition(RhiannonTurnState.SPEAKING, reason=reason, expression=expression)
        self._snapshot = self._snapshot.model_copy(update={"active_speech_job_id": clean_job_id})
        return self.snapshot

    def accepts_speech_job(self, job_id: str | None) -> bool:
        clean_job_id = str(job_id or "").strip()
        return bool(clean_job_id and self._snapshot.active_speech_job_id and clean_job_id == self._snapshot.active_speech_job_id)

    def finish_speech(self, job_id: str, *, next_state: RhiannonTurnState | str = RhiannonTurnState.READY) -> RhiannonTurnSnapshot:
        if not self.accepts_speech_job(job_id):
            raise ValueError("stale or unknown Rhiannon speech job")
        self._snapshot = self._snapshot.model_copy(update={"active_speech_job_id": None})
        return self.transition(next_state, reason="speech_finished")

    def interrupt(self, *, reason: str = "user_interruption", next_state: RhiannonTurnState | str | None = None) -> RhiannonTurnSnapshot:
        self._snapshot = self._snapshot.model_copy(update={"active_speech_job_id": None})
        interrupted = self.transition(RhiannonTurnState.INTERRUPTED, reason=reason)
        if next_state is None:
            return interrupted
        return self.transition(next_state, reason=f"{reason}:resume")

    def fail(self, reason: str) -> RhiannonTurnSnapshot:
        self._snapshot = self._snapshot.model_copy(update={"active_speech_job_id": None})
        return self.transition(RhiannonTurnState.ERROR, reason=reason)

    def recover(self, *, next_state: RhiannonTurnState | str = RhiannonTurnState.READY) -> RhiannonTurnSnapshot:
        self.transition(RhiannonTurnState.RECOVERY, reason="recovery_started")
        return self.transition(next_state, reason="recovery_completed")


class RhiannonModelMetadata(BaseModel):
    protocol: str = RHIANNON_MODEL_PROTOCOL
    model_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=240)
    source_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    production_status: str = Field(default="reference_unrigged", max_length=64)
    rig_version: str | None = Field(default=None, max_length=64)
    facial_version: str | None = Field(default=None, max_length=64)
    viseme_version: str | None = Field(default=None, max_length=64)
    texture_material_version: str | None = Field(default=None, max_length=64)
    compatibility_version: str = Field(default="AuraHost.performance/v1", max_length=80)
    vertices: int | None = Field(default=None, ge=0)
    triangles: int | None = Field(default=None, ge=0)
    has_skeleton: bool = False
    has_skin: bool = False
    has_animations: bool = False
    has_morph_targets: bool = False
    provenance: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_production_claim(self):
        if self.production_status == "production":
            missing = [name for name, available in {"skeleton": self.has_skeleton,"skin": self.has_skin,"animations": self.has_animations,"morph_targets": self.has_morph_targets}.items() if not available]
            if missing:
                raise ValueError("production Rhiannon model requires " + ", ".join(missing))
            if not self.rig_version or not self.facial_version or not self.viseme_version:
                raise ValueError("production Rhiannon model requires rig, facial and viseme versions")
        return self

    @property
    def production_ready(self) -> bool:
        return bool(self.production_status == "production" and self.has_skeleton and self.has_skin and self.has_animations and self.has_morph_targets and self.rig_version and self.facial_version and self.viseme_version)


def legacy_base_reference_metadata() -> RhiannonModelMetadata:
    return RhiannonModelMetadata(
        model_id="rhiannon.legacy-aura-base.reference",
        version="2026-09-06",
        source="Rhiannon_Legacy_Aura_Base_Mesh_REFERENCE.glb",
        source_sha256="0707d8ab2feb9a9dd09ebf99334d69b334a9acdda6834f62e235213b32588362",
        production_status="reference_unrigged",
        vertices=10023,
        triangles=9997,
        has_skeleton=False,
        has_skin=False,
        has_animations=False,
        has_morph_targets=False,
        texture_material_version="legacy-reference-2048-jpeg",
        provenance=("Legacy Aura visual base recovered by the 2026-09-06 ZIP deep scan; reference only. Production Rhiannon requires a new rig, skin, facial controls, canonical visemes and validated animations."),
    )


def public_turn_contract() -> dict[str, Any]:
    legacy_model = legacy_base_reference_metadata()
    return {
        "protocol": RHIANNON_TURN_PROTOCOL,
        "states": [state.value for state in RhiannonTurnState],
        "transitions": {state.value: sorted(target.value for target in targets) for state, targets in _ALLOWED_TRANSITIONS.items()},
        "avatar_state_mapping": {state.value: avatar_state for state, avatar_state in TURN_STATE_TO_AVATAR_STATE.items()},
        "allowed_expressions": sorted(_ALLOWED_EXPRESSIONS),
        "speech_job_contract": {"job_identity_required_for_timed_frames": True,"stale_job_rejection": True,"interruption_invalidates_active_job": True},
        "legacy_model_reference": {**legacy_model.model_dump(mode="json"),"production_ready": legacy_model.production_ready},
        "boundaries": {"provider_registry_owned_here": False,"voice_generation_owned_here": False,"voice_cloning_owned_here": False,"uses_existing_avatar_performance_bus": True,"arbitrary_animation_code_allowed": False,"legacy_static_model_counts_as_production": False},
    }


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


@router.get("/aura-intelligence/api/rhiannon/companion-contract", include_in_schema=False)
def companion_contract(request: Request):
    _member(request)
    return JSONResponse(public_turn_contract(), headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


def _turn_script() -> str:
    contract = public_turn_contract()
    transitions = json.dumps(contract["transitions"], separators=(",", ":"))
    state_mapping = json.dumps(contract["avatar_state_mapping"], separators=(",", ":"))
    expressions = json.dumps(contract["allowed_expressions"], separators=(",", ":"))
    return f"""
(()=>{{
  const protocol={json.dumps(RHIANNON_TURN_PROTOCOL)};
  const transitions={transitions};
  const avatarState={state_mapping};
  const allowedExpressions=new Set({expressions});
  let state='idle',revision=0,activeSpeechJobId=null,expression='neutral';

  function emit(type,detail={{}}){{
    try{{document.dispatchEvent(new CustomEvent('rhiannon:turn-state',{{detail:{{protocol,type,state,revision,active_speech_job_id:activeSpeechJobId,expression,...detail}}}}))}}catch(_){{}}
  }}
  function cleanReason(value){{return String(value||'').slice(0,240)}}
  function applyAvatar(detail={{}}){{try{{window.AuraHost?.setState(avatarState[state]||'idle',{{...detail,rhiannon_turn_state:state,rhiannon_turn_revision:revision,protocol}})}}catch(_){{}}}}
  function transition(next,detail={{}}){{
    next=String(next||'');
    if(!Object.prototype.hasOwnProperty.call(transitions,next)){{emit('rejected_state',{{requested:next}});return false}}
    if(next!==state&&!(transitions[state]||[]).includes(next)){{emit('rejected_transition',{{from:state,to:next}});return false}}
    const requestedExpression=detail.expression==null?expression:String(detail.expression);
    if(!allowedExpressions.has(requestedExpression)){{emit('rejected_expression',{{requested:requestedExpression}});return false}}
    state=next;expression=requestedExpression;revision+=1;applyAvatar(detail);emit('transition',{{reason:cleanReason(detail.reason),detail}});return true
  }}
  function beginSpeech(jobId,detail={{}}){{
    jobId=String(jobId||'').trim().slice(0,160);
    if(!jobId){{emit('rejected_speech_job',{{reason:'missing_job_id'}});return false}}
    if(activeSpeechJobId&&activeSpeechJobId!==jobId)interrupt('speech_job_superseded','ready');
    if(!transition('speaking',{{...detail,reason:detail.reason||'speech_started',job_id:jobId}}))return false;
    activeSpeechJobId=jobId;
    emit('speech_started',{{job_id:jobId}});return true
  }}
  function acceptsSpeechJob(jobId){{return !!activeSpeechJobId&&String(jobId||'')===activeSpeechJobId}}
  function speechFrame(detail={{}}){{
    const jobId=String(detail.job_id||'');
    if(!acceptsSpeechJob(jobId)){{emit('stale_speech_frame',{{job_id:jobId}});return false}}
    try{{window.AuraHost?.performance?.speechFrame({{...detail,job_id:jobId}})}}catch(_){{}}
    if(detail.speaking!==false&&state!=='speaking')transition('speaking',{{reason:'speech_frame',job_id:jobId}});
    return true
  }}
  function finishSpeech(jobId,next='ready',detail={{}}){{
    jobId=String(jobId||'');
    if(!acceptsSpeechJob(jobId)){{emit('stale_speech_finish',{{job_id:jobId}});return false}}
    try{{window.AuraHost?.performance?.speechFrame({{speaking:false,viseme:'sil',source:'rhiannon_turn',job_id:jobId}})}}catch(_){{}}
    activeSpeechJobId=null;
    const changed=transition(next,{{...detail,reason:detail.reason||'speech_finished',job_id:jobId}});
    emit('speech_finished',{{job_id:jobId,next}});return changed
  }}
  function interrupt(reason='user_interruption',next='listening',detail={{}}){{
    const interruptedJobId=activeSpeechJobId;activeSpeechJobId=null;
    try{{window.AuraHost?.performance?.speechFrame({{speaking:false,viseme:'sil',source:'rhiannon_turn_interrupt',job_id:interruptedJobId}})}}catch(_){{}}
    if(state!=='interrupted'){{if(!transition('interrupted',{{...detail,reason,interrupted_job_id:interruptedJobId}}))emit('interrupt_without_transition',{{reason,interrupted_job_id:interruptedJobId}})}}
    emit('speech_interrupted',{{reason,interrupted_job_id:interruptedJobId}});
    if(next)transition(next,{{...detail,reason:`${{reason}}:resume`}});
    return interruptedJobId
  }}
  function snapshot(){{return {{protocol,state,revision,active_speech_job_id:activeSpeechJobId,expression,avatar_state:avatarState[state]||'idle'}}}}
  window.RhiannonTurnHost=Object.freeze({{protocol,transition,beginSpeech,acceptsSpeechJob,speechFrame,finishSpeech,interrupt,snapshot}});
  emit('ready',{{}});
}})();
""".strip()


RHIANNON_TURN_SCRIPT = _turn_script()


@router.get("/aura-intelligence/rhiannon-turn-state.js", include_in_schema=False)
def turn_state_script():
    return Response(content=RHIANNON_TURN_SCRIPT, media_type="application/javascript", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


__all__ = ["router","RHIANNON_TURN_PROTOCOL","RHIANNON_MODEL_PROTOCOL","RhiannonTurnState","RhiannonTurnSnapshot","RhiannonTurnStateMachine","RhiannonModelMetadata","TURN_STATE_TO_AVATAR_STATE","legacy_base_reference_metadata","public_turn_contract","RHIANNON_TURN_SCRIPT"]
