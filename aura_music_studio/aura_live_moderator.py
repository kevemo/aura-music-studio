from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AURA_LIVE_MODERATOR_HANDLE = "aura.chat.mod"
AURA_LIVE_MODERATOR_PROFILE_URL = "https://www.tiktok.com/@aura.chat.mod"

_HANDLE_RE = re.compile(r"^[A-Za-z0-9._]{2,24}$")


class ModerationMode(str, Enum):
    ADVISORY = "advisory"
    ASSISTED = "assisted"
    AUTO_PROTECT = "auto_protect"


class ModerationAction(str, Enum):
    ALLOW = "allow"
    OBSERVE = "observe"
    WARN = "warn"
    RECOMMEND_MUTE = "recommend_mute"
    RECOMMEND_BLOCK = "recommend_block"
    ESCALATE = "escalate"


class AuraModeratorAuthorization(BaseModel):
    """Creator-controlled authorization state for Aura's TikTok LIVE moderation identity.

    This record never stores TikTok passwords, browser cookies, device tokens, or private API
    credentials. It records only the creator's explicit intent and whether the creator has
    confirmed that @aura.chat.mod was manually/officially assigned as a TikTok LIVE moderator.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    creator_handle: str = Field(min_length=2, max_length=24)
    aura_handle: Literal["aura.chat.mod"] = AURA_LIVE_MODERATOR_HANDLE
    profile_url: Literal["https://www.tiktok.com/@aura.chat.mod"] = AURA_LIVE_MODERATOR_PROFILE_URL
    creator_consent: bool = False
    moderator_assignment_confirmed: bool = False
    mode: ModerationMode = ModerationMode.ADVISORY
    provider_write_enabled: bool = False

    @field_validator("creator_handle")
    @classmethod
    def validate_creator_handle(cls, value: str) -> str:
        value = value.removeprefix("@").strip()
        if not _HANDLE_RE.fullmatch(value):
            raise ValueError("TikTok creator handle is invalid")
        if value.lower() == AURA_LIVE_MODERATOR_HANDLE:
            raise ValueError("Creator handle cannot be the Aura moderator identity")
        return value

    @model_validator(mode="after")
    def enforce_authorization_boundary(self):
        if self.provider_write_enabled:
            if not self.creator_consent:
                raise ValueError("Provider moderation writes require explicit creator consent")
            if not self.moderator_assignment_confirmed:
                raise ValueError("Provider moderation writes require confirmed TikTok moderator assignment")
            if self.mode is ModerationMode.ADVISORY:
                raise ValueError("Advisory mode cannot enable provider moderation writes")
        return self


class ModerationSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: Literal[
        "harassment",
        "hate",
        "sexual",
        "threat",
        "doxxing",
        "scam",
        "spam",
        "impersonation",
        "self_harm_concern",
        "grooming_concern",
        "creator_defined",
        "other",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    severity: int = Field(ge=0, le=4)
    evidence: str = Field(default="", max_length=500)


class ModerationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ModerationAction
    public_response_allowed: bool = False
    provider_write_permitted: bool = False
    requires_human_confirmation: bool = True
    reason: str


@dataclass(frozen=True)
class TikTokLiveConnectorCapabilities:
    """Capabilities granted by an approved TikTok/partner transport.

    The Command Center must never infer these from a logged-in browser session or from the
    existence of the public Aura profile. Provider write operations remain disabled unless an
    approved connector explicitly reports the corresponding moderation capability.
    """

    approved_transport: bool = False
    can_read_live_comments: bool = False
    can_warn: bool = False
    can_mute: bool = False
    can_block: bool = False
    can_post_as_moderator: bool = False


class AuraLiveModerator:
    """Policy boundary for Aura AI-assisted TikTok LIVE moderation.

    AI/classifier layers may produce ModerationSignal objects, but this class is the final
    authorization gate. It keeps creator consent, moderation mode, official-provider capability,
    and severity policy separate so model output cannot directly become a TikTok moderation write.
    """

    _IMMEDIATE_ESCALATION = {"threat", "doxxing", "grooming_concern"}

    def decide(
        self,
        authorization: AuraModeratorAuthorization,
        signal: ModerationSignal,
        capabilities: TikTokLiveConnectorCapabilities,
    ) -> ModerationDecision:
        if not authorization.creator_consent:
            return ModerationDecision(
                action=ModerationAction.OBSERVE,
                provider_write_permitted=False,
                requires_human_confirmation=True,
                reason="Aura LIVE moderation is not authorized by this creator.",
            )

        if signal.category in self._IMMEDIATE_ESCALATION and signal.severity >= 3:
            return ModerationDecision(
                action=ModerationAction.ESCALATE,
                public_response_allowed=False,
                provider_write_permitted=False,
                requires_human_confirmation=True,
                reason="High-severity safety signal requires immediate human escalation and evidence preservation.",
            )

        if signal.confidence < 0.70:
            return ModerationDecision(
                action=ModerationAction.OBSERVE,
                provider_write_permitted=False,
                requires_human_confirmation=True,
                reason="Classifier confidence is below the autonomous moderation threshold.",
            )

        action = self._recommended_action(signal)

        if authorization.mode is ModerationMode.ADVISORY:
            return ModerationDecision(
                action=action,
                public_response_allowed=False,
                provider_write_permitted=False,
                requires_human_confirmation=True,
                reason="Advisory mode provides recommendations only.",
            )

        approved_write_path = (
            authorization.provider_write_enabled
            and authorization.moderator_assignment_confirmed
            and capabilities.approved_transport
        )
        if not approved_write_path:
            return ModerationDecision(
                action=action,
                public_response_allowed=False,
                provider_write_permitted=False,
                requires_human_confirmation=True,
                reason="TikTok moderation write access is unavailable or not approved; recommendation only.",
            )

        permitted = self._capability_allows(action, capabilities)
        auto_safe = authorization.mode is ModerationMode.AUTO_PROTECT and signal.severity <= 2

        return ModerationDecision(
            action=action,
            public_response_allowed=(
                action is ModerationAction.WARN and capabilities.can_post_as_moderator
            ),
            provider_write_permitted=permitted,
            requires_human_confirmation=not (permitted and auto_safe),
            reason=(
                "Approved TikTok moderation transport is available; bounded action is permitted by connector capability."
                if permitted
                else "The approved connector does not expose the required moderation capability."
            ),
        )

    @staticmethod
    def _recommended_action(signal: ModerationSignal) -> ModerationAction:
        if signal.severity <= 0:
            return ModerationAction.ALLOW
        if signal.severity == 1:
            return ModerationAction.WARN
        if signal.severity == 2:
            return ModerationAction.RECOMMEND_MUTE
        return ModerationAction.RECOMMEND_BLOCK

    @staticmethod
    def _capability_allows(
        action: ModerationAction,
        capabilities: TikTokLiveConnectorCapabilities,
    ) -> bool:
        if action in {ModerationAction.ALLOW, ModerationAction.OBSERVE, ModerationAction.ESCALATE}:
            return False
        if action is ModerationAction.WARN:
            return capabilities.can_warn
        if action is ModerationAction.RECOMMEND_MUTE:
            return capabilities.can_mute
        if action is ModerationAction.RECOMMEND_BLOCK:
            return capabilities.can_block
        return False


__all__ = [
    "AURA_LIVE_MODERATOR_HANDLE",
    "AURA_LIVE_MODERATOR_PROFILE_URL",
    "AuraLiveModerator",
    "AuraModeratorAuthorization",
    "ModerationAction",
    "ModerationDecision",
    "ModerationMode",
    "ModerationSignal",
    "TikTokLiveConnectorCapabilities",
]
