from __future__ import annotations

from dataclasses import dataclass, asdict
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


# Stable entitlement keys. API/UI code should check these instead of price values.
BASIC_CREATE = "basic_create"
BASIC_LYRICS = "basic_lyrics"
BASIC_PREVIEW = "basic_preview"
FULL_TRACK = "full_track"
UNLIMITED_REGEN_UNTIL_CONFIRMED = "unlimited_regen_until_confirmed"
MP3_DOWNLOAD = "mp3_download"
WAV_DOWNLOAD = "wav_download"
FLAC_DOWNLOAD = "flac_download"
BASIC_MASTERING = "basic_mastering"
ADVANCED_MASTERING = "advanced_mastering"
REFERENCE_MASTERING = "reference_mastering"
UPLOAD_AUDIO = "upload_audio"
UPLOAD_SCORE = "upload_score"
BACKING_TRACK = "backing_track"
COVER_REMIX = "cover_remix"
REGION_REPAINT = "region_repaint"
STEM_SPLITTER = "stem_splitter"
STEM_DOWNLOAD = "stem_download"
MULTITRACK_DAW = "multitrack_daw"
TAKE_LANES = "take_lanes"
AUTOMATION = "automation"
SAMPLE_LAB = "sample_lab"
STYLE_DNA = "style_dna"
HARMONY_ARCHITECT = "harmony_architect"
APPROVED_VOICE_DUPLICATION = "approved_voice_duplication"
AUDIO_TO_MIDI_CONTROL = "audio_to_midi_control"
PRODUCER_CHAT = "producer_chat"
BANDLAB_EXPORT = "bandlab_export"
PRIORITY_QUEUE = "priority_queue"
UNLIMITED_CONFIRMED_SONGS = "unlimited_confirmed_songs"


FREE_FEATURES = frozenset({
    BASIC_CREATE,
    BASIC_LYRICS,
    BASIC_PREVIEW,
    PRODUCER_CHAT,
})

BASE_FEATURES = FREE_FEATURES | frozenset({
    FULL_TRACK,
    UNLIMITED_REGEN_UNTIL_CONFIRMED,
    MP3_DOWNLOAD,
    WAV_DOWNLOAD,
    BASIC_MASTERING,
    UPLOAD_AUDIO,
    UPLOAD_SCORE,
    BACKING_TRACK,
    HARMONY_ARCHITECT,
})

PRO_FEATURES = BASE_FEATURES | frozenset({
    UNLIMITED_CONFIRMED_SONGS,
    FLAC_DOWNLOAD,
    ADVANCED_MASTERING,
    REFERENCE_MASTERING,
    COVER_REMIX,
    REGION_REPAINT,
    STEM_SPLITTER,
    STEM_DOWNLOAD,
    MULTITRACK_DAW,
    TAKE_LANES,
    AUTOMATION,
    SAMPLE_LAB,
    STYLE_DNA,
    APPROVED_VOICE_DUPLICATION,
    AUDIO_TO_MIDI_CONTROL,
    BANDLAB_EXPORT,
    PRIORITY_QUEUE,
})


PLANS: dict[str, Plan] = {
    "free": Plan(
        id="free",
        name="Free",
        monthly_price_usd=Decimal("0.00"),
        description=(
            "Basic Live Sound Studio access: create song ideas, use Aura's basic lyric/producer tools "
            "and generate basic previews. Full finished-track allowance and premium studio tools are not included."
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
            "One confirmed full track every day. Regenerate that day's track as many times as needed "
            "until you confirm the result, with MP3/WAV download, basic mastering, uploads, backing-track "
            "creation and harmony tools."
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
            "Unlimited full-track creation and the complete Live Sound Studio: unlimited regeneration, "
            "downloads, splitter/stems, multitrack DAW, mastering, reference tools, covers/remixes, Sample Lab, "
            "Style DNA, Harmony Architect, approved voice duplication, automation and every enabled studio feature."
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
