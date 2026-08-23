from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    monthly_price_usd: Decimal
    description: str
    confirmed_songs_per_day: int | None
    regeneration_until_confirmed: bool
    features: frozenset[str]
    studio_claims_output_ownership: bool = False

    def has(self, feature: str) -> bool:
        return feature in self.features

    def public_dict(self) -> dict:
        data = asdict(self)
        data["monthly_price_usd"] = str(self.monthly_price_usd)
        data["features"] = sorted(self.features)
        return data


# Stable entitlement keys. API/UI code checks these rather than price values.
BASIC_CREATE = "basic_create"
BASIC_LYRICS = "basic_lyrics"
BASIC_PREVIEW = "basic_preview"
AURA_SPEECH = "aura_speech"
PRODUCER_CHAT = "producer_chat"
INSTRUMENT_SELECTOR = "instrument_selector"
ADVANCED_INSTRUMENT_SELECTOR = "advanced_instrument_selector"
BASIC_FX = "basic_fx"
STANDARD_FX = "standard_fx"
ADVANCED_FX = "advanced_fx"
AI_FX_DESIGNER = "ai_fx_designer"
PLUGIN_RACK = "plugin_rack"
BASIC_AUTOTUNE = "basic_autotune"
STANDARD_AUTOTUNE = "standard_autotune"
ADVANCED_AUTOTUNE = "advanced_autotune"
AUTOMIX = "automix"
FULL_TRACK = "full_track"
BUILD_AROUND_UPLOAD = "build_around_upload"
UNLIMITED_REGEN_UNTIL_CONFIRMED = "unlimited_regen_until_confirmed"
MP3_DOWNLOAD = "mp3_download"
WAV_DOWNLOAD = "wav_download"
FLAC_DOWNLOAD = "flac_download"
BASIC_MASTERING = "basic_mastering"
ADVANCED_MASTERING = "advanced_mastering"
REFERENCE_MASTERING = "reference_mastering"
ALBUM_MASTERING = "album_mastering"
AUDIO_CLEANUP = "audio_cleanup"
UPLOAD_AUDIO = "upload_audio"
UPLOAD_SCORE = "upload_score"
BACKING_TRACK = "backing_track"
COVER_REMIX = "cover_remix"
REGION_REPAINT = "region_repaint"
BASIC_STEM_SPLITTER = "basic_stem_splitter"
STEM_SPLITTER = "stem_splitter"
STEM_DOWNLOAD = "stem_download"
MULTITRACK_DAW = "multitrack_daw"
TAKE_LANES = "take_lanes"
AUTOMATION = "automation"
REVISION_HISTORY = "revision_history"
DEEP_REVISION_HISTORY = "deep_revision_history"
SAMPLE_LAB = "sample_lab"
STYLE_DNA = "style_dna"
HARMONY_ARCHITECT = "harmony_architect"
APPROVED_VOICE_DUPLICATION = "approved_voice_duplication"
AUDIO_TO_MIDI_CONTROL = "audio_to_midi_control"
NEURAL_AMP = "neural_amp"
SPATIAL_AUDIO = "spatial_audio"
VIDEO_SYNC = "video_sync"

# Visual creation — available to ordinary Studio customers as part of the public creative product.
IMAGE_GENERATION = "image_generation"
POSTER_GENERATION = "poster_generation"
BASIC_IMAGE_EDIT = "basic_image_edit"
BASIC_VISUAL_FX = "basic_visual_fx"
ADVANCED_IMAGE_GENERATION = "advanced_image_generation"
IMAGE_HIGH_QUALITY = "image_high_quality"
IMAGE_PROVIDER_CONTROL = "image_provider_control"
IMAGE_ADVANCED_EDIT = "image_advanced_edit"
IMAGE_TRANSPARENT_BACKGROUND = "image_transparent_background"
IMAGE_LAYER_COMPOSITOR = "image_layer_compositor"
BRAND_KIT_COMPOSITOR = "brand_kit_compositor"
VISUAL_FX_STUDIO = "visual_fx_studio"
MULTITRACK_VISUAL_TIMELINE = "multitrack_visual_timeline"
VISUAL_KEYFRAMES = "visual_keyframes"
VISUAL_MASKING = "visual_masking"
VISUAL_BLEND_MODES = "visual_blend_modes"
VISUAL_MOTION_TRACKING = "visual_motion_tracking"
VISUAL_CHROMA_KEY = "visual_chroma_key"
VISUAL_BACKGROUND_REMOVAL = "visual_background_removal"
VISUAL_OBJECT_EDIT = "visual_object_edit"
VISUAL_COLOR_GRADING = "visual_color_grading"
VISUAL_SPEED_CURVES = "visual_speed_curves"
VISUAL_ADVANCED_CAPTIONS = "visual_advanced_captions"
VISUAL_ADVANCED_EXPORT = "visual_advanced_export"

