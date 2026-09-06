from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from .aura_agent_core import AuraAgent
from .aura_agent_tools import public_tool_specs
from .aura_avatar_runtime import avatar_status
from .aura_calendar_extensions import install_aura_calendar_extensions, router as aura_calendar_router
from .aura_chat_store import AuraChatStore
from .aura_connector_extensions import install_aura_connector_extensions
from .aura_connectors import install_aura_connector_tools, router as aura_connectors_router, vault as connector_vault
from .aura_gmail_extensions import install_aura_gmail_extensions
from .aura_multimodal import AuraVisionService
from .aura_notifications import notification_store, router as aura_notifications_router
from .aura_sandbox import sandbox
from .aura_scheduled_briefing import install_aura_scheduled_briefing
from .aura_tasks import install_aura_task_tools, router as aura_tasks_router, task_store
from .aura_workspace_briefing import install_aura_workspace_briefing
from .creative_renderers import renderer_states
from .rhiannon_voice_contracts import (
    CapabilityState,
    VoiceCapability,
    public_voice_contract,
    voice_capability_snapshot,
)
from .speech import AuraSpeechService
from .web_access import AuraWebGateway

# These are installed through this already-mounted workspace module so the production
# entrypoint stays stable. Later tool wrappers still delegate to them, while the verified
# workflow wrapper remains the final execution layer installed by app.py.
install_aura_task_tools()
install_aura_connector_tools()
install_aura_connector_extensions()
install_aura_gmail_extensions()
install_aura_calendar_extensions()
install_aura_workspace_briefing()
install_aura_scheduled_briefing()

router = APIRouter(tags=["Aura Workspace"])
router.include_router(aura_tasks_router)
router.include_router(aura_connectors_router)
router.include_router(aura_calendar_router)
router.include_router(aura_notifications_router)
store = AuraChatStore()
agent = AuraAgent(store=store)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _safe_renderers() -> dict:
    rows = renderer_states(probe=False)
    for value in rows.values():
        value.pop("base_url", None)
    return rows


def _speech_status() -> dict:
    try:
        diagnostics = AuraSpeechService().diagnostics()
    except Exception:
        diagnostics = {}

    capability_rows = voice_capability_snapshot(diagnostics)
    capabilities = {row.capability: row for row in capability_rows}
    synthesis = capabilities[VoiceCapability.SPEECH_SYNTHESIS]
    stt_configured = bool(
        diagnostics.get("stt_command_configured")
        or (diagnostics.get("whisper_cli") and diagnostics.get("whisper_model_configured"))
    )
    tts_configured = bool(
        synthesis.local_runtime_configured or synthesis.remote_provider_configured
    )
    return {
        "stt_configured": stt_configured,
        "tts_configured": tts_configured,
        "tts_state": synthesis.state.value,
        # Configuration alone is not health evidence. This remains false until the
        # provider-neutral contract receives authenticated runtime health evidence.
        "tts_ready": synthesis.state == CapabilityState.AVAILABLE,
        "voice_contract": public_voice_contract(diagnostics),
    }


def _web_status() -> dict:
    try:
        value = AuraWebGateway().diagnostics()
    except Exception:
        return {"enabled": False, "search_configured": False, "direct_https_fetch": False}
    return {
        "enabled": bool(value.get("enabled")),
        "search_configured": bool(value.get("self_hosted_search_configured")),
        "direct_https_fetch": bool(value.get("direct_https_fetch")),
        "ssrf_private_network_blocking": bool(value.get("private_network_fetch_blocked")),
        "safe_redirect_validation": bool(value.get("safe_redirect_validation")),
    }


@router.get("/aura-intelligence/api/voice/contracts")
def rhiannon_voice_contract(request: Request, response: Response):
    _member(request)
    response.headers["Cache-Control"] = "private, no-store"
    return _speech_status()["voice_contract"]


