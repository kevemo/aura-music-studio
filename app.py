"""Production entrypoint for Elevate Souls Productions Presents: The Live Sound Studio.

Run locally:
    uvicorn app:app --host 0.0.0.0 --port 8000

The FastAPI app serves the public website, membership/account system, ESP owner portal,
studio API, production queue, Aura Internet, spoken Aura and entitlement enforcement.
"""

from aura_music_studio.api import app
from aura_music_studio.system_api import router as system_router
from aura_music_studio.vocal_api import router as vocal_router

# Modular advanced routers are mounted here so specialist subsystems can evolve independently
# while retaining the core app's server-side authentication, entitlement and tenant middleware.
app.include_router(vocal_router)
app.include_router(system_router)

__all__ = ["app"]