VIDEO_GENERATION = "video_generation"
ADVANCED_VIDEO_GENERATION = "advanced_video_generation"
VIDEO_DIRECTOR = "video_director"
VIDEO_TO_VIDEO = "video_to_video"
VIDEO_EXTENDED_DURATION = "video_extended_duration"
VIDEO_HIGH_QUALITY = "video_high_quality"
VIDEO_PROVIDER_CONTROL = "video_provider_control"
VIDEO_ADVANCED_EXPORT = "video_advanced_export"
BANDLAB_EXPORT = "bandlab_export"
PRIORITY_QUEUE = "priority_queue"
UNLIMITED_CONFIRMED_SONGS = "unlimited_confirmed_songs"


FREE_FEATURES = frozenset({
    BASIC_CREATE,
    BASIC_LYRICS,
    BASIC_PREVIEW,
    PRODUCER_CHAT,
    AURA_SPEECH,
    INSTRUMENT_SELECTOR,
    BASIC_FX,
    BASIC_AUTOTUNE,
    BASIC_MASTERING,
})

BASE_FEATURES = FREE_FEATURES | frozenset({
    FULL_TRACK,
    BUILD_AROUND_UPLOAD,
    UNLIMITED_REGEN_UNTIL_CONFIRMED,
    MP3_DOWNLOAD,
    WAV_DOWNLOAD,
    STANDARD_FX,
    STANDARD_AUTOTUNE,
    AUTOMIX,
    BASIC_STEM_SPLITTER,
    AUDIO_CLEANUP,
    UPLOAD_AUDIO,
    UPLOAD_SCORE,
    BACKING_TRACK,
    HARMONY_ARCHITECT,
    REVISION_HISTORY,
    VIDEO_GENERATION,
    IMAGE_GENERATION,
    POSTER_GENERATION,
    BASIC_IMAGE_EDIT,
    BASIC_VISUAL_FX,
})

PRO_FEATURES = BASE_FEATURES | frozenset({
    UNLIMITED_CONFIRMED_SONGS,
    ADVANCED_INSTRUMENT_SELECTOR,
    ADVANCED_FX,
    AI_FX_DESIGNER,
    PLUGIN_RACK,
    ADVANCED_AUTOTUNE,
    FLAC_DOWNLOAD,
    ADVANCED_MASTERING,
    REFERENCE_MASTERING,
    ALBUM_MASTERING,
    COVER_REMIX,
    REGION_REPAINT,
    STEM_SPLITTER,
    STEM_DOWNLOAD,
    MULTITRACK_DAW,
    TAKE_LANES,
    AUTOMATION,
    DEEP_REVISION_HISTORY,
    SAMPLE_LAB,
    STYLE_DNA,
    APPROVED_VOICE_DUPLICATION,
    AUDIO_TO_MIDI_CONTROL,
    NEURAL_AMP,
    SPATIAL_AUDIO,
    VIDEO_SYNC,
    ADVANCED_VIDEO_GENERATION,
    VIDEO_DIRECTOR,
    VIDEO_TO_VIDEO,
    VIDEO_EXTENDED_DURATION,
    VIDEO_HIGH_QUALITY,
    VIDEO_PROVIDER_CONTROL,
    VIDEO_ADVANCED_EXPORT,
    ADVANCED_IMAGE_GENERATION,
    IMAGE_HIGH_QUALITY,
    IMAGE_PROVIDER_CONTROL,
    IMAGE_ADVANCED_EDIT,
    IMAGE_TRANSPARENT_BACKGROUND,
    IMAGE_LAYER_COMPOSITOR,
    BRAND_KIT_COMPOSITOR,
    VISUAL_FX_STUDIO,
    MULTITRACK_VISUAL_TIMELINE,
    VISUAL_KEYFRAMES,
    VISUAL_MASKING,
    VISUAL_BLEND_MODES,
    VISUAL_MOTION_TRACKING,
    VISUAL_CHROMA_KEY,
    VISUAL_BACKGROUND_REMOVAL,
    VISUAL_OBJECT_EDIT,
    VISUAL_COLOR_GRADING,
    VISUAL_SPEED_CURVES,
    VISUAL_ADVANCED_CAPTIONS,
    VISUAL_ADVANCED_EXPORT,
    BANDLAB_EXPORT,
    PRIORITY_QUEUE,
})


