"""Production entrypoint for Elevate Souls Productions Content Creation Command Center.

Powered by Aura AI.

Run locally:
    uvicorn app:app --host 0.0.0.0 --port 8000

The legacy ``aura_music_studio`` Python package name is retained as a compatibility
identifier for existing installs, project data and deployment configuration.
"""

from aura_music_studio.api import app
from aura_music_studio.auth_security import CrossSiteRequestGuardMiddleware
from aura_music_studio.aura_artifacts import install_aura_artifacts, router as aura_artifacts_router
from aura_music_studio.aura_artifacts_ui import AuraArtifactsUIMiddleware, router as aura_artifacts_ui_router
from aura_music_studio.aura_attachment_tools import install_aura_attachment_tools
from aura_music_studio.aura_avatar_runtime import AuraAvatarRuntimeMiddleware, router as aura_avatar_router
from aura_music_studio.aura_chat_hardening import install_aura_chat_hardening
from aura_music_studio.aura_context_extensions import install_aura_context_extensions
from aura_music_studio.aura_daw_tools import install_aura_daw_tools
from aura_music_studio.aura_game_tools import install_aura_game_tools
from aura_music_studio.aura_intelligence import router as aura_intelligence_router
from aura_music_studio.aura_live_relay_error_boundary import AuraLiveRelayErrorBoundaryMiddleware
from aura_music_studio.aura_live_relay_identity import AuraLiveRelayIdentityMiddleware
from aura_music_studio.aura_multimodal import router as aura_multimodal_router
from aura_music_studio.aura_notifications_ui import router as aura_notifications_ui_router
from aura_music_studio.aura_productivity_tools import install_aura_productivity_tools
from aura_music_studio.aura_profiles import install_aura_profiles, router as aura_profiles_router
from aura_music_studio.aura_project_bridge import router as aura_project_bridge_router
from aura_music_studio.aura_project_knowledge import install_aura_project_knowledge
from aura_music_studio.aura_realtime_portal import router as aura_realtime_portal_router
from aura_music_studio.aura_reasoning_modes import router as aura_reasoning_modes_router
from aura_music_studio.aura_research_tools import install_aura_research_tools
from aura_music_studio.aura_runtime_context import install_aura_runtime_context
from aura_music_studio.aura_sandbox import install_aura_sandbox_tools, router as aura_sandbox_router
from aura_music_studio.aura_sec_portal import router as aura_sec_portal_router
from aura_music_studio.aura_self_host_control import router as aura_self_host_control_router
from aura_music_studio.aura_streaming import router as aura_streaming_router
from aura_music_studio.aura_table_tools import install_aura_table_tools
from aura_music_studio.aura_tool_extensions import install_aura_tool_extensions
from aura_music_studio.aura_ui_extension import AuraUIExtensionMiddleware, router as aura_ui_extension_router
from aura_music_studio.aura_voice_conversation import AuraVoiceConversationMiddleware, router as aura_voice_conversation_router
from aura_music_studio.aura_workflow_engine import install_aura_workflow_engine
from aura_music_studio.aura_workspace_api import router as aura_workspace_router
from aura_music_studio.brand_migration import BrandMigrationMiddleware
from aura_music_studio.brand_ui import router as brand_router
from aura_music_studio.commercial_entitlement_routes import router as commercial_entitlement_router
from aura_music_studio.compute_node_api import router as compute_node_router
from aura_music_studio.credit_wallet import router as credit_wallet_router
from aura_music_studio.creative_library import router as creative_library_router
from aura_music_studio.creative_media_preview import CreativeMediaPreviewMiddleware, router as creative_media_preview_router
from aura_music_studio.creative_portal import router as creative_portal_router
from aura_music_studio.creative_project_api import router as creative_project_router
from aura_music_studio.creative_project_continuity import (
    CreativeProjectContinuityMiddleware,
    router as creative_project_continuity_router,
)
from aura_music_studio.creative_studio_integration import (
    CreativeStudioIntegrationMiddleware,
    router as creative_studio_integration_router,
)
from aura_music_studio.creative_version_autopromotion import router as creative_version_autopromotion_router
from aura_music_studio.creative_workspace import router as creative_workspace_router
from aura_music_studio.daw_api import router as daw_router
from aura_music_studio.daw_midi import router as daw_midi_router
from aura_music_studio.daw_mixer_ui import router as daw_mixer_ui_router
from aura_music_studio.daw_portal import router as daw_portal_router
from aura_music_studio.daw_recording_api import router as daw_recording_router
from aura_music_studio.daw_recording_ui import router as daw_recording_ui_router
from aura_music_studio.daw_routing_api import router as daw_routing_router
from aura_music_studio.daw_routing_ui import router as daw_routing_ui_router
from aura_music_studio.discovery import router as discovery_router
from aura_music_studio.edit_api import router as edit_router
from aura_music_studio.engineering_job_api import router as engineering_job_router
from aura_music_studio.esp_agent_roster_overlay import router as esp_agent_roster_overlay_router
from aura_music_studio.esp_command_center import router as esp_command_center_router
from aura_music_studio.esp_creator_plan_overlay import router as esp_creator_plan_overlay_router
from aura_music_studio.esp_level_up import install_esp_access_subscription_separation, router as esp_level_up_router
from aura_music_studio.esp_level_up_gateway import router as esp_level_up_gateway_router
from aura_music_studio.esp_niche_portal import router as esp_niche_portal_router
from aura_music_studio.esp_owner_access_portal import router as esp_owner_access_router
from aura_music_studio.esp_progress_portal import router as esp_progress_portal_router
from aura_music_studio.esp_social_access_control import install_social_access_control
from aura_music_studio.esp_social_insights_portal import router as esp_social_insights_router
from aura_music_studio.esp_social_intelligence_api import router as esp_social_intelligence_router
from aura_music_studio.esp_social_portal_overlay import router as esp_social_portal_overlay_router
from aura_music_studio.game_forge_api import router as game_forge_router
from aura_music_studio.game_forge_portal import router as game_forge_portal_router
from aura_music_studio.game_forge_project_navigation_middleware import GameForgeProjectNavigationMiddleware
from aura_music_studio.game_forge_world_api import router as game_forge_world_router
from aura_music_studio.lyric_alignment_api import router as lyric_alignment_router
from aura_music_studio.lyric_alignment_portal import router as lyric_alignment_portal_router
from aura_music_studio.media_studios import router as media_studios_router
from aura_music_studio.member_dashboard import router as member_dashboard_router
from aura_music_studio.native_commerce_api import (
    native_paypal_checkout,
    native_paypal_webhook,
    native_products_account_json,
    native_products_account_page,
    native_products_pricing,
)
from aura_music_studio.output_api import router as output_router
from aura_music_studio.owner_auth_portal import router as owner_auth_router
from aura_music_studio.owner_authorization_migration import install_owner_authorization_migration
from aura_music_studio.owner_backup_portal import router as owner_backup_router
from aura_music_studio.owner_compute_portal import router as owner_compute_router
from aura_music_studio.owner_control_center import router as owner_control_center_router
from aura_music_studio.owner_identity import OwnerIdentityMiddleware
from aura_music_studio.owner_user_directory import router as owner_user_directory_router
from aura_music_studio.owner_user_intelligence import router as owner_user_intelligence_router
from aura_music_studio.owner_users_portal import router as owner_users_legacy_router
from aura_music_studio.performance_input_api import router as performance_input_router
from aura_music_studio.privacy_api import router as privacy_router
from aura_music_studio.production_portal import router as production_portal_router
from aura_music_studio.production_readiness import router as production_readiness_router
from aura_music_studio.production_suite_api import router as production_suite_router
from aura_music_studio.professional_editor_api import router as professional_editor_router
from aura_music_studio.professional_editor_security_overlay import install_professional_editor_patch_guard
from aura_music_studio.provider_cost_governance import (
    install_provider_cost_governance,
    router as provider_cost_governance_router,
)
from aura_music_studio.pulsar_player import PulsarPlayerMiddleware, router as pulsar_player_router
from aura_music_studio.recording_api import router as recording_router
from aura_music_studio.recording_portal import router as recording_portal_router
from aura_music_studio.revision_api import router as revision_router
from aura_music_studio.revision_portal import router as revision_portal_router
from aura_music_studio.route_integrity import deduplicate_http_routes
from aura_music_studio.shared_sky_owner_ops import router as shared_sky_owner_ops_router
from aura_music_studio.social_management_api import router as social_management_router
from aura_music_studio.social_management_portal import router as social_management_portal_router
from aura_music_studio.song_dna_api import router as song_dna_router
from aura_music_studio.song_dna_execution_guard import router as song_dna_execution_guard_router
from aura_music_studio.song_dna_execution_overlay import router as song_dna_execution_overlay_router
from aura_music_studio.song_dna_execution_portal import router as song_dna_execution_portal_router
from aura_music_studio.song_dna_portal import router as song_dna_portal_router
from aura_music_studio.source_detection_api import router as source_detection_router
from aura_music_studio.stripe_billing_hardening import router as stripe_billing_hardening_router
from aura_music_studio.system_api import router as system_router
from aura_music_studio.take_api import router as take_router
from aura_music_studio.take_portal import router as take_portal_router
from aura_music_studio.universal_creative_catalogue_api import router as universal_creative_catalogue_router
from aura_music_studio.universal_creative_library import router as universal_creative_library_router
from aura_music_studio.usage_tracking import CreativeUsageMiddleware
from aura_music_studio.vocal_api import router as vocal_router
from aura_music_studio.voice_house_api import router as voice_house_router
from aura_music_studio.voice_house_assets_api import router as voice_house_assets_router
from aura_music_studio.voice_house_portal import router as voice_house_portal_router