@router.get("/aura-intelligence/api/capabilities")
def capabilities(request: Request):
    member = _member(request)
    model = agent.diagnostics()
    vision = AuraVisionService().diagnostics()
    speech = _speech_status()
    web = _web_status()
    renderers = _safe_renderers()
    avatar = avatar_status()
    sandbox_state = sandbox.diagnostics()
    task_runtime = task_store.worker_status()
    connector_runtime = connector_vault.diagnostics()
    connected_services = connector_vault.status(member.user_id)
    unread_notifications = notification_store.unread_count(member.user_id)
    tools = public_tool_specs(web_enabled=True, tools_enabled=True)
    return {
        "member_plan": member.plan.id,
        "software": {
            "realtime_streaming": True,
            "persistent_private_threads": True,
            "conversation_search_edit_branch_regenerate": True,
            "explicit_memory": True,
            "fast_auto_deep_creative_modes": True,
            "private_custom_aura_profiles": True,
            "private_versioned_artifacts": True,
            "isolated_code_sandbox_adapter": True,
            "durable_aura_tasks": True,
            "scheduled_read_only_research": True,
            "scheduled_connected_workspace_briefing": True,
            "private_notification_inbox": True,
            "aura_today_center": True,
            "encrypted_private_connectors": True,
            "google_drive_search_and_document_read": True,
            "google_calendar_read_connector": True,
            "google_calendar_event_detail": True,
            "gmail_search_connector": True,
            "gmail_full_message_read": True,
            "google_workspace_briefing": True,
            "project_pinning": True,
            "project_knowledge_search": True,
            "file_upload_and_extraction": True,
            "rights_gated_attachment_promotion": True,
            "current_turn_attachment_workflows": True,
            "verified_multi_step_tool_workflows": True,
            "image_audio_video_perception_adapters": True,
            "safe_calculator": True,
            "safe_statistics": True,
            "safe_csv_xlsx_analysis": True,
            "svg_data_charts": True,
            "web_search_and_fetch": True,
            "multi_source_deep_research": True,
            "verified_source_trails": True,
            "conversational_daw_controls": True,
            "song_dna_tools": True,
            "creative_image_video_tools": True,
            "voice_profile_inspection": True,
            "single_turn_voice_input": True,
            "hands_free_voice_conversation": True,
            "rhiannon_voice_capability_contract": True,
            "canonical_viseme_timing_contract": True,
            "voice_asset_provenance_contract": True,
            "embodied_aura_state_runtime": True,
            "conversation_markdown_export": True,
        },
        "runtime": {
            "reasoning": model,
            "vision": vision,
            "speech": speech,
            "web": web,
            "creative_renderers": renderers,
            "avatar": avatar,
            "sandbox": sandbox_state,
            "tasks": task_runtime,
            "notifications": {"unread_count": unread_notifications},
            "connectors": {
                "runtime": connector_runtime,
                "connected": connected_services,
                "tokens_exposed": False,
            },
            "deep_research_ready": bool(web.get("enabled") and web.get("search_configured")),
            "hands_free_voice_configured": bool(speech.get("stt_configured") and speech.get("tts_configured")),
            "hands_free_voice_ready": bool(speech.get("stt_configured") and speech.get("tts_ready")),
            "rhiannon_speech_synthesis_state": speech.get("tts_state", CapabilityState.UNAVAILABLE.value),
            "image_generation_ready": bool((renderers.get("image") or {}).get("configured")),
            "video_generation_ready": bool((renderers.get("video") or {}).get("configured")),
            "production_3d_avatar_ready": bool(avatar.get("production_3d_ready")),
            "isolated_code_execution_ready": bool(sandbox_state.get("configured")),
            "aura_task_worker_ready": bool(task_runtime.get("ready")),
            "google_connector_ready": bool(connector_runtime.get("encrypted_vault_ready") and connector_runtime.get("google_oauth_configured")),
        },
        "tools": tools,
        "truthfulness_contract": "A software feature can be connected while its external/local model, renderer, speech service, isolated sandbox, task worker, OAuth provider or 3D rig remains unconfigured or unverified; Rhiannon must report that state rather than pretending execution succeeded.",
    }


def _safe_filename(title: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", title).strip("._")[:100]
    return value or "Aura_Conversation"


@router.get("/aura-intelligence/api/threads/{thread_id}/export.md")
def export_thread_markdown(thread_id: str, request: Request):
    member = _member(request)
    thread = store.thread(member.user_id, thread_id)
    if not thread:
        raise HTTPException(404, "Aura conversation not found")
    messages = store.messages(member.user_id, thread_id, limit=400)
    lines = [
        f"# {thread.get('title') or 'Aura conversation'}",
        "",
        "Exported from Pulsar-Frequency House — Aura Core",
        f"Exported: {datetime.now(timezone.utc).isoformat()}",
    ]
    if thread.get("project_name"):
        lines.append(f"Pinned project: `{thread['project_name']}`")
    lines += ["", "---", ""]
    for message in messages:
        role = "Member" if message.get("role") == "user" else "Aura"
        lines.append(f"## {role}")
        lines.append("")
        attachments = store.message_attachments(member.user_id, thread_id, message["id"])
        if attachments:
            lines.append("Attachments: " + ", ".join(f"`{item.get('name') or 'attachment'}`" for item in attachments))
            lines.append("")
        lines.append(str(message.get("content") or "").strip())
        lines += ["", "---", ""]
    body = "\n".join(lines).rstrip() + "\n"
    filename = _safe_filename(str(thread.get("title") or "Aura_Conversation")) + ".md"
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


__all__ = ["router"]
