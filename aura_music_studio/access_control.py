from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .accounts import AccountStore
from .membership import MembershipService
from .plans import (
    APPROVED_VOICE_DUPLICATION,
    AUDIO_CLEANUP,
    AUDIO_TO_MIDI_CONTROL,
    AURA_SPEECH,
    BASIC_CREATE,
    BASIC_MASTERING,
    BASIC_TIMELINE,
    FULL_TRACK,
    HARMONY_ARCHITECT,
    MP3_DOWNLOAD,
    MULTITRACK_DAW,
    NEURAL_AMP,
    PRODUCER_CHAT,
    REGION_REPAINT,
    SAMPLE_LAB,
    SPATIAL_AUDIO,
    STEM_SPLITTER,
    STYLE_DNA,
    UPLOAD_AUDIO,
    VIDEO_SYNC,
    WAV_DOWNLOAD,
)
from .request_context import reset_current_user_id, set_current_user_id

PUBLIC_EXACT = {
    "/", "/pricing", "/signup", "/signin", "/signout", "/dashboard", "/studio",
    "/health", "/plans", "/membership/review", "/membership/decision",
    "/membership/payment", "/docs", "/redoc", "/openapi.json", "/favicon.webp",
    "/robots.txt", "/sitemap.xml", "/manifest.webmanifest", "/service-worker.js",
    "/ai-music-studio", "/ai-song-generator", "/backing-track-maker", "/stem-splitter",
    "/ai-mastering", "/ai-vocal-studio",
}
# Privacy endpoints authenticate themselves with a valid session but deliberately do not require
# an active paid/free entitlement. Brand assets remain public. ESP compute-node endpoints bypass
# member authentication because every node operation performs its own node-specific credential check.
PUBLIC_PREFIXES = (
    "/auth/", "/admin/", "/owner", "/privacy/", "/brand/", "/node-coordinator/",
)


def _token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get("lss_session")


def _required_feature(path: str, method: str) -> str | None:
    if path == "/songs" and method == "POST":
        return BASIC_CREATE
    if path.startswith("/speech/"):
        return AURA_SPEECH
    if path.startswith("/web/"):
        return PRODUCER_CHAT
    if "/producer" in path:
        return PRODUCER_CHAT
    if path.endswith("/produce") or path.endswith("/render-jobs"):
        return FULL_TRACK
    if "/daw/" in path or path.endswith("/daw"):
        return BASIC_TIMELINE
    if path.endswith("/region-edit"):
        return REGION_REPAINT
    if path.endswith("/add-generated-track"):
        return MULTITRACK_DAW
    if path.endswith("/harmonies"):
        return HARMONY_ARCHITECT
    if path.endswith("/voice-convert") or path.endswith("/voice-profiles") or path.endswith("/voices"):
        return APPROVED_VOICE_DUPLICATION
    if path.endswith("/restore"):
        return AUDIO_CLEANUP
    if path.endswith("/neural-amp"):
        return NEURAL_AMP
    if path.endswith("/spatial"):
        return SPATIAL_AUDIO
    if path.endswith("/video-sync"):
        return VIDEO_SYNC
    if "/separate" in path:
        return STEM_SPLITTER
    if "/sample/" in path:
        return SAMPLE_LAB
    if path.endswith("/style-blend"):
        return STYLE_DNA
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
    """Server-side membership, entitlement and tenant-boundary enforcement."""

    def __init__(self, app):
        super().__init__(app)
        self.store = AccountStore()
        self.memberships = MembershipService(self.store)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS" or path in PUBLIC_EXACT or any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
            return await call_next(request)

        session_token = _token(request)
        try:
            member = self.memberships.from_session(session_token, require_active=True)
        except PermissionError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=401)

        context_token = set_current_user_id(member.user_id)
        request.state.member = member
        try:
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

            if path.endswith("/download"):
                requested = (request.query_params.get("path") or "").lower()
                if requested.endswith(".mp3"):
                    needed = MP3_DOWNLOAD
                elif requested.endswith(".wav") and "stem" not in requested and "bandlab" not in requested:
                    needed = WAV_DOWNLOAD
                else:
                    needed = STEM_SPLITTER
                if not member.plan.has(needed):
                    return JSONResponse(
                        {"detail": "This download requires a higher membership tier", "upgrade_required": True},
                        status_code=403,
                    )

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
                    pass
            return response
        finally:
            reset_current_user_id(context_token)
