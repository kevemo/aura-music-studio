"""Production entrypoint for Pulsar-Frequency House.

Run locally:
    uvicorn app:app --host 0.0.0.0 --port 8000

The legacy ``aura_music_studio`` Python package name is retained as a compatibility
identifier for existing installs, project data and deployment configuration.
"""

from aura_music_studio.api import app
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
from aura_music_studio.output_api import router as output_router
from aura_music_studio.owner_backup_portal import router as owner_backup_router
from aura_music_studio.owner_compute_portal import router as owner_compute_router
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
from aura_music_studio.vocal_api import router as vocal_router

# ``aura_music_studio.api`` already registers the legacy public homepage. Remove only
# that route and replace it with the Pulsar-Frequency House master landing page.
# Pricing, sign-in, membership, studio and API routes remain intact.
app.router.routes[:] = [
    route for route in app.router.routes if getattr(route, "path", None) != "/"
]
app.include_router(creative_portal_router)

app.include_router(brand_router)
app.include_router(discovery_router)
app.include_router(compute_node_router)
app.include_router(creative_project_router)
app.include_router(creative_workspace_router)
app.include_router(social_management_router)
app.include_router(social_management_portal_router)
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

# Applied last so all legacy text emitted by older modules is rewritten at the public
# HTTP boundary. Binary media responses are not modified.
app.add_middleware(BrandMigrationMiddleware)

__all__ = ["app"]
