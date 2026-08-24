"""Production entrypoint for Pulsar-Frequency House.

Run locally:
    uvicorn app:app --host 0.0.0.0 --port 8000

The legacy ``aura_music_studio`` Python package name is retained as a compatibility
identifier for existing installs, project data and deployment configuration.
"""

from aura_music_studio.api import app
from aura_music_studio.aura_intelligence import router as aura_intelligence_router
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
from aura_music_studio.esp_niche_portal import router as esp_niche_portal_router
from aura_music_studio.esp_progress_portal import router as esp_progress_portal_router
from aura_music_studio.member_dashboard import router as member_dashboard_router
from aura_music_studio.output_api import router as output_router
from aura_music_studio.owner_backup_portal import router as owner_backup_router
from aura_music_studio.owner_compute_portal import router as owner_compute_router
from aura_music_studio.owner_control_center import router as owner_control_center_router
from aura_music_studio.owner_identity import OwnerIdentityMiddleware
from aura_music_studio.owner_users_portal import router as owner_users_router
from aura_music_studio.privacy_api import router as privacy_router
from aura_music_studio.production_portal import router as production_portal_router
from aura_music_studio.production_suite_api import router as production_suite_router
from aura_music_studio.recording_api import router as recording_router
from aura_music_studio.recording_portal import router as recording_portal_router
from aura_music_studio.revision_api import router as revision_router
from aura_music_studio.revision_portal import router as revision_portal_router
from aura_music_studio.social_management_api import router as social_management_router
from aura_music_studio.social_management_portal import router as social_management_portal_router
from aura_music_studio.source_detection_api import router as source_detection_router
from aura_music_studio.system_api import router as system_router
from aura_music_studio.take_api import router as take_router
from aura_music_studio.take_portal import router as take_portal_router
from aura_music_studio.usage_tracking import CreativeUsageMiddleware
from aura_music_studio.vocal_api import router as vocal_router

# ``aura_music_studio.api`` already registers historical public/member/owner surfaces.
# Replace only the homepage, member dashboard and owner dashboard; existing sign-in,
# membership/payment, backup, compute and other action routes remain intact.
app.router.routes[:] = [
    route
    for route in app.router.routes
    if getattr(route, "path", None) not in {"/", "/dashboard", "/owner/dashboard"}
]
app.include_router(creative_portal_router)
app.include_router(member_dashboard_router)
app.include_router(aura_intelligence_router)

app.include_router(brand_router)
app.include_router(discovery_router)
app.include_router(compute_node_router)
app.include_router(creative_project_router)
app.include_router(creative_workspace_router)

# ESP niche selection must be registered before the legacy Command Center route so
# active ESP members enter the niche-personalised gateway first. Social-management
# routes are nested under /command-center and enforce ESP/niche/affiliation access
# independently at the API layer. Progress is private to ESP members and visible to
# the protected owner console.
app.include_router(esp_niche_portal_router)
app.include_router(esp_progress_portal_router)
app.include_router(social_management_router, include_in_schema=False)
app.include_router(social_management_portal_router)

# Mary/Kev owner command centre and user controls share the protected owner-session
# boundary. The owner identity is a separately signed context used for theme/Aura
# personalisation and the audit trail; selecting Mary or Kev never changes permissions.
app.include_router(owner_control_center_router)
app.include_router(owner_users_router)

app.include_router(daw_router)
app.include_router(daw_portal_router)
app.include_router(daw_recording_router)
app.include_router(daw_recording_ui_router)
app.include_router(daw_routing_router)
app.include_router(daw_routing_ui_router)
app.include_router(daw_mixer_ui_router)
app.include_router(vocal_router)
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

# Bind the signed Mary/Kev owner identity for owner routes so downstream owner actions
# receive the correct audit actor without trusting form fields.
app.add_middleware(OwnerIdentityMiddleware)

# Applied last so all legacy text emitted by older modules is rewritten at the public
# HTTP boundary. Binary media responses are not modified.
app.add_middleware(BrandMigrationMiddleware)

__all__ = ["app"]
