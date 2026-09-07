from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal

from .native_products import AURA_OS_ENTITLEMENT, AURA_SEC_ENTITLEMENT, BillingPeriod


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    monthly_price: Decimal
    currency: str
    description: str
    confirmed_songs_per_day: int | None
    regeneration_until_confirmed: bool
    image_poster_creations_per_day: int | None
    features: frozenset[str]
    studio_claims_output_ownership: bool = False
    annual_price: Decimal | None = None

    def has(self, feature: str) -> bool:
        return feature in self.features

    def price_for(self, period: BillingPeriod | str = BillingPeriod.MONTHLY) -> Decimal:
        try:
            billing_period = BillingPeriod(period)
        except ValueError as exc:
            raise ValueError(f"Unsupported billing period: {period}") from exc
        if billing_period is BillingPeriod.MONTHLY:
            return self.monthly_price
        if self.annual_price is None:
            raise ValueError(f"Annual billing is not available for plan: {self.id}")
        return self.annual_price

    def price_minor_for(self, period: BillingPeriod | str = BillingPeriod.MONTHLY) -> int:
        return int((self.price_for(period) * Decimal("100")).to_integral_value())

    @property
    def monthly_price_usd(self) -> Decimal:
        """Deprecated compatibility alias.

        Historic code named the price field USD even though the authoritative public prices
        are GBP. Keep the attribute temporarily so parallel feature branches and persisted
        integrations do not break while new code uses monthly_price + currency.
        """
        return self.monthly_price

    @property
    def monthly_price_gbp(self) -> Decimal:
        if self.currency != "GBP":
            raise ValueError("This plan is not priced in GBP")
        return self.monthly_price

    @property
    def monthly_price_minor(self) -> int:
        return self.price_minor_for(BillingPeriod.MONTHLY)

    @property
    def annual_price_minor(self) -> int | None:
        if self.annual_price is None:
            return None
        return self.price_minor_for(BillingPeriod.ANNUAL)

    @property
    def currency_symbol(self) -> str:
        return {"GBP": "£", "USD": "$", "EUR": "€"}.get(self.currency, self.currency + " ")

    @property
    def display_price(self) -> str:
        if self.monthly_price == 0:
            return "Free"
        return f"{self.currency_symbol}{self.monthly_price}"

    def display_price_for(self, period: BillingPeriod | str = BillingPeriod.MONTHLY) -> str:
        price = self.price_for(period)
        if price == 0:
            return "Free"
        billing_period = BillingPeriod(period)
        suffix = "/month" if billing_period is BillingPeriod.MONTHLY else "/year"
        return f"{self.currency_symbol}{price}{suffix}"

    def public_dict(self) -> dict:
        data = asdict(self)
        data["monthly_price"] = str(self.monthly_price)
        data["monthly_price_minor"] = self.monthly_price_minor
        data["annual_price"] = str(self.annual_price) if self.annual_price is not None else None
        data["annual_price_minor"] = self.annual_price_minor
        data["supported_billing_periods"] = [BillingPeriod.MONTHLY.value]
        if self.annual_price is not None:
            data["supported_billing_periods"].append(BillingPeriod.ANNUAL.value)
        data["display_price"] = self.display_price
        # Deprecated output alias for clients built before the GBP schema correction.
        data["monthly_price_usd"] = str(self.monthly_price)
        data["features"] = sorted(self.features)
        data["image_poster_creations_unlimited"] = self.image_poster_creations_per_day is None
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
IMAGE_POSTER_CREATE = "image_poster_create"
IMAGE_POSTER_DOWNLOAD = "image_poster_download"
MUSIC_VIDEO_DOWNLOAD = "music_video_download"
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
BASIC_TIMELINE = "basic_timeline"
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
BANDLAB_EXPORT = "bandlab_export"
PRIORITY_QUEUE = "priority_queue"
UNLIMITED_CONFIRMED_SONGS = "unlimited_confirmed_songs"
GAME_PLAYTEST = "game_playtest"
GAME_CREATE = "game_create"
GAME_CREATE_UNLIMITED = "game_create_unlimited"

# Command Center memberships and standalone native products share one canonical entitlement
# vocabulary so a subscription grant cannot diverge from the native-device authorization layer.
AURA_OS = AURA_OS_ENTITLEMENT
AURASEC = AURA_SEC_ENTITLEMENT


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
    IMAGE_POSTER_CREATE,
    IMAGE_POSTER_DOWNLOAD,
    GAME_PLAYTEST,
})