install_esp_access_subscription_separation()
install_social_access_control()
install_aura_runtime_context()
install_aura_tool_extensions()
install_aura_productivity_tools()
install_aura_daw_tools()
install_aura_research_tools()
install_aura_table_tools()
install_aura_project_knowledge()
install_aura_attachment_tools()
install_aura_artifacts()
# Code execution is available only through a separately configured isolated sandbox service.
# Member code is never executed in the FastAPI process or host shell.
install_aura_sandbox_tools()
# Game Forge tools register before workflow resolution so sequential $stepN/$previous values can
# safely pass verified Game DNA/build results between explicit Aura game actions.
install_aura_game_tools()
# Install workflow resolution last among tool wrappers so every tool class can contribute a
# verified result to $stepN/$previous references while preserving its own gates.
install_aura_workflow_engine()
install_aura_chat_hardening()
install_aura_context_extensions()
install_aura_profiles()
install_owner_authorization_migration()
install_professional_editor_patch_guard()
# Provider-cost governance wraps successful renderer submissions only for operational metering.
# It never changes Creation Coins, subscriptions or ESP permissions.
install_provider_cost_governance()

_REPLACED_ROUTES = {"/", "/dashboard", "/owner", "/owner/login", "/owner/logout", "/owner/dashboard"}
app.router.routes[:] = [route for route in app.router.routes if getattr(route, "path", None) not in _REPLACED_ROUTES]

