from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Mapping
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from .aura_avatar_validator import VISEME_ANIMATION_ALIASES

VOICE_CAPABILITY_PROTOCOL = "RhiannonVoice.capabilities/v1"
SPEECH_TIMING_PROTOCOL = "RhiannonVoice.timing/v1"
VOICE_ASSET_PROVENANCE_PROTOCOL = "RhiannonVoice.asset-provenance/v1"

_MAX_SPEECH_DURATION_MS = 60 * 60 * 1000
_ALLOWED_EXPRESSIONS = {
    "neutral",
    "warm",
    "happy",
    "thoughtful",
    "concerned",
    "excited",
    "calm",
    "focused",
    "listening",
    "speaking",
}


class VoiceCapability(str, Enum):
    SPEECH_SYNTHESIS = "speech_synthesis"
    CLONED_SPEECH = "cloned_speech"
    SINGING_VOICE = "singing_voice"
    VOICE_CONVERSION = "voice_conversion"
    PHONEME_TIMING = "phoneme_timing"
    VISEME_TIMING = "viseme_timing"
    STREAMING_SPEECH = "streaming_speech"


class CapabilityState(str, Enum):
    AVAILABLE = "available"
    CONFIGURED_UNVERIFIED = "configured_unverified"
    DEGRADED = "degraded"
    LOADING = "loading"
    UNHEALTHY = "unhealthy"
    UNAVAILABLE = "unavailable"


_VERIFIED_RUNTIME_STATES = frozenset(
    {
        CapabilityState.AVAILABLE,
        CapabilityState.DEGRADED,
        CapabilityState.LOADING,
        CapabilityState.UNHEALTHY,
        CapabilityState.UNAVAILABLE,
    }
)


class VoiceProfileKind(str, Enum):
    RHIANNON_SYSTEM = "rhiannon_system"
    USER = "user"
    THIRD_PARTY_AUTHORISED = "third_party_authorised"


class ConsentState(str, Enum):
    SYSTEM_APPROVED = "system_approved"
    EXPLICIT_CONSENT = "explicit_consent"
    VERIFIED_AUTHORISATION = "verified_authorisation"


class TimingKind(str, Enum):
    WORD = "word"
    PHONEME = "phoneme"
    VISEME = "viseme"
    SYLLABLE = "syllable"
    PHRASE = "phrase"


class VoiceCapabilityDescriptor(BaseModel):
    capability: VoiceCapability
    state: CapabilityState
    local_runtime_configured: bool = False
    remote_provider_configured: bool = False
    streaming: bool = False
    self_hosted: bool = False
    consent_required: bool = False
    premium_entitlement_required: bool = False
    health_evidence_verified: bool = False
    health_evidence_source: str = Field(default="", max_length=120)
    detail: str = Field(default="", max_length=240)


class RhiannonVoiceIdentity(BaseModel):
    schema_version: int = 1
    identity: Literal["rhiannon"] = "rhiannon"
    identity_version: str = Field(default="1.0", max_length=32)
    approved_voice_profile_ref: str = Field(default="rhiannon.system.voice.v1", min_length=1, max_length=120)
    speaking_style: str = Field(default="warm, clear, calm and purposeful", max_length=240)
    pronunciation_rules: dict[str, str] = Field(default_factory=dict, max_length=100)
    pacing: str = Field(default="natural", max_length=80)
    emotional_expression: list[str] = Field(
        default_factory=lambda: sorted(_ALLOWED_EXPRESSIONS),
        max_length=32,
    )
    tone: str = Field(default="warm and confident", max_length=120)
    prosody: str = Field(default="natural conversational prosody", max_length=160)
    supported_languages: list[str] = Field(default_factory=lambda: ["en"], max_length=32)
    fallback_voice_ref: str = Field(default="system.default", max_length=120)
    provenance: str = Field(default="Chat 1 Rhiannon system identity", max_length=240)
    safe_configuration: bool = True


class SpeechTimingSpan(BaseModel):
    kind: TimingKind
    value: str = Field(min_length=1, max_length=120)
    start_ms: int = Field(ge=0, le=_MAX_SPEECH_DURATION_MS)
    end_ms: int = Field(ge=0, le=_MAX_SPEECH_DURATION_MS)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    expression_hint: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def validate_span(self):
        if self.end_ms < self.start_ms:
            raise ValueError("timing span end_ms must be greater than or equal to start_ms")
        if self.kind == TimingKind.VISEME and self.value not in VISEME_ANIMATION_ALIASES:
            raise ValueError(f"unknown canonical viseme: {self.value}")
        if self.expression_hint is not None and self.expression_hint not in _ALLOWED_EXPRESSIONS:
            raise ValueError(f"unsupported expression hint: {self.expression_hint}")
        return self


