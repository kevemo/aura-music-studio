"""Production entrypoint for Pulsar-Frequency House.

Run locally:
    uvicorn app:app --host 0.0.0.0 --port 8000

The legacy ``aura_music_studio`` Python package name is retained as a compatibility
identifier for existing installs, project data and deployment configuration.
"""

from aura_music_studio.api import app
from aura_music_studio.aura_intelligence import router as aura_intelligence_router
from aura_music_studio.aura_realtime_portal import router as aura_realtime_portal_router
from aura_music_studio.aura_streaming import router as aura_streaming_router
from aura_music_studio.aura_tool_extensions import install_aura_tool_extensions
from aura_music_studio.brand_migration import BrandMigrationMiddleware
from aura_music_studio.brand_ui import router as brand_router
from aura_music_studio.compute_node_api import router as compute_node_router
from aura_music_studio.creative_portal import router as creative_portal_router
from aura_music_studio.creative_project_api import router as creative_project_router
from aura_music_studio.creative_workspace import router as creative_workspace_router
from aura_music_studio.daw_api import router as daw_router
from aura_music_studio.daw_mixer_ui import router as daw_mixer_ui_router
from aura_music_studio.daw_portal import router as daw_portal_router
from aura_music_studio.daw_recording_api import router as daw_recording_router
from aura_music_studio.daw_recording_ui import router as daw_recording_ui_router
from aura_music_studio.daw_routing_api import router as daw_routing_router
from aura_music_studio.daw_routing_ui import router as daw_routing_ui_router
from aura_music_studio.discovery import router as discovery_router
from aura_music_studio.edit_api import router as edit_router
from aura_music_studio.engineering_job_api import router as engineering_job_router
from aura_music_studio.esp_command_center import router as esp_command_center_router
from aura_music_studio.esp_level_up import (
    install_esp_access_subscription_separation,
    router as esp_level_up_router,
)
from aura_music_studio.esp_level_up_gateway import router as esp_level_up_gateway_router
from aura_music_studio.esp_niche_portal import router as esp_niche_portal_router
from aura_music_studio.esp_owner_access_portal import router as esp_owner_access_router
from aura_music_studio.esp_progress_portal import router as esp_progress_portal_router
from aura_music_studio.esp_social_access_control import install_social_access_control
from aura_music_studio.esp_social_insights_portal import router as esp_social_insights_router
from aura_music_studio.esp_social_intelligence_api import router as esp_social_intelligence_router
from aura_music_studio.esp_social_portal_overlay import router as esp_social_portal_overlay_router
from aura_music_studio.lyric_alignment_api import router as lyric_alignment_router
from aura_music_studio.lyric_alignment_portal import router as lyric_alignment_portal_router
from aura_music_studio.media_studios import router as media_studios_router
from aura_music_studio.member_dashboard import router as member_dashboard_router
from aura_music_studio.output_api import router as output_router
from aura_music_studio.owner_auth import OwnerLegacyCompatibilityMiddleware
from aura_music_studio.owner_auth_portal import router as owner_auth_router
from aura_music_studio.owner_backup_portal import router as owner_backup_router
from aura_music_studio.owner_compute_portal import router as owner_compute_router
from aura_music_studio.owner_control_center import router as owner_control_center_router
from aura_music_studio.owner_identity import OwnerIdentityMiddleware
from aura_music_studio.owner_user_directory import router as owner_user_directory_router
from aura_music_studio.owner_users_portal import router as owner_users_legacy_router
from aura_music_studio.performance_input_api import router as performance_input_router
from aura_music_studio.privacy_api import router as privacy_router
from aura_music_studio.production_portal import router as production_portal_router
from aura_music_studio.production_suite_api import router as production_suite_router
from aura_music_studio.recording_api import router as recording_router
from aura_music_studio.recording_portal import router as recording_portal_router
from aura_music_studio.revision_api import router as revision_router
from aura_music_studio.revision_portal import router as revision_portal_router
from aura_music_studio.social_management_api import router as social_management_router
from aura_music_studio.social_management_portal import router as social_management_portal_router
from aura_music_studio.song_dna_api import router as song_dna_router
from aura_music_studio.song_dna_execution_guard import router as song_dna_execution_guard_router
from aura_music_studio.song_dna_execution_overlay import router as song_dna_execution_overlay_router
from aura_music_studio.song_dna_execution_portal import router as song_dna_execution_portal_router
from aura_music_studio.song_dna_portal import router as song_dna_portal_router
from aura_music_studio.source_detection_api import router as source_detection_router
from aura_music_studio.system_api import router as system_router
from aura_music_studio.take_api import router as take_router
from aura_music_studio.take_portal import router as take_portal_router
from aura_music_studio.usage_tracking import CreativeUsageMiddleware
from aura_music_studio.vocal_api import router as vocal_router
from aura_music_studio.voice_house_api import router as voice_house_router
from aura_music_studio.voice_house_assets_api import router as voice_house_assets_router
from aura_music_studio.voice_house_portal import router as voice_house_portal_router

