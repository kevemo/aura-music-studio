"""Production entrypoint for Elevate Souls Productions Presents: The Live Sound Studio.

Run locally:
    uvicorn app:app --host 0.0.0.0 --port 8000

The FastAPI app serves the public website, membership/account system, ESP owner portal,
studio API and entitlement enforcement from one process.
"""

from aura_music_studio.api import app

__all__ = ["app"]