class SpeechTimingTrack(BaseModel):
    protocol: Literal["RhiannonVoice.timing/v1"] = SPEECH_TIMING_PROTOCOL
    audio_duration_ms: int = Field(gt=0, le=_MAX_SPEECH_DURATION_MS)
    spans: list[SpeechTimingSpan] = Field(default_factory=list, max_length=20000)
    precise_timing: bool = False
    source: Literal["runtime", "provider", "derived", "fallback"] = "runtime"

    @model_validator(mode="after")
    def validate_timing(self):
        last_start_by_kind: dict[TimingKind, int] = {}
        for span in self.spans:
            if span.end_ms > self.audio_duration_ms:
                raise ValueError("timing span exceeds audio duration")
            previous = last_start_by_kind.get(span.kind)
            if previous is not None and span.start_ms < previous:
                raise ValueError(f"{span.kind.value} timing spans must be ordered by start_ms")
            last_start_by_kind[span.kind] = span.start_ms
        if self.precise_timing and not any(span.kind == TimingKind.VISEME for span in self.spans):
            raise ValueError("precise_timing requires canonical viseme timing spans")
        return self


class RhiannonSpeechRequest(BaseModel):
    protocol: Literal["RhiannonVoice.capabilities/v1"] = VOICE_CAPABILITY_PROTOCOL
    job_id: str = Field(default_factory=lambda: f"speech_{uuid4().hex}", min_length=1, max_length=96)
    text: str = Field(min_length=1, max_length=4000)
    voice_profile_ref: str = Field(default="rhiannon.system.voice.v1", min_length=1, max_length=120)
    language: str = Field(default="en", min_length=2, max_length=32)
    expression: str = Field(default="warm", max_length=32)
    prosody_hint: str = Field(default="", max_length=160)
    pronunciation_overrides: dict[str, str] = Field(default_factory=dict, max_length=100)
    streaming_requested: bool = False
    volume: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_expression(self):
        if self.expression not in _ALLOWED_EXPRESSIONS:
            raise ValueError(f"unsupported Rhiannon expression: {self.expression}")
        return self


class VoiceAssetProvenance(BaseModel):
    protocol: Literal["RhiannonVoice.asset-provenance/v1"] = VOICE_ASSET_PROVENANCE_PROTOCOL
    asset_id: str = Field(min_length=1, max_length=160)
    generation_type: Literal["speech_synthesis", "cloned_speech", "singing_voice", "voice_conversion"]
    voice_profile_ref: str = Field(min_length=1, max_length=120)
    voice_profile_kind: VoiceProfileKind
    consent_state: ConsentState
    provider_runtime_class: Literal["local", "remote", "hybrid", "unknown"]
    generation_timestamp: str = Field(min_length=1, max_length=64)
    source_project_id: str = Field(min_length=1, max_length=160)
    revision: str = Field(min_length=1, max_length=80)
    private: bool = True
    synthetic_generated: bool = True
    entitlement_ref: str | None = Field(default=None, max_length=160)


def _descriptor(
    capability: VoiceCapability,
    *,
    state: CapabilityState,
    local: bool = False,
    remote: bool = False,
    streaming: bool = False,
    self_hosted: bool = False,
    consent_required: bool = False,
    premium: bool = False,
    health_verified: bool = False,
    health_source: str = "",
    detail: str,
) -> VoiceCapabilityDescriptor:
    return VoiceCapabilityDescriptor(
        capability=capability,
        state=state,
        local_runtime_configured=local,
        remote_provider_configured=remote,
        streaming=streaming,
        self_hosted=self_hosted,
        consent_required=consent_required,
        premium_entitlement_required=premium,
        health_evidence_verified=health_verified,
        health_evidence_source=health_source,
        detail=detail,
    )


def _verified_synthesis_health(
    diagnostics: Mapping[str, Any], *, configured: bool
) -> tuple[CapabilityState, bool, str]:
    """Consume only explicit server-side health evidence from the owning runtime.

    Configuration is never promoted to readiness. A caller must supply the internal
    ``tts_health_verified`` flag, a bounded state and a non-empty evidence source. Even
    verified ``available`` evidence fails closed when no TTS runtime/provider is configured.
    Unknown/malformed evidence is ignored rather than becoming capability authority.
    """

    if not bool(diagnostics.get("tts_health_verified")):
        return (
            CapabilityState.CONFIGURED_UNVERIFIED if configured else CapabilityState.UNAVAILABLE,
            False,
            "",
        )

    source = str(diagnostics.get("tts_health_source") or "").strip()[:120]
    raw_state = str(diagnostics.get("tts_health_state") or "").strip().lower()
    if not source:
        return (
            CapabilityState.CONFIGURED_UNVERIFIED if configured else CapabilityState.UNAVAILABLE,
            False,
            "",
        )
    try:
        state = CapabilityState(raw_state)
    except ValueError:
        return (
            CapabilityState.CONFIGURED_UNVERIFIED if configured else CapabilityState.UNAVAILABLE,
            False,
            "",
        )
    if state not in _VERIFIED_RUNTIME_STATES:
        return (
            CapabilityState.CONFIGURED_UNVERIFIED if configured else CapabilityState.UNAVAILABLE,
            False,
            "",
        )
    if not configured and state != CapabilityState.UNAVAILABLE:
        return CapabilityState.UNAVAILABLE, True, source
    return state, True, source


