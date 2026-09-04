from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class EffectBand:
    id: str
    label: str
    coin_price: int
    description: str

    def public(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DiscoverySection:
    id: str
    label: str
    description: str

    def public(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StudioMenu:
    id: str
    domain: str
    label: str
    description: str
    feature_families: tuple[str, ...]
    search_terms: tuple[str, ...]
    ai_composable: bool = True
    graph_editable: bool = True

    def public(self) -> dict:
        row = asdict(self)
        row["feature_families"] = list(self.feature_families)
        row["search_terms"] = list(self.search_terms)
        return row


def _d(key: str, label: str, description: str) -> DiscoverySection:
    return DiscoverySection(id=f"discover.{key}", label=label, description=description)


def _m(
    domain: str,
    key: str,
    label: str,
    description: str,
    families: Iterable[str],
    terms: Iterable[str],
    *,
    ai_composable: bool = True,
    graph_editable: bool = True,
) -> StudioMenu:
    return StudioMenu(
        id=f"studio.{domain}.{key}",
        domain=domain,
        label=label,
        description=description,
        feature_families=tuple(families),
        search_terms=tuple(terms),
        ai_composable=ai_composable,
        graph_editable=graph_editable,
    )


EFFECT_BANDS = (
    EffectBand("core", "Core / Free", 0, "First-party effects available without a Coin purchase where configured."),
    EffectBand("silver", "Silver", 200, "Premium first-party effect band priced at 200 Cosmic Creation Coins."),
    EffectBand("gold", "Gold", 500, "Advanced first-party effect or reusable system band priced at 500 Cosmic Creation Coins."),
)

DISCOVERY_SECTIONS = (
    _d("search", "Search", "Search names, categories, tags, compatible media, moods, genres and workflows."),
    _d("trending", "Trending", "Currently popular first-party and user-authorised catalogue items."),
    _d("new", "New", "Recently released original effects, systems, templates and tools."),
    _d("recommended", "Recommended", "Project-aware recommendations based on media type and current workflow."),
    _d("free", "Free", "Core effects and systems available without a Coin purchase."),
    _d("silver", "Silver", "Effects assigned to the 200-Coin premium band."),
    _d("gold", "Gold", "Advanced effects and systems assigned to the 500-Coin premium band."),
    _d("owned", "Owned", "Account-authorised effects and systems already unlocked for the member."),
    _d("favourites", "Favourites", "Member-bookmarked effects, tools, templates and systems."),
    _d("recent", "Recent", "Recently used catalogue items for rapid reuse."),
    _d("aura_created", "Aura Created", "Reusable graphs and systems created through Aura's bounded Effect/System Creator."),
    _d("user_created", "My Creations", "Reusable catalogue items authored by the current member."),
    _d("esp_originals", "ESP Originals", "First-party effects, systems, templates and creative packs authored for the Command Center."),
)

_DOMAIN_SPECS: dict[str, tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...]] = {
    "video": (
        ("timeline_edit", "Timeline & Edit", ("trim", "split", "ripple", "roll", "compound clip"), ("timeline", "clip", "edit")),
        ("mask_roto_tracking", "Masks, Roto & Tracking", ("shape masks", "rotoscoping", "object tracking", "face tracking"), ("mask", "roto", "track")),
        ("colour_hdr", "Colour & HDR", ("curves", "colour wheels", "HSL qualifiers", "LUTs", "HDR transforms"), ("colour", "grade", "hdr")),
        ("particles_weather_energy", "Particles, Weather & Energy", ("sparkles", "fire", "smoke", "rain", "snow", "lightning", "energy trails"), ("particles", "weather", "energy")),
        ("ai_create", "AI Create", ("text to video", "image to video", "storyboard to shots", "scene generation"), ("ai video", "generate", "story")),
        ("ai_edit", "AI Edit", ("generative replace", "inpaint", "outpaint", "subject edit", "effect graph creation"), ("ai edit", "generative", "effect")),
        ("quality_restore", "Quality & Restore", ("upscale", "deblur", "denoise", "frame interpolation", "stabilisation"), ("upscale", "restore", "quality")),
    ),
    "image": (
        ("layers", "Layers & Composite", ("layers", "groups", "blend modes", "clipping", "mattes"), ("layer", "blend", "composite")),
        ("selections_masks", "Selections & Masks", ("subject select", "object select", "brush mask", "refine edge"), ("select", "mask", "edge")),
        ("typography", "Typography", ("text", "text on path", "outline", "shadow", "warp text"), ("text", "font", "type")),
        ("ai_edit", "AI Edit", ("generative fill", "replace", "remove", "harmonise", "effect graph generation"), ("ai editor", "fill", "replace")),
        ("product", "Product Studio", ("product cutout", "scene placement", "product relight", "shadow", "reflection"), ("product", "commerce", "catalogue")),
    ),
    "music": (
        ("instruments", "Instruments", ("piano", "synth", "bass", "guitar", "drums", "strings", "brass"), ("instrument", "synth", "band")),
        ("eq_filters", "EQ & Filters", ("parametric EQ", "dynamic EQ", "high pass", "low pass", "notch"), ("eq", "filter", "frequency")),
        ("spectral", "Spectral", ("spectral gate", "spectral blur", "spectral freeze", "resynthesis", "spectral repair"), ("spectral", "frequency", "repair")),
        ("mastering", "Mastering", ("reference matching", "master EQ", "stereo image", "limiting", "loudness targets"), ("master", "loudness", "reference")),
        ("aura_chain", "Aura Effect Chain Creator", ("prompt chain", "macro mapping", "parallel chain", "vocal chain", "save reusable rack"), ("aura", "chain", "rack")),
    ),
    "game": (
        ("materials_shaders", "Materials & Shaders", ("PBR", "toon", "water", "glass", "hologram", "dissolve"), ("material", "shader", "pbr")),
        ("vfx_particles", "VFX & Particles", ("fire", "smoke", "explosion", "magic", "trail", "portal"), ("vfx", "particle", "effect")),
        ("network_multiplayer", "Networking & Multiplayer", ("lobby", "matchmaking", "replication", "prediction", "ownership", "reconnect"), ("multiplayer", "network", "lobby")),
        ("procedural", "Procedural Generation", ("dungeon", "terrain", "roads", "loot", "encounters", "cities"), ("procedural", "generator", "seed")),
        ("runtime_live_creation", "Live Creation Runtime", ("live scenery change", "weather shift", "music state", "persistent world delta", "undo rollback"), ("live creation", "voice world", "runtime aura")),
        ("visual_scripting", "Visual Scripting", ("events", "conditions", "actions", "variables", "timers", "debug trace"), ("node", "visual script", "graph")),
    ),
    "voice": (
        ("cleanup", "Cleanup", ("denoise", "dehum", "declick", "dereverb", "speech isolation"), ("cleanup", "voice", "studio")),
        ("speech_generation", "Speech Generation", ("text to speech", "pronunciation", "multilingual", "long form"), ("tts", "speech", "voice")),
        ("voice_to_voice", "Voice to Voice", ("voice conversion", "prosody preservation", "emotion preservation", "strength"), ("voice changer", "conversion", "prosody")),
        ("dubbing_translation", "Dubbing & Translation", ("transcribe", "translate", "timing match", "dub"), ("dub", "translate", "language")),
        ("profiles_consent", "Voice Profiles & Consent", ("voice profile", "consent evidence", "revocation", "provenance", "audit"), ("consent", "profile", "rights")),
    ),
    "live": (
        ("scenes", "Scenes", ("scene", "scene collection", "transition", "preview", "program"), ("scene", "switch", "live")),
        ("overlays", "Overlays", ("lower third", "chat box", "leaderboard", "ticker", "CTA"), ("overlay", "lower third", "graphics")),
        ("audio_filters", "Audio Filters", ("gain", "noise suppression", "compressor", "limiter", "EQ"), ("mic", "audio filter", "live audio")),
        ("moderation", "Guardian & Moderation", ("chat signals", "risk cues", "human review", "approved action", "audit"), ("guardian", "moderation", "safety")),
        ("automation", "Automation", ("event trigger", "timer trigger", "scene action", "cooldown", "rate limit"), ("automation", "trigger", "action")),
    ),
    "social": (
        ("composer", "Composer", ("text", "image", "video", "carousel", "caption", "CTA"), ("post", "composer", "social")),
        ("calendar", "Calendar", ("calendar", "campaign", "draft", "approval", "schedule"), ("calendar", "schedule", "campaign")),
        ("repurpose", "Repurpose & Resize", ("aspect ratios", "crop", "reframe", "duration variants", "thumbnail"), ("repurpose", "resize", "variant")),
        ("analytics", "Analytics", ("views", "watch time", "retention", "engagement", "conversion"), ("analytics", "performance", "retention")),
        ("growth", "Aura Growth Coach", ("content plan", "posting cadence", "hook suggestions", "retention suggestions"), ("growth", "coach", "aura")),
    ),
}

_COMMON_EXTRAS = (
    ("create_import", "Create & Import", ("create", "import", "capture"), ("create", "import", "capture")),
    ("edit", "Edit", ("edit", "transform", "adjust"), ("edit", "transform", "adjust")),
    ("effects", "Effects", ("effects", "filters", "looks"), ("effects", "filters", "looks")),
    ("automation", "Automation", ("automation", "macros", "triggers"), ("automation", "macro", "trigger")),
    ("templates", "Templates", ("templates", "presets", "starters"), ("template", "preset", "starter")),
    ("assets", "Assets", ("assets", "library", "collections"), ("asset", "library", "collection")),
    ("collaboration", "Collaboration", ("drafts", "review", "comments"), ("team", "review", "collaboration")),
    ("analytics", "Analytics", ("metrics", "performance", "freshness"), ("analytics", "metrics", "performance")),
    ("quality", "Quality", ("quality", "validation", "restore"), ("quality", "validate", "restore")),
    ("export_delivery", "Export & Delivery", ("export", "render", "delivery"), ("export", "render", "delivery")),
    ("aura_create", "Aura Create", ("prompt workflows", "bounded generation", "editable results"), ("aura", "generate", "prompt")),
    ("search_discover", "Search & Discover", ("search", "tags", "recommendations"), ("search", "discover", "tags")),
    ("rights_provenance", "Rights & Provenance", ("rights", "provenance", "licence evidence"), ("rights", "provenance", "licence")),
    ("history_versions", "History & Versions", ("history", "versions", "undo"), ("history", "version", "undo")),
)


def _build_domain(domain: str) -> tuple[StudioMenu, ...]:
    rows: list[StudioMenu] = []
    used: set[str] = set()
    for key, label, families, terms in _DOMAIN_SPECS[domain] + _COMMON_EXTRAS:
        if key in used:
            continue
        used.add(key)
        rows.append(_m(domain, key, label, f"{label} workflows for the {domain} studio.", families, terms))
    return tuple(rows)


STUDIO_MENUS = {domain: _build_domain(domain) for domain in _DOMAIN_SPECS}

PUBLIC_BLOCKED_BRAND_TERMS = (
    "capcut", "adobe", "premiere", "after effects", "photoshop", "davinci", "resolve",
    "filmora", "canva", "ableton", "logic pro", "unreal", "unity", "godot", "obs",
    "elevenlabs", "descript",
)


def _contains_blocked_brand(text: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
    padded = f" {normalized} "
    return any(
        f" {re.sub(r'[^a-z0-9]+', ' ', term.casefold()).strip()} " in padded
        for term in PUBLIC_BLOCKED_BRAND_TERMS
    )


def validate_studio_menus() -> None:
    discovery_ids = [section.id for section in DISCOVERY_SECTIONS]
    if len(discovery_ids) != len(set(discovery_ids)):
        raise ValueError("Discovery menu IDs must be unique")
    if [band.id for band in EFFECT_BANDS] != ["core", "silver", "gold"]:
        raise ValueError("Effect bands must remain core, silver and gold")
    if [band.coin_price for band in EFFECT_BANDS] != [0, 200, 500]:
        raise ValueError("Effect band Coin prices changed unexpectedly")

    required_domains = {"video", "image", "music", "game", "voice", "live", "social"}
    if set(STUDIO_MENUS) != required_domains:
        raise ValueError("Studio menu domain set is incomplete")

    ids: list[str] = []
    for domain, menus in STUDIO_MENUS.items():
        if len(menus) < 14:
            raise ValueError(f"Studio menu taxonomy is too shallow for {domain}")
        for menu in menus:
            if menu.domain != domain or not menu.id.startswith(f"studio.{domain}."):
                raise ValueError(f"Invalid menu namespace: {menu.id}")
            if not menu.feature_families or not menu.search_terms:
                raise ValueError(f"Studio menu requires feature/search families: {menu.id}")
            public_text = " ".join((menu.id, menu.label, menu.description, *menu.feature_families, *menu.search_terms))
            if _contains_blocked_brand(public_text):
                raise ValueError(f"Third-party brand leaked into public studio taxonomy: {menu.id}")
            ids.append(menu.id)
    if len(ids) != len(set(ids)):
        raise ValueError("Studio menu IDs must be unique")


validate_studio_menus()


def public_studio_catalogue(*, domain: str | None = None) -> dict:
    selected = (domain or "").strip().casefold()
    if selected and selected not in STUDIO_MENUS:
        raise ValueError("Unknown studio catalogue domain")
    domains = [selected] if selected else sorted(STUDIO_MENUS)
    menus = [menu.public() for key in domains for menu in STUDIO_MENUS[key]]
    return {
        "catalogue_version": 1,
        "domains": domains,
        "discovery": [section.public() for section in DISCOVERY_SECTIONS],
        "effect_bands": [band.public() for band in EFFECT_BANDS],
        "menus": menus,
        "menu_count": len(menus),
        "original_first_party_taxonomy": True,
        "third_party_branding_in_public_taxonomy": False,
        "effect_band_is_separate_from_subscription_plan": True,
    }


__all__ = [
    "DISCOVERY_SECTIONS",
    "EFFECT_BANDS",
    "PUBLIC_BLOCKED_BRAND_TERMS",
    "STUDIO_MENUS",
    "DiscoverySection",
    "EffectBand",
    "StudioMenu",
    "public_studio_catalogue",
    "validate_studio_menus",
]
