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


def _m(domain: str, key: str, label: str, families: Iterable[str], terms: Iterable[str] = (), *, graph_editable: bool = True) -> StudioMenu:
    families = tuple(families)
    terms = tuple(terms) or tuple(families[:3])
    return StudioMenu(
        id=f"studio.{domain}.{key}",
        domain=domain,
        label=label,
        description=f"Original {label.casefold()} tools for the {domain} studio.",
        feature_families=families,
        search_terms=terms,
        graph_editable=graph_editable,
    )


EFFECT_BANDS: tuple[EffectBand, ...] = (
    EffectBand("core", "Core / Free", 0, "First-party effects available without a Coin purchase where configured."),
    EffectBand("silver", "Silver", 200, "Premium first-party effect band priced at 200 Cosmic Creation Coins."),
    EffectBand("gold", "Gold", 500, "Advanced first-party effect or reusable system band priced at 500 Cosmic Creation Coins."),
)

DISCOVERY_SECTIONS: tuple[DiscoverySection, ...] = tuple(
    DiscoverySection(f"discover.{key}", label, description)
    for key, label, description in (
        ("search", "Search", "Search names, categories, tags, compatible media, moods, genres and workflows."),
        ("recommended", "Recommended", "Project-aware first-party recommendations."),
        ("trending", "Trending", "Popular catalogue items without copying third-party ranking data."),
        ("new", "New", "Recently released original effects, systems, templates and tools."),
        ("free", "Free", "Core items available without a Coin purchase."),
        ("silver", "Silver", "Items assigned to the 200-Coin band."),
        ("gold", "Gold", "Items assigned to the 500-Coin band."),
        ("owned", "Owned", "Account-authorised items already unlocked."),
        ("favourites", "Favourites", "Member-bookmarked creative items."),
        ("recent", "Recent", "Recently used catalogue items."),
        ("aura_created", "Aura Created", "Reusable graphs and systems created through Aura."),
        ("my_creations", "My Creations", "Reusable items authored by the current member."),
        ("esp_originals", "ESP Originals", "First-party Command Center creative systems."),
    )
)