app.include_router(creative_portal_router)
app.include_router(media_studios_router)
app.include_router(member_dashboard_router)
app.include_router(game_forge_portal_router)
# World-integrity scan/publish handlers intentionally precede the foundation Game Forge routes.
app.include_router(game_forge_world_router)
app.include_router(game_forge_router)
app.include_router(aura_realtime_portal_router)
app.include_router(aura_intelligence_router)
app.include_router(aura_streaming_router)
app.include_router(aura_multimodal_router)
app.include_router(aura_reasoning_modes_router)
app.include_router(aura_profiles_router)
app.include_router(aura_artifacts_router)
app.include_router(aura_artifacts_ui_router)
app.include_router(aura_notifications_ui_router)
app.include_router(aura_sandbox_router)
app.include_router(aura_self_host_control_router)
# Aura Sec is mounted directly into the canonical production app because late nested router
# composition is not propagated after a parent router has already been included. This keeps the
# member Security Center inside the one shared application without exposing native authority.
app.include_router(aura_sec_portal_router)
app.include_router(aura_ui_extension_router)
app.include_router(aura_voice_conversation_router)
app.include_router(aura_avatar_router)
app.include_router(aura_project_bridge_router)
app.include_router(aura_workspace_router)
app.include_router(brand_router)
app.include_router(discovery_router)
app.include_router(compute_node_router)
app.include_router(credit_wallet_router)
app.include_router(creative_version_autopromotion_router)
# Nested compatibility routers are not flattened by the current overlay adapter. Mount the
# professional editor directly so its authenticated/gated endpoints are present in production.
app.include_router(professional_editor_router)
# Commercial entitlement routes intentionally precede their underlying Creative handlers.
# This makes image/poster daily limits and media-download gates authoritative server-side.
app.include_router(commercial_entitlement_router)
# Shared creative upload/render bridge is mounted on the production app so Creative House,
# Image Designer and Video Studio use the same tenant project, rights ledger and renderer inputs.
app.include_router(creative_studio_integration_router)
# One explicit project context follows the member across the five creative surfaces without
# creating a parallel project store or widening any server-side entitlement/role boundary.
app.include_router(creative_project_continuity_router)
app.include_router(creative_project_router)
app.include_router(creative_media_preview_router)
# Universal catalogue routes are mounted directly on the canonical production app. Specific
# menu/runtime endpoints must precede the legacy /{item_id:path} catch-all so they are reachable.
app.include_router(universal_creative_catalogue_router)
app.include_router(universal_creative_library_router)
app.include_router(creative_library_router)
app.include_router(pulsar_player_router)
app.include_router(creative_workspace_router)

