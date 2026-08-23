"""Production entrypoint for Elevate Souls Productions Presents: The Live Sound Studio.

Run locally:
    uvicorn app:app --host 0.0.0.0 --port 8000

The FastAPI app serves the public website, membership/account system, ESP owner portal,
studio API, production queue, Aura Internet, spoken Aura and entitlement enforcement.
"""

from aura_music_studio.api import app
from aura_music_studio.vocal_api import router as vocal_router

# Vocal/harmony production is kept as a modular router so the consent-gated voice stack
# can evolve independently of the main Studio API surface.
app.include_router(vocal_router)

__all__ = ["app"]