# Keep the internal BASE_* identifier and the public plan id "base" for backwards
# compatibility. Public catalogue presentation projects this tier as "Basic".
BASE_FEATURES = FREE_FEATURES | frozenset({
    FULL_TRACK,
    BUILD_AROUND_UPLOAD,
    UNLIMITED_REGEN_UNTIL_CONFIRMED,
    MP3_DOWNLOAD,
    WAV_DOWNLOAD,
    MUSIC_VIDEO_DOWNLOAD,
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
    BASIC_TIMELINE,
    GAME_CREATE,
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
    HARMONY_ARCHITECT,
    APPROVED_VOICE_DUPLICATION,
    AUDIO_TO_MIDI_CONTROL,
    NEURAL_AMP,
    SPATIAL_AUDIO,
    VIDEO_SYNC,
    BANDLAB_EXPORT,
    PRIORITY_QUEUE,
    GAME_CREATE_UNLIMITED,
    AURA_OS,
    AURASEC,
})


PLANS: dict[str, Plan] = {
    "free": Plan(
        id="free",
        name="Free",
        monthly_price=Decimal("0.00"),
        currency="GBP",
        description=(
            "Explore Aura songwriting/producer help and core creative tools. Image and poster creation includes up to "
            "5 generated outputs per day, and those image/poster outputs can be saved and downloaded. Free members can "
            "also play and test Game Forge builds that have passed the platform's public playtest safety/rating preflight. "
            "Music/video downloads and game creation unlock on Basic."
        ),
        confirmed_songs_per_day=0,
        regeneration_until_confirmed=False,
        image_poster_creations_per_day=5,
        features=FREE_FEATURES,
    ),
    "base": Plan(
        id="base",
        name="Basic",
        monthly_price=Decimal("4.99"),
        currency="GBP",
        description=(
            "£4.99/month Basic tier with increased creative access, project editing and enabled Music, Video and Game capabilities. "
            "The authoritative cross-studio daily allowance and any Cosmic Creation Coin overage are enforced separately "
            "by server-side usage/admission controls; this plan object defines feature entitlement rather than inventing "
            "a second usage counter. Includes upload-to-song production, MP3/WAV, standard instrument choices and FX, Aura "
            "Tune, AutoMix, useful stem splitting, mastering, cleanup, backing-track creation, harmony tools, project "
            "revision history, basic waveform timeline editing, and enabled Game Forge creation/editing."
        ),
        confirmed_songs_per_day=1,
        regeneration_until_confirmed=True,
        image_poster_creations_per_day=10,
        features=BASE_FEATURES,
    ),
    "pro": Plan(
        id="pro",
        name="Unlimited Pro",
        monthly_price=Decimal("9.99"),
        annual_price=Decimal("99.00"),
        currency="GBP",
        description=(
            "£9.99/month or £99/year Unlimited Pro tier with the highest enabled creative access, Aura OS Assistant/Overlay "
            "and Aura Sec included. Normal use is effectively unlimited subject to fair-use, infrastructure, provider-capacity, "
            "rate-control, anti-abuse and safety safeguards. Includes the complete enabled production stack: expanded instrument/"
            "performance types, editable multitrack build-around production, full FX banks, Aura AI FX Designer, owner-approved "
            "native plugin racks, advanced/custom Aura Tune, detailed splitter/stem downloads, visual multitrack DAW, take lanes, "
            "automation and deep revision history, advanced/reference/album mastering, Sample Lab, Style DNA, covers/remixes/repaint, "
            "Harmony Architect, consent-approved voice duplication, neural amp processing, immersive spatial audio, video/music sync, "
            "enabled export formats, and unlimited active Game Forge project workspaces. Eligible song/game publishing remains subject "
            "to marketplace entitlement, rights and governance gates."
        ),
        confirmed_songs_per_day=None,
        regeneration_until_confirmed=True,
        image_poster_creations_per_day=None,
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
        raise PermissionError(f"{feature} requires a higher Command Center membership tier")


def public_plans() -> list[dict]:
    return [PLANS[k].public_dict() for k in ("free", "base", "pro")]


OWNERSHIP_NOTICE = (
    "Elevate Souls Productions Content Creation Command Center and Elevate Souls Productions do not claim ownership of a "
    "member's original inputs or eligible generated outputs. Rights in AI-assisted outputs remain subject to applicable "
    "law, the licences of underlying open models, and any third-party/source-material rights. Members must have the rights "
    "required for material they upload or ask the Studio to transform."
)