app.include_router(esp_level_up_gateway_router)
app.include_router(esp_niche_portal_router)
app.include_router(esp_agent_roster_overlay_router)
# Shared Sky owner runtime is mounted directly because late nested-router composition is not
# propagated after an already-included parent router has been snapshotted by FastAPI.
app.include_router(shared_sky_owner_ops_router)
app.include_router(esp_creator_plan_overlay_router)
app.include_router(esp_level_up_router)
app.include_router(esp_progress_portal_router)
app.include_router(social_management_router, include_in_schema=False)
app.include_router(esp_social_intelligence_router, include_in_schema=False)
app.include_router(esp_social_insights_router)
app.include_router(esp_social_portal_overlay_router)
app.include_router(social_management_portal_router)
app.include_router(owner_auth_router)
app.include_router(esp_owner_access_router)
app.include_router(owner_control_center_router)
app.include_router(owner_user_intelligence_router)
app.include_router(owner_user_directory_router)
app.include_router(owner_users_legacy_router)
app.include_router(provider_cost_governance_router)
# Native commerce routes are bound directly to the canonical FastAPI app rather than copied from a
# late-composed router. This preserves the verified handlers while making production reachability
# deterministic under the Command Center's overlay/router composition architecture.
app.add_api_route(
    "/pricing/native-products",
    native_products_pricing,
    methods=["GET"],
)
app.add_api_route(
    "/account/native-products.json",
    native_products_account_json,
    methods=["GET"],
)
app.add_api_route(
    "/account/native-products",
    native_products_account_page,
    methods=["GET"],
    include_in_schema=False,
)
app.add_api_route(
    "/billing/native/paypal/checkout",
    native_paypal_checkout,
    methods=["POST"],
)
app.add_api_route(
    "/billing/native/paypal/webhook",
    native_paypal_webhook,
    methods=["POST"],
    include_in_schema=False,
)
# Hardened Stripe subscription, Creation Coin and marketplace routes must be mounted once at the
# production entrypoint so payment effects can only flow through provider-evidence controls.
app.include_router(stripe_billing_hardening_router)