STUDIO_MENUS: dict[str, tuple[StudioMenu, ...]] = {
    "video": (
        _m("video", "create_import", "Create & Import", ("media import", "camera capture", "screen capture", "proxy media", "AI generation")),
        _m("video", "timeline", "Timeline & Edit", ("trim", "split", "ripple", "roll", "slip", "slide", "nest", "freeze frame")),
        _m("video", "transform", "Transform & Layout", ("position", "scale", "rotation", "crop", "perspective", "corner pin", "auto reframe")),
        _m("video", "composite", "Overlay & Composite", ("layers", "blend modes", "alpha", "track mattes", "split screen", "picture in picture")),
        _m("video", "masks", "Masks, Roto & Tracking", ("shape masks", "bezier masks", "subject masks", "rotoscoping", "point tracking", "planar tracking")),
        _m("video", "keying", "Keying", ("chroma key", "luma key", "spill suppression", "edge refinement", "garbage matte")),
        _m("video", "colour", "Colour & HDR", ("exposure", "white balance", "curves", "colour wheels", "HSL", "LUT", "HDR tone mapping", "scopes")),
        _m("video", "looks", "Filters & Looks", ("cinematic looks", "monochrome", "duotone", "vintage", "night", "seasonal", "genre looks")),
        _m("video", "light", "Light, Lens & Film", ("bloom", "glow", "lens flare", "light leak", "halation", "vignette", "grain", "bokeh")),
        _m("video", "distortion", "Blur, Distortion & Glitch", ("blur", "motion blur", "sharpen", "displacement", "warp", "scanline", "RGB split", "digital jitter")),
        _m("video", "particles", "Particles, Weather & Energy", ("sparkles", "confetti", "embers", "fire", "smoke", "fog", "rain", "snow", "lightning", "shockwave")),
        _m("video", "motion", "Motion, Camera & 3D", ("keyframes", "easing", "motion paths", "camera shake", "parallax", "depth camera", "3D transform")),
        _m("video", "transitions", "Transitions", ("dissolve", "wipe", "slide", "zoom", "whip", "glitch", "particle", "mask transition")),
        _m("video", "text", "Captions, Text & Titles", ("auto captions", "subtitles", "kinetic text", "lower thirds", "title cards", "credits", "callouts")),
        _m("video", "ai_edit", "AI Edit", ("object removal", "object replacement", "background replacement", "generative extend", "restyle", "scene generation", "effect graph creation")),
        _m("video", "audio", "Audio & Reactive", ("audio effects", "voice cleanup", "beat sync", "bass trigger", "spectrum drive", "music visualizer")),
        _m("video", "restore", "Quality & Restore", ("upscale", "deblur", "denoise", "frame interpolation", "stabilisation", "flicker reduction")),
        _m("video", "export", "Export & Delivery", ("codec", "bitrate", "frame rate", "HDR", "caption sidecar", "batch variants"), graph_editable=False),
    ),
    "image": (
        _m("image", "generate", "Generate", ("text to image", "reference image", "variation", "transparent generation", "consistent subject")),
        _m("image", "layers", "Layers & Composite", ("layers", "groups", "blend modes", "opacity", "clipping", "mattes")),
        _m("image", "selection", "Selections & Masks", ("subject selection", "object selection", "colour selection", "path selection", "brush mask", "refine edge")),
        _m("image", "retouch", "Retouch", ("heal", "clone", "blemish cleanup", "dodge burn", "red-eye", "texture preservation")),
        _m("image", "objects", "Background & Objects", ("remove background", "replace background", "remove object", "replace object", "insert object", "shadow synthesis")),
        _m("image", "expand_crop", "Crop, Expand & Reframe", ("crop", "straighten", "perspective", "content-aware crop", "generative expand", "aspect conversion", "smart reframe")),
        _m("image", "lighting", "Lighting & Relight", ("exposure", "relight", "rim light", "soft light", "sunlight", "neon light", "shadow shaping")),
        _m("image", "colour", "Colour & Tone", ("white balance", "curves", "levels", "HSL", "selective colour", "gradient map", "LUT", "duotone")),
        _m("image", "geometry", "Geometry & Transform", ("resize", "rotate", "skew", "distort", "warp", "mesh warp", "align", "distribute")),
        _m("image", "typography", "Typography", ("text", "text on path", "outline", "shadow", "glow", "extrusion", "warp text", "font pairing")),
        _m("image", "brushes", "Brushes & Drawing", ("brushes", "pencils", "ink", "erasers", "smudge", "vector pen", "shape drawing")),
        _m("image", "filters", "Filters & Effects", ("blur", "sharpen", "noise", "grain", "posterize", "halftone", "glow", "distortion", "stylise")),
        _m("image", "textures", "Textures, Patterns & Materials", ("patterns", "gradients", "paper", "fabric", "metal", "glass", "procedural texture", "seamless tile")),
        _m("image", "layout", "Layouts & Templates", ("poster", "cover", "thumbnail", "social post", "story", "flyer", "banner", "collage")),
        _m("image", "ai_edit", "AI Edit", ("generative fill", "generative expand", "object edit", "background edit", "relight", "style transform", "effect graph")),
        _m("image", "restore", "Quality & Restore", ("upscale", "deblur", "denoise", "scratch repair", "colour recovery", "detail recovery")),
        _m("image", "export", "Export & Delivery", ("size", "format", "compression", "transparency", "colour profile", "batch export"), graph_editable=False),
    ),
    "music": (
        _m("music", "project", "Projects & Arrangement", ("sessions", "tracks", "clips", "takes", "comping", "markers", "arrangement", "versions")),
        _m("music", "record", "Record & Capture", ("audio recording", "MIDI recording", "count-in", "metronome", "takes", "punch recording", "loop recording")),
        _m("music", "edit", "Audio Edit", ("trim", "split", "fade", "crossfade", "gain", "normalize", "reverse", "time stretch", "pitch shift")),
        _m("music", "midi", "MIDI & Piano Roll", ("piano roll", "notation", "quantize", "velocity", "humanize", "groove", "probability", "automation")),
        _m("music", "instruments", "Instruments", ("synths", "samplers", "drums", "bass", "keys", "guitar", "orchestral", "sound design")),
        _m("music", "eq", "EQ & Filters", ("parametric EQ", "dynamic EQ", "high-pass", "low-pass", "notch", "shelving", "resonance control")),
        _m("music", "dynamics", "Dynamics", ("compressor", "multiband compressor", "gate", "expander", "limiter", "clipper", "de-esser", "transient shaper")),
        _m("music", "tone", "Tone & Character", ("saturation", "distortion", "overdrive", "fuzz", "amp", "cabinet", "exciter", "bitcrush")),
        _m("music", "modulation", "Modulation", ("chorus", "flanger", "phaser", "tremolo", "vibrato", "rotary", "ring modulation")),
        _m("music", "space", "Delay & Reverb", ("delay", "echo", "multitap", "reverb", "convolution reverb", "early reflections", "resonator")),
        _m("music", "vocal", "Vocal Processing", ("pitch correction", "harmony", "formant", "vocoder", "de-esser", "breath control", "vocal chain")),
        _m("music", "stereo", "Stereo & Spatial", ("stereo width", "mid-side", "panning", "phase", "correlation", "binaural", "spatial audio")),
        _m("music", "restore", "Restoration", ("noise reduction", "hum removal", "declick", "declip", "dereverb", "spectral repair")),
        _m("music", "mastering", "Mastering", ("reference analysis", "loudness target", "tonal balance", "multiband", "imaging", "limiting", "dither", "A/B compare")),
        _m("music", "ai", "AI Music Tools", ("song generation", "stem generation", "stem separation", "audio to MIDI", "chord suggestions", "arrangement assist", "effect graph")),
        _m("music", "meters", "Meters & Analyzers", ("spectrum", "waveform", "LUFS", "true peak", "phase", "correlation", "stereo image"), graph_editable=False),
        _m("music", "export", "Export & Delivery", ("mixdown", "stems", "sample rate", "bit depth", "metadata", "batch export"), graph_editable=False),
    ),
    "game": (
        _m("game", "project", "Projects & Templates", ("project", "template", "version", "branch", "import", "export")),
        _m("game", "scene", "Scenes & Levels", ("scene", "level", "world", "hierarchy", "prefab", "streaming", "checkpoints")),
        _m("game", "entities", "Entities & Components", ("entity", "component", "prefab", "properties", "tags", "events")),
        _m("game", "world", "Terrain & World Building", ("terrain", "biomes", "foliage", "roads", "water", "weather", "procedural generation")),
        _m("game", "materials", "Materials & Shaders", ("PBR", "materials", "shader graph", "textures", "decals", "post-process")),
        _m("game", "lighting", "Lighting & VFX", ("lights", "shadows", "volumetrics", "particles", "fire", "smoke", "lightning", "energy effects")),
        _m("game", "physics", "Physics", ("collision", "rigid body", "soft body", "constraints", "ragdoll", "destruction", "vehicles")),
        _m("game", "animation", "Animation & Rigging", ("skeleton", "animation", "state machine", "blend tree", "IK", "retargeting", "motion capture")),
        _m("game", "camera", "Camera & Cinematics", ("camera", "follow", "orbit", "shake", "cutscene", "timeline", "cinematic transitions")),
        _m("game", "gameplay", "Gameplay Systems", ("input", "abilities", "combat", "inventory", "quests", "dialogue", "interaction", "save points")),
        _m("game", "ai", "AI & Navigation", ("navigation", "pathfinding", "behaviour", "NPC", "perception", "decision graph", "world director")),
        _m("game", "audio", "Audio & Adaptive Music", ("spatial audio", "music states", "adaptive score", "ambience", "footsteps", "sound events")),
        _m("game", "ui", "UI & HUD", ("HUD", "menus", "inventory UI", "map", "dialogue UI", "accessibility")),
        _m("game", "multiplayer", "Multiplayer & Networking", ("replication", "authority", "lobbies", "matchmaking", "sessions", "server", "rollback")),
        _m("game", "aura_presence", "Aura Presence & Live Creation", ("voice command", "WorldDelta", "SceneDelta", "dynamic scenery", "story mutation", "world state", "undo", "rollback")),
        _m("game", "engines", "Engine & Build Targets", ("ESP native engine", "advanced engine integration", "lightweight indie runtime", "web build", "desktop build", "mobile build"), graph_editable=False),
        _m("game", "publish", "Publish & Marketplace", ("build", "package", "version", "multiplayer entitlement", "marketplace", "analytics"), graph_editable=False),
    ),
    "voice": (
        _m("voice", "record", "Record & Import", ("microphone", "import", "takes", "waveform", "trim", "gain")),
        _m("voice", "transcribe", "Transcription", ("speech to text", "speaker timing", "word timing", "punctuation", "captions")),
        _m("voice", "cleanup", "Cleanup", ("noise removal", "hum removal", "click removal", "plosive control", "breath control", "dereverb")),
        _m("voice", "eq", "EQ & Dynamics", ("EQ", "filter", "compressor", "limiter", "gate", "de-esser")),
        _m("voice", "isolation", "Voice Isolation", ("voice isolation", "background attenuation", "room reduction", "dialogue enhance")),
        _m("voice", "pitch", "Pitch & Formant", ("pitch", "formant", "intonation", "timing", "naturalness")),
        _m("voice", "performance", "Performance & Prosody", ("pace", "emphasis", "pause", "emotion", "style", "energy")),
        _m("voice", "character", "Character Effects", ("telephone", "radio", "robot", "megaphone", "whisper", "creature", "spatial character")),
        _m("voice", "tts", "Text to Speech", ("TTS", "language", "voice profile", "speed", "prosody", "caption sync")),
        _m("voice", "voice_change", "Voice Transformation", ("voice to voice", "performance transfer", "timbre", "pitch", "formant")),
        _m("voice", "dubbing", "Dubbing & Translation", ("translation", "dubbing", "timing", "speaker mapping", "language variants")),
        _m("voice", "pronunciation", "Pronunciation", ("phonemes", "dictionary", "names", "regional pronunciation", "practice")),
        _m("voice", "spatial", "Spatial & Environment", ("panning", "room", "distance", "binaural", "ambience", "reverb")),
        _m("voice", "rights", "Consent & Rights", ("consent", "provenance", "voice asset", "revoke", "audit"), graph_editable=False),
        _m("voice", "ai", "Aura Voice Tools", ("cleanup chain", "performance assist", "translation", "lesson practice", "effect graph")),
        _m("voice", "export", "Export & Delivery", ("format", "sample rate", "captions", "transcript", "metadata"), graph_editable=False),
    ),
    "live": (
        _m("live", "scenes", "Scenes & Sources", ("scene", "camera", "screen", "media", "browser source", "audio source")),
        _m("live", "overlays", "Overlays", ("frames", "lower thirds", "chat", "goals", "labels", "branding")),
        _m("live", "transitions", "Transitions", ("cut", "fade", "wipe", "stinger", "motion transition", "scene morph")),
        _m("live", "camera", "Camera Effects", ("crop", "colour", "background", "portrait", "blur", "keying", "tracking")),
        _m("live", "audio", "Audio Filters", ("EQ", "compressor", "limiter", "gate", "noise reduction", "ducking")),
        _m("live", "voice", "Voice Effects", ("pitch", "formant", "radio", "robot", "reverb", "character")),
        _m("live", "captions", "Captions & TTS", ("live captions", "translation", "TTS", "speaker labels", "accessibility")),
        _m("live", "alerts", "Alerts & Reactions", ("follows", "members", "gifts", "reactions", "milestones", "custom events")),
        _m("live", "particles", "Particles & Reactive Visuals", ("confetti", "hearts", "sparks", "stars", "spectrum", "beat reactive")),
        _m("live", "widgets", "Widgets", ("goals", "timers", "polls", "scoreboard", "chat", "ticker")),
        _m("live", "show", "Show Control", ("cue", "scene queue", "hotkey", "macro", "schedule", "run of show")),
        _m("live", "automation", "Automation", ("event trigger", "scene rule", "audio rule", "overlay rule", "bounded Aura action")),
        _m("live", "record", "Recording & Replay", ("record", "replay", "clip", "highlight", "marker", "export")),
        _m("live", "safety", "Guardian & Safety", ("permissions", "moderation", "privacy", "consent", "audit", "emergency stop"), graph_editable=False),
        _m("live", "ai", "Aura LIVE", ("show assistant", "caption assist", "scene suggestions", "effect graph", "creator coaching")),
    ),
    "social": (
        _m("social", "plan", "Planning", ("calendar", "content plan", "campaign", "brief", "ideas", "goals")),
        _m("social", "create", "Create", ("post", "short", "story", "carousel", "thumbnail", "cover")),
        _m("social", "repurpose", "Repurpose", ("clip extraction", "aspect variants", "caption variants", "hook variants", "cross-platform version")),
        _m("social", "text", "Hooks, Copy & Captions", ("hook", "caption", "title", "description", "CTA", "hashtags", "keywords")),
        _m("social", "visual", "Visual Packaging", ("thumbnail", "poster frame", "cover", "brand layout", "series identity")),
        _m("social", "brand", "Brand Kits", ("logo", "font", "colour", "templates", "watermark", "voice and tone")),
        _m("social", "templates", "Campaign Templates", ("launch", "event", "announcement", "countdown", "education", "testimonial", "recruitment")),
        _m("social", "ads", "Ads & Promotions", ("ad creative", "headline variants", "CTA variants", "product creative", "offer card", "A/B variants")),
        _m("social", "product", "Product Creative", ("product photo", "product video", "catalogue card", "feature callout", "demo")),
        _m("social", "localise", "Translate & Localise", ("translation", "localized captions", "dubbing", "regional copy", "language variants")),
        _m("social", "publish", "Publish & Schedule", ("schedule", "queue", "provider publish", "retry", "published confirmation"), graph_editable=False),
        _m("social", "analytics", "Analytics", ("views", "watch time", "retention", "engagement", "clicks", "conversion"), graph_editable=False),
        _m("social", "trends", "Trends & Research", ("trend research", "topic signals", "format patterns", "seasonality", "opportunity score"), graph_editable=False),
        _m("social", "growth", "Aura Growth Coach", ("content plan", "posting cadence", "hook suggestions", "retention suggestions", "experiment plan"), graph_editable=False),
        _m("social", "team", "Approvals & Team", ("draft", "review", "approve", "reject", "comment", "version", "audit"), graph_editable=False),
    ),
}


PUBLIC_BLOCKED_BRAND_TERMS = (
    "capcut", "adobe", "premiere", "after effects", "photoshop", "davinci", "resolve",
    "filmora", "canva", "ableton", "logic pro", "unreal", "unity", "godot", "obs",
    "elevenlabs", "descript",
)


def _contains_blocked_brand(text: str) -> bool:
    """Match explicit brand words/phrases, not innocent substrings such as 'canvases'."""
    normalized = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
    padded = f" {normalized} "
    return any(f" {re.sub(r'[^a-z0-9]+', ' ', term.casefold()).strip()} " in padded for term in PUBLIC_BLOCKED_BRAND_TERMS)


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
    "DISCOVERY_SECTIONS", "EFFECT_BANDS", "STUDIO_MENUS", "DiscoverySection", "EffectBand",
    "StudioMenu", "public_studio_catalogue", "validate_studio_menus", "_contains_blocked_brand",
]
