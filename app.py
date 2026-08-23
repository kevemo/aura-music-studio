"""Production entrypoint for Elevate Souls Productions Presents: The Live Sound Studio.

Run locally:
    uvicorn app:app --host 0.0.0.0 --port 8000
"""

from aura_music_studio.api import app
from aura_music_studio.edit_api import router as edit_router
from aura_music_studio.output_api import router as output_router
from aura_music_studio.privacy_api import router as privacy_router
from aura_music_studio.system_api import router as system_router
from aura_music_studio.vocal_api import router as vocal_router

# Modular advanced routers share the core app's authentication, plan enforcement and tenant isolation.
app.include_router(vocal_router)
app.include_router(edit_router)
app.include_router(output_router)
app.include_router(privacy_router)
app.include_router(system_router)

__all__ = ["app"]