app.include_router(daw_router)
app.include_router(daw_midi_router)
app.include_router(daw_portal_router)
app.include_router(daw_recording_router)
app.include_router(daw_recording_ui_router)
app.include_router(daw_routing_router)
app.include_router(daw_routing_ui_router)
app.include_router(daw_mixer_ui_router)
app.include_router(vocal_router)
app.include_router(voice_house_router)
app.include_router(voice_house_assets_router)
app.include_router(voice_house_portal_router)
app.include_router(song_dna_execution_portal_router)
app.include_router(lyric_alignment_portal_router)
app.include_router(song_dna_execution_overlay_router)
app.include_router(song_dna_portal_router)
app.include_router(song_dna_execution_guard_router)
app.include_router(song_dna_router)
app.include_router(lyric_alignment_router)
app.include_router(performance_input_router)
app.include_router(edit_router)
app.include_router(engineering_job_router)
app.include_router(esp_command_center_router)
app.include_router(recording_router)
app.include_router(output_router)
app.include_router(owner_backup_router)
app.include_router(owner_compute_router)
app.include_router(privacy_router)
app.include_router(production_portal_router)
app.include_router(production_suite_router)
app.include_router(recording_portal_router)
app.include_router(revision_router)
app.include_router(revision_portal_router)
app.include_router(take_router)
app.include_router(take_portal_router)
app.include_router(source_detection_router)
# Liveness, fail-closed deployment readiness and authenticated metrics must be mounted on the
# real release application, not only exercised on an isolated test FastAPI instance.
app.include_router(production_readiness_router)
app.include_router(system_router)

# FastAPI/Starlette dispatches the first exact path+method match. Remove later exact copies after
# every router has been composed so route ownership is unambiguous and OpenAPI has one operation
# per reachable handler while preserving the runtime precedence the application already used.
deduplicate_http_routes(app)

app.add_middleware(CreativeUsageMiddleware)
app.add_middleware(CreativeMediaPreviewMiddleware)
app.add_middleware(CreativeStudioIntegrationMiddleware)
# Added after the shared creative integration middleware so the continuity script executes last
# on these member pages and only rewrites same-origin links among the five creative surfaces.
app.add_middleware(CreativeProjectContinuityMiddleware)
# Game Forge sub-workspaces keep their route-level auth/plan authority; this layer only restores
# the persisted Creative project identity into same-game navigation after an HTML page is allowed.
app.add_middleware(GameForgeProjectNavigationMiddleware)
app.add_middleware(PulsarPlayerMiddleware)
app.add_middleware(AuraUIExtensionMiddleware)
app.add_middleware(AuraArtifactsUIMiddleware)
app.add_middleware(AuraVoiceConversationMiddleware)
app.add_middleware(AuraAvatarRuntimeMiddleware)
app.add_middleware(OwnerIdentityMiddleware)
app.add_middleware(BrandMigrationMiddleware)
# Require provider + LIVE session + provider event identity at the canonical production relay
# boundary while preserving the existing engine's token, membership, rate and processing gates.
app.add_middleware(AuraLiveRelayIdentityMiddleware)
# Added after the relay identity boundary so validation HTTPExceptions remain bounded 4xx JSON.
app.add_middleware(AuraLiveRelayErrorBoundaryMiddleware)
# Added last so this guard wraps all route surfaces at the browser request boundary.
app.add_middleware(CrossSiteRequestGuardMiddleware)

__all__ = ["app"]
