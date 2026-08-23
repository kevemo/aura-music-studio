"""Production entrypoint for Elevate Souls Productions Presents: The Live Sound Studio.

Run locally:
    uvicorn app:app --host 0.0.0.0 --port 8000
"""

from aura_music_studio.api import app
from aura_music_studio.brand_ui import router as brand_router
from aura_music_studio.edit_api import router as edit_router
from aura_music_studio.engineering_job_api import router as engineering_job_router
from aura_music_studio.esp_command_center import router as esp_command_center_router
from aura_music_studio.output_api import router as output_router
from aura_music_studio.privacy_api import router as privacy_router
from aura_music_studio.production_portal import router as production_portal_router
from aura_music_studio.production_suite_api import router as production_suite_router
from aura_music_studio.recording_api import router as recording_router
from aura_music_studio.recording_portal import router as recording_portal_router
from aura_music_studio.revision_api import router as revision_router
from aura_music_studio.revision_portal import router as revision_portal_router
from aura_music_studio.source_detection_api import router as source_detection_router
from aura_music_studio.system_api import router as system_router
from aura_music_studio.take_api import router as take_router
from aura_music_studio.take_portal import router as take_portal_router
from aura_music_studio.video_api import router as video_router
from aura_music_studio.vocal_api import router as vocal_router

# Modular advanced routers share the core app's authentication, plan enforcement and tenant isolation.
# Brand routes are public static assets shared by every customer/owner portal.
app.include_router(brand_router)
app.include_router(vocal_router)
app.include_router(video_router)
app.include_router(edit_router)
app.include_router(engineering_job_router)
app.include_router(esp_command_center_router)
app.include_router(recording_router)
app.include_router(output_router)
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

__all__ = ["app"]
