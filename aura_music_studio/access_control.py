from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import unquote

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
    get_plan,
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
PUBLIC_PREFIXES = (
    "/auth/", "/admin/", "/owner", "/privacy/", "/brand/", "/node-coordinator/",
    # Shared Sky public Creator identity is an explicit read-only allow-listed route family.
    # The endpoint itself exposes only public profile fields and returns 404 for inactive,
    # revoked, non-Creator or non-discoverable accounts.
    "/shared-sky/public/",
    # Browser/link sources in TikTok LIVE Studio and OBS do not share the member's site session.
    # These routes are public only at the middleware layer; every request is authenticated by a
    # high-entropy, rotatable source token stored only as a SHA-256 digest server-side.
    "/live-overlay/source/",
    # Stripe checkout must also work for owner-approved accounts that are not active yet,
    # and Stripe webhooks have no member session. Every state-changing Stripe route therefore
    # performs its own session or cryptographic webhook verification before mutating state.
    "/billing/stripe/",
)


def _token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get("lss_session")


def _required_feature(path: str, method: str) -> str | None:
    if path == "/songs" and method == "POST":
        return BASIC_CREATE
    if path.startswith("/image-effects/") or ("/image-effects/" in path and path.startswith("/projects/")):
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

    # Voice House identity creation is a server-authoritative premium operation. Keep these
    # checks method-specific: an account downgrade must never prevent a user from listing,
    # inspecting, revoking or deleting an existing private identity profile. Consent withdrawal
    # and erasure are safety/privacy controls, not paid generation features.
    if method == "POST" and (
        path.endswith("/voice-house/challenge")
        or path.endswith("/voice-house/profiles")
    ):
        return APPROVED_VOICE_DUPLICATION

    # Preserve the established premium boundary for the legacy/current execution surfaces.
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


def _base_daw_project_requires_pro(path: str) -> bool:
    """Detect advanced DAW state left by a previous Pro membership.

    Base may keep every project file after downgrade, but multitrack, take lanes, automation,
    auxiliary routing and frozen-track state remain Pro-only and cannot be operated through Base.
    """
    if "/projects/" not in path or "/daw" not in path:
        return False
    try:
        encoded = path.split("/projects/", 1)[1].split("/daw", 1)[0]
        project_name = unquote(encoded).strip("/")
        if not project_name:
            return False
        from .tenant_storage import project_path
        from .session import StudioSession

        project = project_path(project_name, must_exist=True)
        session_file = project / "aura_session.json"
        if not session_file.is_file():
            return False
        session = StudioSession.load(session_file)
        ordinary = [track for track in session.tracks if track.role not in {"master", "bus"}]
        buses = [track for track in session.tracks if track.role == "bus"]
        if len(ordinary) > 1 or buses:
            return True
        for track in ordinary:
            if track.automation or track.sends or track.metadata.get("frozen"):
                return True
            if any(clip.take_lane > 0 for clip in track.clips if clip.kind == "audio"):
                return True
        return False
    except Exception:
        return False


class MembershipAccessMiddleware(BaseHTTPMiddleware):
    """Server-side membership, entitlement and tenant-boundary enforcement."""

    def __init__(self, app):
        super().__init__(app)
        self.store = AccountStore()
        self.memberships = MembershipService(self.store)

    def _live_overlay_speech_denial(self, path: str, method: str) -> JSONResponse | None:
        """Revalidate paid LIVE speech at execution time for bearer browser sources.

        Browser-source URLs intentionally have no member session. A saved overlay profile must
        therefore never act as a durable paid entitlement after the owning account is downgraded
        or its paid period expires. Token authentication remains inside the overlay route; this
        guard only applies after a valid token resolves to an account.
        """
        if method != "POST" or not path.startswith("/live-overlay/source/") or not path.endswith("/speech"):
            return None
        raw_token = path[len("/live-overlay/source/") : -len("/speech")].strip("/")
        if not raw_token:
            return None
        try:
            from .aura_live_overlay_studio import _profile, _user_for_source

            user_id = _user_for_source(unquote(raw_token))
            profile = _profile(user_id)
        except Exception:
            # Preserve the source route's own token/not-found response without turning this
            # entitlement guard into a second source-authentication implementation.
            return None

        user = self.store.get_user(user_id)
        if not user:
            return JSONResponse({"detail": "Overlay source owner not found"}, status_code=404)
        current_user = self.memberships.subscriptions.enforce(user)
        if current_user.get("status") != "active":
            return JSONResponse(
                {"detail": "Active membership is required for server LIVE speech"},
                status_code=403,
            )
        plan = get_plan(current_user.get("plan_id") or "free")
        mode = str(profile.get("voice_mode") or "browser").lower()
        if mode == "aura" and plan.id not in {"base", "pro"}:
            return JSONResponse(
                {
                    "detail": "Aura overlay voice requires a current Basic or Pro membership",
                    "plan": plan.id,
                    "upgrade_required": True,
                },
                status_code=403,
            )
        if mode == "clone" and (plan.id != "pro" or not plan.has(APPROVED_VOICE_DUPLICATION)):
            return JSONResponse(
                {
                    "detail": "Consent-approved cloned LIVE voice requires a current Pro membership",
                    "plan": plan.id,
                    "upgrade_required": True,
                },
                status_code=403,
            )
        return None

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        live_speech_denial = self._live_overlay_speech_denial(path, request.method)
        if live_speech_denial is not None:
            return live_speech_denial
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

            if not member.plan.has(MULTITRACK_DAW) and _base_daw_project_requires_pro(path):
                return JSONResponse(
                    {
                        "detail": "This project contains Pro multitrack, take-lane, automation, routing or frozen-track state. Upgrade to Pro to reopen its advanced DAW session.",
                        "plan": member.plan.id,
                        "upgrade_required": True,
                        "project_preserved": True,
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