PLANS: dict[str, Plan] = {
    "free": Plan(
        id="free",
        name="Free",
        monthly_price_usd=Decimal("0.00"),
        description=(
            "Explore Aura songwriting/producer help, spoken control, basic previews, the core instrument selector, "
            "starter FX, basic Aura Tune and basic mastering. Full music, video and image/poster creation unlock on Base."
        ),
        confirmed_songs_per_day=0,
        regeneration_until_confirmed=False,
        features=FREE_FEATURES,
    ),
    "base": Plan(
        id="base",
        name="Base",
        monthly_price_usd=Decimal("4.99"),
        description=(
            "Core Live Sound Studio creation: one confirmed full track every day with unlimited regenerations until confirmation, "
            "upload-to-song production, MP3/WAV, standard instruments and FX, Aura Tune, AutoMix, useful stem splitting, mastering, "
            "cleanup, backing tracks and harmony tools; standard AI video creation; and genuine AI image/poster generation with core "
            "visual editing and effects."
        ),
        confirmed_songs_per_day=1,
        regeneration_until_confirmed=True,
        features=BASE_FEATURES,
    ),
    "pro": Plan(
        id="pro",
        name="Pro",
        monthly_price_usd=Decimal("9.99"),
        description=(
            "The complete professional Live Sound Studio. Includes unlimited full-track creation plus advanced controls across music, "
            "audio, video, image and poster workflows: expanded instruments, multitrack production, full FX, advanced Aura Tune, detailed "
            "stems, automation, deep revisions, advanced mastering, Sample Lab, Style DNA, voice tools, Aura Video Director, video-to-video, "
            "extended/high-quality video rendering, advanced image generation/editing, transparent outputs, branded poster composition, "
            "and the Visual FX Studio with a layered timeline, keyframes, masks, blend modes, motion tracking, chroma key, AI background "
            "and object editing, professional color grading, speed curves, advanced captions/exports and priority processing."
        ),
        confirmed_songs_per_day=None,
        regeneration_until_confirmed=True,
        features=PRO_FEATURES,
    ),
}


def get_plan(plan_id: str) -> Plan:
    key = (plan_id or "").strip().lower()
    if key not in PLANS:
        raise ValueError(f"Unknown plan: {plan_id}")
    return PLANS[key]


def require_feature(plan_id: str, feature: str) -> None:
    plan = get_plan(plan_id)
    if not plan.has(feature):
        raise PermissionError(f"{feature} requires a higher Live Sound Studio membership tier")


def public_plans() -> list[dict]:
    return [PLANS[k].public_dict() for k in ("free", "base", "pro")]


OWNERSHIP_NOTICE = (
    "The Live Sound Studio and Elevate Souls Productions do not claim ownership of a member's original "
    "inputs or eligible generated outputs. Rights in AI-assisted outputs remain subject to applicable law, "
    "the licences of underlying open models, and any third-party/source-material rights. Members must have "
    "the rights required for material they upload or ask the Studio to transform."
)
