from __future__ import annotations

import os
from dataclasses import asdict, dataclass

from fastapi import APIRouter, HTTPException, Request

from .engine_manager import EngineManager

router = APIRouter(tags=["Aura Self-Host Control"])


@dataclass(frozen=True)
class CapabilityPolicy:
    capability: str
    preferred_mode: str
    local_engines: tuple[str, ...] = ()
    external_reason: str | None = None
    external_boundary: str | None = None


CAPABILITY_POLICIES: tuple[CapabilityPolicy, ...] = (
    CapabilityPolicy("music_generation", "self_host", ("ace-step-1.5", "yue", "diffrhythm", "audiocraft", "stable-audio-tools")),
    CapabilityPolicy("singing_synthesis", "self_host", ("diffsinger",)),
    CapabilityPolicy("voice_conversion", "self_host", ("seed-vc", "rvc")),
    CapabilityPolicy("speech_to_text", "self_host", ("whisper-cpp",)),
    CapabilityPolicy("text_to_speech", "self_host", ("piper-tts",)),
    CapabilityPolicy("source_separation", "self_host", ("audio-separator", "demucs")),
    CapabilityPolicy("mastering", "self_host", ("matchering", "phaselimiter")),
    CapabilityPolicy("mixing_dsp", "self_host", ("pedalboard",)),
    CapabilityPolicy("audio_to_midi", "self_host", ("basic-pitch",)),
    CapabilityPolicy("neural_amp", "self_host", ("neural-amp-modeler",)),
    CapabilityPolicy("spatial_audio", "self_host", ("spatial-audio-framework",)),
    CapabilityPolicy("project_storage", "self_host"),
    CapabilityPolicy("member_database", "self_host"),
    CapabilityPolicy("overlay_rendering", "self_host"),
    CapabilityPolicy("live_automation", "self_host"),
    CapabilityPolicy("moderation_decision_support", "self_host"),
    CapabilityPolicy("support_knowledge_base", "self_host"),
    CapabilityPolicy(
        "tiktok_live_provider_actions",
        "external_required",
        external_reason="TikTok controls its platform APIs, scopes and moderation execution authority.",
        external_boundary="Aura may self-host policy, safety, queues and normalized event processing, but provider reads/writes require an approved TikTok or partner transport.",
    ),
    CapabilityPolicy(
        "social_network_publishing",
        "external_required",
        external_reason="Social networks control publishing APIs and account authorization.",
        external_boundary="Aura self-hosts scheduling, content preparation, approvals and audit; final publication requires each provider's supported authorization path.",
    ),
    CapabilityPolicy(
        "card_payment_processing",
        "external_required",
        external_reason="Card processing requires regulated payment rails and a payment service provider/acquirer.",
        external_boundary="Aura self-hosts plans, entitlements, discounts, ledgers and billing orchestration; raw card processing is not self-hosted.",
    ),
    CapabilityPolicy(
        "public_email_delivery",
        "hybrid",
        external_reason="Reliable internet email delivery ultimately depends on external DNS and receiving mail systems even when Aura runs its own SMTP stack.",
        external_boundary="Prefer an Aura-managed/self-hosted mail stack where operationally safe; retain a provider relay fallback until deliverability, abuse handling and reputation controls are proven.",
    ),
)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def self_host_report() -> dict:
    engine_status = {row["name"]: row for row in EngineManager().status()}
    capabilities: list[dict] = []
    for policy in CAPABILITY_POLICIES:
        row = asdict(policy)
        local = []
        for engine_name in policy.local_engines:
            status = engine_status.get(engine_name)
            if status:
                local.append(
                    {
                        "engine": engine_name,
                        "installed": bool(status.get("installed")),
                        "command_configured": bool(status.get("command_configured")),
                        "maturity": status.get("maturity"),
                        "deployment": status.get("deployment"),
                    }
                )
        row["local_engines"] = local
        if policy.preferred_mode == "self_host":
            row["self_host_ready"] = not policy.local_engines or any(
                item["installed"] or item["command_configured"] for item in local
            )
        elif policy.preferred_mode == "external_required":
            row["self_host_ready"] = False
        else:
            row["self_host_ready"] = bool(os.getenv("AURA_SELF_HOST_MAIL_READY", "").strip())
        capabilities.append(row)

    self_hostable = [x for x in capabilities if x["preferred_mode"] == "self_host"]
    ready = [x for x in self_hostable if x["self_host_ready"]]
    return {
        "policy": "self_host_first",
        "principle": "If Aura can safely and lawfully operate a capability on ESP-controlled infrastructure, self-hosting is the preferred production architecture.",
        "external_services_are_exceptions": True,
        "self_hostable_capabilities": len(self_hostable),
        "self_host_ready_capabilities": len(ready),
        "capabilities": capabilities,
        "truth_boundary": "A capability is not reported self-host ready merely because code exists. Local engine/service installation or an explicit self-host readiness signal is required where applicable.",
    }


@router.get("/aura/self-host/status")
def aura_self_host_status(request: Request):
    _member(request)
    return self_host_report()


__all__ = ["router", "CAPABILITY_POLICIES", "self_host_report"]