def voice_capability_snapshot(diagnostics: Mapping[str, Any] | None = None) -> list[VoiceCapabilityDescriptor]:
    """Return Chat 1's truthful provider-neutral voice capability projection.

    Configuration alone remains CONFIGURED_UNVERIFIED. Runtime health is accepted only as
    explicit server-side evidence from the owning voice/runtime layer; client state is never
    consulted and malformed evidence fails closed.
    """

    diagnostics = diagnostics or {}
    local_tts = bool(
        diagnostics.get("tts_command_configured")
        or diagnostics.get("piper_model_configured")
    )
    remote_tts = bool(diagnostics.get("tts_url_configured"))
    synthesis_state, health_verified, health_source = _verified_synthesis_health(
        diagnostics, configured=bool(local_tts or remote_tts)
    )

    if health_verified:
        detail = f"Verified speech runtime health reports {synthesis_state.value}."
    elif synthesis_state == CapabilityState.CONFIGURED_UNVERIFIED:
        detail = "Speech runtime configuration exists but health has not been proven."
    else:
        detail = "No speech synthesis runtime is configured for this Chat 1 integration."

    return [
        _descriptor(
            VoiceCapability.SPEECH_SYNTHESIS,
            state=synthesis_state,
            local=local_tts,
            remote=remote_tts,
            self_hosted=local_tts,
            health_verified=health_verified,
            health_source=health_source,
            detail=detail,
        ),
        _descriptor(
            VoiceCapability.CLONED_SPEECH,
            state=CapabilityState.UNAVAILABLE,
            consent_required=True,
            premium=True,
            detail="Owned by Chat 2; no canonical Chat 1 backend capability evidence is attached.",
        ),
        _descriptor(
            VoiceCapability.SINGING_VOICE,
            state=CapabilityState.UNAVAILABLE,
            consent_required=True,
            premium=True,
            detail="Owned by Chat 2; no canonical Chat 1 backend capability evidence is attached.",
        ),
        _descriptor(
            VoiceCapability.VOICE_CONVERSION,
            state=CapabilityState.UNAVAILABLE,
            consent_required=True,
            premium=True,
            detail="Owned by Chat 2; no canonical Chat 1 backend capability evidence is attached.",
        ),
        _descriptor(
            VoiceCapability.PHONEME_TIMING,
            state=CapabilityState.UNAVAILABLE,
            detail="Current speech handoff does not return canonical phoneme timing metadata.",
        ),
        _descriptor(
            VoiceCapability.VISEME_TIMING,
            state=CapabilityState.UNAVAILABLE,
            detail="Current speech handoff does not return canonical viseme timing metadata.",
        ),
        _descriptor(
            VoiceCapability.STREAMING_SPEECH,
            state=CapabilityState.UNAVAILABLE,
            detail="Current speech endpoint returns completed audio rather than a streaming contract.",
        ),
    ]


def public_voice_contract(diagnostics: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "capability_protocol": VOICE_CAPABILITY_PROTOCOL,
        "timing_protocol": SPEECH_TIMING_PROTOCOL,
        "asset_provenance_protocol": VOICE_ASSET_PROVENANCE_PROTOCOL,
        "identity": RhiannonVoiceIdentity().model_dump(mode="json"),
        "capabilities": [
            item.model_dump(mode="json")
            for item in voice_capability_snapshot(diagnostics)
        ],
        "canonical_visemes": list(VISEME_ANIMATION_ALIASES),
        "allowed_expressions": sorted(_ALLOWED_EXPRESSIONS),
        "boundaries": {
            "voice_cloning_engine_owned_here": False,
            "voice_profile_creation_owned_here": False,
            "raw_model_embeddings_exposed": False,
            "provider_secrets_exposed": False,
            "client_entitlement_authority": False,
            "microphone_audio_auto_enrolled_for_cloning": False,
            "runtime_health_requires_server_evidence": True,
            "configuration_implies_ready": False,
        },
    }