# Final permission model:
# 1. Free/Basic/Pro controls public creative features only.
# 2. ESP Creator/Agent/Both is a separate Mary/Kev-controlled permission.
# 3. Social Media Centre additionally requires ESP-only affiliation, niche selection and
#    no owner suspension. These policies are installed before requests are handled.
install_esp_access_subscription_separation()
install_social_access_control()
# Aura Core's additional cross-media tools are installed centrally so normal and realtime
# chat share the exact same tool registry, write gates and idempotency behavior.
install_aura_tool_extensions()

# ``aura_music_studio.api`` already registers historical public/member/owner surfaces.
# Replace only the surfaces now owned by Pulsar-Frequency House. The old owner login/logout
# routes are removed so new browser sessions never store LSS_ADMIN_KEY as the session value.
_REPLACED_ROUTES = {
    "/",
    "/dashboard",
    "/owner",
    "/owner/login",
    "/owner/logout",
    "/owner/dashboard",
}
app.router.routes[:] = [
    route for route in app.router.routes if getattr(route, "path", None) not in _REPLACED_ROUTES
]
app.include_router(creative_portal_router)
# Dedicated media routes are registered before the compatibility redirects living in
# member_dashboard, so /video-studio and /image-designer open real media workspaces.
app.include_router(media_studios_router)
app.include_router(member_dashboard_router)
# The realtime portal owns the visible /aura-intelligence page. The underlying API router
# remains mounted afterwards for durable threads/memory/files, while the separate streaming
# router provides token/tool events. Route order prevents the older compatibility page from
# shadowing the realtime interface.
app.include_router(aura_realtime_portal_router)
app.include_router(aura_intelligence_router)
app.include_router(aura_streaming_router)

app.include_router(brand_router)
app.include_router(discovery_router)
app.include_router(compute_node_router)
app.include_router(creative_project_router)
app.include_router(creative_workspace_router)

# Canonical ESP gateway is first: signed-in regular users can request ESP verification,
# active ESP users are sent through niche selection and then into Level Up Hub. The older
# command center remains mounted later for training-resource routes, but no longer owns the
# top-level /command-center user journey.
app.include_router(esp_level_up_gateway_router)
app.include_router(esp_niche_portal_router)
app.include_router(esp_level_up_router)
app.include_router(esp_progress_portal_router)
app.include_router(social_management_router, include_in_schema=False)
app.include_router(esp_social_intelligence_router, include_in_schema=False)
app.include_router(esp_social_insights_router)
app.include_router(esp_social_portal_overlay_router)
app.include_router(social_management_portal_router)

# Owner login uses an opaque hashed server-side session. Mary/Kev also have a dedicated
# ESP access console for role activation/revocation, Social Media Centre suspension and
# explicit Agent→Creator assignments.
app.include_router(owner_auth_router)
app.include_router(esp_owner_access_router)
app.include_router(owner_control_center_router)
app.include_router(owner_user_directory_router)
app.include_router(owner_users_legacy_router)

app.include_router(daw_router)
app.include_router(daw_portal_router)
app.include_router(daw_recording_router)
app.include_router(daw_recording_ui_router)
app.include_router(daw_routing_router)
app.include_router(daw_routing_ui_router)
app.include_router(daw_mixer_ui_router)
app.include_router(vocal_router)
# Voice House uses explicit consent evidence, purpose-scoped permissions, conservative
# similarity caps and immediate revocation. The asset selector is mounted separately so
# raw server paths never need to be exposed to the browser.
app.include_router(voice_house_router)
app.include_router(voice_house_assets_router)
app.include_router(voice_house_portal_router)
# Song DNA v2: the mature editor remains the source/planning surface. The execution
# console adds Generate → Audition → Reject/Commit, while lyric alignment keeps estimated
# timestamps separate from verified/forced-aligned timing. The guard is registered before
# the base Song DNA API so public surgical vocal rendering cannot use unverified timing.
app.include_router(song_dna_execution_portal_router)
app.include_router(lyric_alignment_portal_router)
app.include_router(song_dna_execution_overlay_router)
app.include_router(song_dna_portal_router)
app.include_router(song_dna_execution_guard_router)
app.include_router(song_dna_router)
app.include_router(lyric_alignment_router)
# Performance inputs accept a user-owned rhythm, beatbox, hum, melody or instrument
# performance and convert it into editable timing/MIDI guidance without treating MIDI as
# final audio. The original real audio stays part of project DNA as the performance anchor.
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
app.include_router(system_router)

# Cross-media creation activity is recorded only after successful writes and stores
# category/event metadata, not the user's private creative content itself.
app.add_middleware(CreativeUsageMiddleware)

# Bind the signed Mary/Kev owner identity to owner actions without trusting form fields.
app.add_middleware(OwnerIdentityMiddleware)

# Temporary compatibility bridge: a valid opaque owner session is translated only in
# the in-process Request cookie cache for legacy backup/compute/payment route functions.
# The deployment key is never written to the response/browser by this middleware.
app.add_middleware(OwnerLegacyCompatibilityMiddleware)

# Applied last so all legacy text emitted by older modules is rewritten at the public
# HTTP boundary. Binary media responses are not modified.
app.add_middleware(BrandMigrationMiddleware)

__all__ = ["app"]
