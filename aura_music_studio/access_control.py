from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .accounts import AccountStore
from .membership import MembershipService
from .plans import (
    APPROVED_VOICE_DUPLICATION,
    AUDIO_TO_MIDI_CONTROL,
    BASIC_CREATE,
    BASIC_MASTERING,
    FULL_TRACK,
    MP3_DOWNLOAD,
    MULTITRACK_DAW,
    PRODUCER_CHAT,
    SAMPLE_LAB,
    STEM_SPLITTER,
    STYLE_DNA,
    UPLOAD_AUDIO,
    WAV_DOWNLOAD,
)

# Exact website/account pages are public at the middleware boundary. Individual pages
# (such as /dashboard) still resolve their own session and never expose another user's data.
PUBLIC_EXACT = {
    "/",
    "/pricing",
    "/signup",
    "/signin",
    "/signout",
    "/dashboard",
    "/health",
    "/plans",
    "/membership/review",
    "/membership/decision",
    "/membership/payment",
    "/docs",
    "/redoc",
    "/openapi.json",
}
PUBLIC_PREFIXES = (
    "/auth/",
    "/admin/",
)


def _token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get("lss_session")


def _required_feature(path: str, method: str) -> str | None:
    if path == "/songs" and method == "POST":
        return BASIC_CREATE
    if "/producer" in path:
        return PRODUCER_CHAT
    if path.endswith("/produce"):
        return FULL_TRACK
    if "/separate" in path:
        return STEM_SPLITTER
    if "/sample/" in path:
        return SAMPLE_LAB
    if path.endswith("/style-blend"):
        return STYLE_DNA
    if path.endswith("/voice-profiles"):
        return APPROVED_VOICE_DUPLICATION
    if path.endswith("/transcribe"):
        return AUDIO_TO_MIDI_CONTROL
    if "/session" in path:
        return MULTITRACK_DAW
    if path.endswith("/master"):
        return BASIC_MASTERING
    if path.endswith("/assets") and method == "POST":
        return UPLOAD_AUDIO
    if path.endswith("/analyze"):
        return BASIC_CREATE
    return None


class MembershipAccessMiddleware(BaseHTTPMiddleware):
    """Server-side entitlement enforcement for the public product API.

    UI state is never trusted. Even if a member calls an endpoint directly, the plan's
    feature set and daily full-track policy are checked here.
    """

    def __init__(self, app):
        super().__init__(app)
        self.store = AccountStore()
        self.memberships = MembershipService(self.store)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            request.method == "OPTIONS"
            or path in PUBLIC_EXACT
            or any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)
        ):
            return await call_next(request)

        token = _token(request)
        try:
            member = self.memberships.from_session(token, require_active=True)
        except PermissionError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=401)

        feature = _required_feature(path, request.method)
        if feature and not member.plan.has(feature):
            return JSONResponse(
                {
                    "detail": f"{feature} is not included in the {member.plan.name} tier",
                    "plan": member.plan.id,
                    "upgrade_required": True,
                },
                status_code=403,
            )

        # Download policy: Base gets final MP3/WAV; Pro's broader feature set is checked
        # by extension/filename here. Free has no finished master downloads.
        if path.endswith("/download"):
            requested = (request.query_params.get("path") or "").lower()
            if requested.endswith(".mp3"):
                needed = MP3_DOWNLOAD
            elif requested.endswith(".wav") and "stem" not in requested and "bandlab" not in requested:
                needed = WAV_DOWNLOAD
            else:
                # stem archives, FLAC, BandLab packs and advanced assets are Pro territory.
                needed = STEM_SPLITTER
            if not member.plan.has(needed):
                return JSONResponse(
                    {"detail": "This download requires a higher membership tier", "upgrade_required": True},
                    status_code=403,
                )

        # Base: one confirmed full song per UTC day. A draft slot can be regenerated
        # repeatedly until the member explicitly confirms it. Pro is unlimited.
        project_id = None
        base_slot = None
        if request.method == "POST" and path.endswith("/produce"):
            try:
                project_id = path.split("/projects/", 1)[1].rsplit("/produce", 1)[0]
            except Exception:
                project_id = None
            if project_id and member.plan.confirmed_songs_per_day is not None:
                try:
                    base_slot = self.store.start_song_slot(
                        member.user_id,
                        project_id,
                        datetime.now(timezone.utc).date().isoformat(),
                    )
                except (PermissionError, ValueError) as exc:
                    return JSONResponse({"detail": str(exc)}, status_code=403)
                if base_slot.get("state") == "confirmed":
                    return JSONResponse(
                        {"detail": "This track has already been confirmed. Start a new daily project to create another finished song."},
                        status_code=403,
                    )

        response = await call_next(request)

        if project_id and base_slot and 200 <= response.status_code < 300:
            try:
                self.store.record_regeneration(member.user_id, project_id)
            except Exception:
                # Generation already succeeded; usage logging should not corrupt the audio response.
                pass
        return response
