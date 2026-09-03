from __future__ import annotations

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


def _d(id: str, label: str, description: str) -> DiscoverySection:
    return DiscoverySection(id=id, label=label, description=description)


def _m(
    domain: str,
    key: str,
    label: str,
    description: str,
    families: Iterable[str],
    terms: Iterable[str] = (),
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


# Effect purchase bands are deliberately independent from subscription plan IDs.
# Assignment of a specific effect to a band belongs to server-authoritative catalogue metadata.
EFFECT_BANDS: tuple[EffectBand, ...] = (
    EffectBand("core", "Core / Free", 0, "First-party effects available without a Coin purchase where configured."),
    EffectBand("silver", "Silver", 200, "Premium first-party effect band priced at 200 Cosmic Creation Coins."),
    EffectBand("gold", "Gold", 500, "Advanced first-party effect or reusable system band priced at 500 Cosmic Creation Coins."),
)


DISCOVERY_SECTIONS: tuple[DiscoverySection, ...] = (
    _d("discover.search", "Search", "Search names, categories, tags, compatible media, moods, genres and workflows."),
    _d("discover.trending", "Trending", "Currently popular first-party and user-authorised catalogue items."),
    _d("discover.new", "New", "Recently released original effects, systems, templates and tools."),
    _d("discover.recommended", "Recommended", "Project-aware recommendations based on media type and current workflow."),
    _d("discover.free", "Free", "Core effects and systems available without a Coin purchase."),
    _d("discover.silver", "Silver", "Effects assigned to the 200-Coin premium band."),
    _d("discover.gold", "Gold", "Advanced effects and systems assigned to the 500-Coin premium band."),
    _d("discover.owned", "Owned", "Account-authorised effects and systems already unlocked for the member."),
    _d("discover.favourites", "Favourites", "Member-bookmarked effects, tools, templates and systems."),
    _d("discover.recent", "Recent", "Recently used catalogue items for rapid reuse."),
    _d("discover.aura_created", "Aura Created", "Reusable graphs and systems created through Aura's bounded Effect/System Creator."),
    _d("discover.user_created", "My Creations", "Reusable catalogue items authored by the current member."),
    _d("discover.esp_originals", "ESP Originals", "First-party effects, systems, templates and creative packs authored for the Command Center."),
)


STUDIO_MENUS: dict[str, tuple[StudioMenu, ...]] = {
    "video": (
        _m("video", "create_import", "Create & Import", "Start, import, capture or generate source media.", ("new video", "camera capture", "screen capture", "frame capture", "media import", "proxy creation"), ("record", "capture", "import")),
        _m("video", "timeline_edit", "Timeline & Edit", "Non-destructive timeline editing and clip operations.", ("trim", "split", "slip", "slide", "ripple", "roll", "duplicate", "replace", "nest", "compound clip", "freeze frame"), ("timeline", "clip", "edit")),
        _m("video", "transform_layout", "Transform & Layout", "Spatial placement and framing controls.", ("position", "scale", "rotation", "anchor", "crop", "perspective", "corner pin", "safe areas", "auto reframe"), ("crop", "resize", "reframe")),
        _m("video", "overlay_composite", "Overlay & Composite", "Layer, blend and composite multiple visual sources.", ("overlays", "blend modes", "mattes", "alpha", "track mattes", "picture in picture", "split screen", "collage"), ("overlay", "blend", "composite")),
        _m("video", "mask_roto_tracking", "Masks, Roto & Tracking", "Create and track editable masks and subject mattes.", ("shape masks", "bezier masks", "subject masks", "rotoscoping", "point tracking", "planar tracking", "object tracking", "face tracking"), ("mask", "roto", "track")),
        _m("video", "keying", "Keying", "Extract foreground subjects and keyed colours.", ("chroma key", "luma key", "difference key", "spill suppression", "edge refinement", "garbage matte"), ("green screen", "key", "background")),
        _m("video", "colour_hdr", "Colour & HDR", "Primary, secondary and HDR colour workflows.", ("exposure", "contrast", "white balance", "curves", "colour wheels", "HSL qualifiers", "LUTs", "tone mapping", "HDR transforms", "scopes"), ("color", "colour", "grade", "lut")),
        _m("video", "filters_looks", "Filters & Looks", "Reusable original looks with editable intensity and underlying parameters.", ("cinematic looks", "monochrome", "duotone", "pastel", "vintage", "night", "dream", "seasonal", "genre looks"), ("filter", "preset", "look")),
        _m("video", "light_lens_film", "Light, Lens & Film", "Optical and photographic treatments.", ("bloom", "glow", "lens flare", "light leaks", "halation", "vignette", "film grain", "gate weave", "chromatic aberration", "bokeh"), ("glow", "lens", "film")),
        _m("video", "blur_sharpen_noise", "Blur, Sharpen & Noise", "Focus, detail and noise treatments.", ("gaussian blur", "directional blur", "radial blur", "depth blur", "motion blur", "sharpen", "clarity", "denoise", "procedural noise"), ("blur", "sharp", "noise")),
        _m("video", "distortion_glitch", "Distortion & Glitch", "Editable spatial, temporal and digital distortion systems.", ("displacement", "warp", "twirl", "wave", "pixel sort", "datamosh-style graph", "scanline", "RGB split", "digital jitter"), ("glitch", "warp", "distort")),
        _m("video", "particles_weather_energy", "Particles, Weather & Energy", "Particle and simulation-driven visual effects.", ("sparkles", "stars", "confetti", "embers", "fire", "smoke", "fog", "rain", "snow", "dust", "lightning", "shockwave", "energy trails", "magic particles"), ("particles", "weather", "energy", "sparkle")),
        _m("video", "motion_camera_3d", "Motion, Camera & 3D", "Keyframed motion, virtual camera and 2.5D/3D presentation.", ("keyframes", "easing", "motion paths", "camera shake", "push pull", "orbit", "parallax", "depth camera", "3D transform", "motion presets"), ("motion", "camera", "3d")),
        _m("video", "transitions", "Transitions", "Editable transitions generated from bounded graph primitives.", ("dissolve", "wipe", "slide", "push", "zoom", "whip", "spin", "blur", "light", "glitch", "liquid", "particle", "mask", "camera match"), ("transition", "between clips")),
        _m("video", "stylise", "Stylise", "Transform visual character without copying third-party style packs.", ("illustrative", "comic", "posterised", "neon", "retro display", "ink", "paint", "paper", "low-poly look", "holographic"), ("stylize", "stylise", "art")),
        _m("video", "portrait_subject", "Portrait & Subject", "Subject-aware appearance and portrait editing.", ("retouch", "skin cleanup", "relight", "hair recolour", "eye emphasis", "wardrobe recolour", "subject isolate", "body-safe reshape controls"), ("portrait", "retouch", "hair")),
        _m("video", "background_object", "Background & Objects", "Object-aware scene edits.", ("remove background", "replace background", "object removal", "object replacement", "object insert", "shadow synthesis", "reflection synthesis", "scene relight"), ("remove", "replace", "background")),
        _m("video", "captions_titles", "Captions, Text & Titles", "Transcription-driven and manually authored typography systems.", ("auto captions", "word timing", "speaker styles", "karaoke highlighting", "lower thirds", "kinetic text", "title cards", "credits", "callouts", "subtitles"), ("caption", "subtitle", "text", "title")),
        _m("video", "patterns_stickers", "Patterns, Elements & Stickers", "Editable decorative elements and pattern emitters.", ("patterns", "brush strokes", "shapes", "arrows", "icons", "stickers", "emoji fields", "frames", "borders", "animated elements"), ("pattern", "sticker", "element")),
        _m("video", "audio_reactive", "Audio Reactive", "Drive visual parameters from analysed audio features.", ("beat sync", "bass trigger", "onset trigger", "spectrum drive", "envelope drive", "lyric cue", "music visualizer"), ("beat", "audio reactive", "music sync")),
        _m("video", "ai_create", "AI Create", "Generate original project media through configured model adapters.", ("text to video", "image to video", "reference-guided video", "storyboard to shots", "scene generation", "dialogue scene"), ("ai video", "generate", "story")),
        _m("video", "ai_edit", "AI Edit", "Instruction-led edits that remain project-scoped and provenance-aware.", ("generative replace", "inpaint", "outpaint", "restyle", "subject edit", "camera extension", "motion transfer", "effect graph creation"), ("ai effect", "generative edit")),
        _m("video", "ai_localise", "Translate & Localise", "Create language variants with captions, dubbing and timing controls.", ("video translation", "subtitle translation", "dubbing", "speaker matching", "lip sync", "language variants"), ("translate", "dub", "language")),
        _m("video", "ai_clips", "Clips & Repurpose", "Create short-form variants from longer media.", ("highlight detection", "hook scoring", "AI clipper", "silence removal", "bad-take suggestions", "auto cut", "aspect variants"), ("clipper", "autocut", "shorts")),
        _m("video", "quality_restore", "Quality & Restore", "Enhancement and restoration pipeline.", ("upscale", "deblur", "denoise", "deinterlace", "frame interpolation", "flicker reduction", "stabilisation", "rolling-shutter correction", "detail recovery"), ("uhd", "upscale", "restore", "quality")),
        _m("video", "marketing", "Marketing Creative", "Product, advert and campaign-oriented video workflows.", ("product video", "smart advert", "promo template", "CTA system", "brand variant", "testimonial layout", "social commerce creative"), ("ad", "product", "marketing")),
        _m("video", "export_delivery", "Export & Delivery", "Render and package platform-ready outputs.", ("resolution", "frame rate", "codec", "bitrate", "HDR", "audio mix", "captions burn-in", "caption sidecar", "social presets", "batch variants"), ("export", "render", "delivery"), graph_editable=False),
    ),
    "image": (
        _m("image", "generate", "Generate", "Create original images and design starting points.", ("text to image", "reference image", "variation", "consistent subject", "style controls", "transparent generation"), ("generate", "ai image")),
        _m("image", "layers", "Layers & Composite", "Non-destructive raster, vector, text and adjustment layers.", ("layers", "groups", "blend modes", "opacity", "smart transforms", "clipping", "mattes", "composite"), ("layer", "blend")),
        _m("image", "selections_masks", "Selections & Masks", "Precise editable selections and masks.", ("subject select", "object select", "colour select", "path select", "brush mask", "gradient mask", "refine edge", "feather"), ("select", "mask")),
        _m("image", "retouch", "Retouch", "Portrait, product and cleanup tools.", ("heal", "clone", "blemish cleanup", "dodge burn", "frequency-style separation", "skin texture preservation", "red-eye", "teeth and eye adjustments"), ("retouch", "heal", "clone")),
        _m("image", "background_object", "Background & Objects", "Object-aware editing and compositing.", ("remove background", "replace background", "remove object", "replace object", "insert object", "shadow", "reflection", "depth-aware composite"), ("background", "object", "remove")),
        _m("image", "expand_crop", "Crop, Expand & Reframe", "Resize and extend canvases intelligently.", ("crop", "straighten", "perspective", "content-aware crop", "generative expand", "aspect conversion", "smart reframe"), ("expand", "crop", "reframe")),
        _m("image", "lighting", "Lighting & Relight", "Edit lighting direction, balance and atmosphere.", ("exposure", "relight", "subject lighting", "background lighting", "rim light", "softbox look", "sunlight", "neon light", "shadow shaping"), ("light", "relight")),
        _m("image", "colour", "Colour & Tone", "Professional colour correction and look development.", ("white balance", "curves", "levels", "HSL", "selective colour", "gradient map", "LUT", "tone mapping", "duotone"), ("color", "colour", "tone")),
        _m("image", "geometry", "Geometry & Transform", "Transform raster and vector content.", ("resize", "rotate", "skew", "distort", "perspective", "warp", "mesh warp", "liquify-style bounded transform", "align", "distribute"), ("transform", "warp", "perspective")),
        _m("image", "typography", "Typography", "Editable text and typographic effects.", ("text", "text on path", "variable type", "outline", "shadow", "glow", "extrusion", "warp text", "kinetic poster frames", "font pairing"), ("text", "font", "type")),
        _m("image", "layout", "Layout & Templates", "Responsive design documents and reusable layouts.", ("poster", "cover", "thumbnail", "social post", "story", "flyer", "banner", "collage", "presentation visual", "brand layout"), ("poster", "template", "layout")),
        _m("image", "brushes", "Brushes & Drawing", "Raster/vector brush and drawing systems.", ("paint brush", "ink", "pencil", "marker", "airbrush", "texture brush", "stamp brush", "symmetry", "stabiliser", "eraser"), ("brush", "draw", "paint")),
        _m("image", "textures_patterns", "Textures & Patterns", "Procedural and asset-backed surface treatments.", ("paper", "fabric", "metal", "stone", "wood", "grain", "noise", "seamless pattern", "halftone", "geometric pattern"), ("texture", "pattern")),
        _m("image", "gradients_materials", "Gradients & Materials", "Gradient, material and appearance systems.", ("linear gradient", "radial gradient", "mesh gradient", "metallic", "glass", "chrome", "holographic", "plastic", "fabric", "emissive"), ("gradient", "material", "chrome")),
        _m("image", "frames_mockups", "Frames & Mockups", "Frames, scene placement and presentation mockups.", ("frames", "borders", "device mockup", "print mockup", "apparel mockup", "packaging mockup", "signage", "perspective surface"), ("frame", "mockup")),
        _m("image", "filters_looks", "Filters & Looks", "Original one-tap looks backed by editable graph parameters.", ("cinematic", "vintage", "black and white", "dream", "high contrast", "soft", "editorial", "seasonal", "colour families"), ("filter", "look")),
        _m("image", "ai_edit", "AI Edit", "Instruction-driven bounded image transformations.", ("generative fill", "replace", "remove", "harmonise", "style variation", "scene relight", "subject variation", "effect graph generation"), ("ai editor", "generative fill")),
        _m("image", "restore_upscale", "Restore & Upscale", "Quality recovery and enlargement.", ("upscale", "denoise", "deblur", "scratch repair", "face restore", "colourise", "compression cleanup", "old-photo restore"), ("restore", "upscale", "enhance")),
        _m("image", "product", "Product Studio", "Commerce-oriented product imagery.", ("product cutout", "scene placement", "product relight", "shadow", "reflection", "colour variants", "background packs", "catalogue layout"), ("product", "commerce")),
        _m("image", "marketing", "Marketing & Ads", "Campaign and promotional visual systems.", ("smart advert", "poster", "social advert", "CTA layout", "brand kit", "campaign variants", "fashion presentation", "event creative"), ("ad", "poster", "marketing")),
        _m("image", "batch_export", "Batch & Export", "Process and export sets of visual variants.", ("batch resize", "batch crop", "format conversion", "compression", "metadata", "transparent export", "social sizes", "print sizes"), ("batch", "export"), graph_editable=False),
    ),
    "music": (
        _m("music", "create_compose", "Create & Compose", "Song, score and arrangement creation workflows.", ("text to song", "lyrics to song", "instrumental", "build around upload", "chord assistant", "melody assistant", "arrangement", "song sections"), ("song", "compose", "arrange")),
        _m("music", "tracks_arrangement", "Tracks & Arrangement", "Professional multitrack session structure.", ("audio tracks", "instrument tracks", "MIDI tracks", "buses", "returns", "folders", "takes", "comping", "markers", "tempo map"), ("track", "arrangement", "daw")),
        _m("music", "instruments", "Instruments", "Original and licensed instrument catalogue access.", ("piano", "keys", "synth", "bass", "guitar", "drums", "percussion", "strings", "brass", "woodwind", "orchestral", "world", "experimental"), ("instrument", "synth")),
        _m("music", "samples_loops", "Samples & Loops", "Rights-aware sample, loop and one-shot workflows.", ("loops", "one shots", "drum hits", "phrases", "textures", "semantic search", "tempo match", "key match", "slice", "chop"), ("sample", "loop")),
        _m("music", "midi_score", "MIDI & Score", "Note, score and performance-control editing.", ("piano roll", "score", "quantise", "groove", "humanise", "velocity", "MPE-style expression", "arpeggiation", "chord track", "audio to MIDI"), ("midi", "score", "notes")),
        _m("music", "recording", "Recording", "Browser and device recording workflows.", ("input selection", "latency calibration", "monitoring", "takes", "punch in", "loop record", "count-in", "metronome", "multi-input"), ("record", "take", "mic")),
        _m("music", "editing", "Audio Editing", "Non-destructive region and waveform editing.", ("trim", "split", "fade", "crossfade", "warp", "time stretch", "pitch shift", "reverse", "gain", "silence", "transient editing"), ("audio edit", "warp", "region")),
        _m("music", "vocals", "Vocals", "Vocal production and creative treatment.", ("pitch correction", "manual tuning", "formant", "de-ess", "alignment", "double", "harmony", "choir", "vocal chain", "breath control"), ("vocal", "tune", "harmony")),
        _m("music", "eq_filters", "EQ & Filters", "Frequency shaping and filtering.", ("parametric EQ", "dynamic EQ", "graphic EQ", "high pass", "low pass", "band pass", "notch", "tilt", "resonant filter", "auto filter"), ("eq", "filter")),
        _m("music", "dynamics", "Dynamics", "Level and transient control.", ("compressor", "limiter", "gate", "expander", "multiband dynamics", "transient shaper", "de-esser", "sidechain", "parallel compression"), ("compressor", "limiter", "dynamics")),
        _m("music", "saturation_distortion", "Saturation & Distortion", "Harmonic colour and nonlinear processing.", ("tube", "tape", "console", "soft clip", "hard clip", "overdrive", "fuzz", "bit crush", "wavefold", "amp", "cabinet"), ("saturation", "distortion", "drive")),
        _m("music", "delay_reverb", "Delay & Reverb", "Time and space effects.", ("room", "hall", "plate", "spring", "convolution", "algorithmic reverb", "echo", "tape delay", "ping pong", "multitap", "shimmer"), ("reverb", "delay", "echo")),
        _m("music", "modulation", "Modulation", "Periodic and pitch/time modulation effects.", ("chorus", "flanger", "phaser", "tremolo", "vibrato", "rotary", "ensemble", "ring modulation", "frequency shifting"), ("chorus", "phaser", "modulation")),
        _m("music", "spatial", "Spatial & Stereo", "Stereo image and immersive placement tools.", ("pan", "stereo width", "mid side", "binaural", "early reflections", "depth", "movement", "mono compatibility", "spatial automation"), ("stereo", "spatial", "width")),
        _m("music", "spectral", "Spectral", "Frequency-domain creative and corrective processing.", ("spectral gate", "spectral blur", "spectral freeze", "resynthesis", "harmonic selection", "spectral repair", "tonal balance"), ("spectral", "frequency")),
        _m("music", "glitch_granular", "Granular & Creative", "Experimental sound design processors.", ("granular", "stutter", "repeat", "buffer scramble", "reverse grains", "pitch grains", "time freeze", "randomiser", "rhythmic gate"), ("granular", "glitch", "stutter")),
        _m("music", "restoration", "Cleanup & Restoration", "Corrective audio repair.", ("denoise", "dehum", "declick", "declip", "dereverb", "room reduction", "vocal isolation", "plosive reduction", "spectral repair"), ("cleanup", "repair", "denoise")),
        _m("music", "stem_separation", "Stems & Separation", "Separate and recombine musical components.", ("vocals", "drums", "bass", "instruments", "guitar", "keys", "detailed stems", "rebalance", "stem export"), ("stems", "separate")),
        _m("music", "mixing_routing", "Mixing & Routing", "Console, bus and routing workflows.", ("faders", "pan", "sends", "returns", "groups", "buses", "parallel paths", "sidechain routing", "metering", "AutoMix suggestions"), ("mix", "mixer", "routing")),
        _m("music", "mastering", "Mastering", "Single, album and reference mastering.", ("reference matching", "master EQ", "multiband", "stereo image", "saturation", "clipping", "limiting", "loudness targets", "true peak", "translation report"), ("master", "loudness", "reference")),
        _m("music", "automation", "Automation", "Time-varying parameter and macro control.", ("volume", "pan", "plugin parameters", "send levels", "tempo", "macro controls", "LFO", "envelope followers", "drawn automation"), ("automation", "keyframe", "macro")),
        _m("music", "adaptive_audio", "Adaptive & Interactive Audio", "Music and sound that reacts to external state.", ("intensity states", "stem switching", "transition stingers", "game events", "scene events", "loop regions", "adaptive ambience"), ("adaptive", "game music", "interactive")),
        _m("music", "aura_chain", "Aura Effect Chain Creator", "Prompt-to-editable bounded DSP/effect-chain creation.", ("prompt chain", "macro mapping", "parallel chain", "multiband chain", "vocal chain", "instrument chain", "master chain", "save reusable rack"), ("aura", "chain", "rack")),
        _m("music", "export_delivery", "Export & Delivery", "Professional audio output and package generation.", ("mixdown", "stems", "masters", "alternate versions", "instrumental", "acapella", "sample rate", "bit depth", "loudness", "metadata"), ("export", "bounce", "render"), graph_editable=False),
    ),
    "game": (
        _m("game", "project_templates", "Projects & Templates", "Original starting systems for common game structures.", ("2D platformer", "top-down", "puzzle", "adventure", "racing", "survival", "RPG", "simulation", "sandbox", "narrative", "multiplayer starter"), ("template", "starter")),
        _m("game", "world_terrain", "World & Terrain", "Build and edit playable spaces.", ("terrain", "height", "biomes", "water", "roads", "paths", "foliage", "caves", "world partition", "level streaming"), ("world", "terrain", "level")),
        _m("game", "environment", "Environment", "Reusable environment systems.", ("sky", "day night", "weather", "fog", "wind", "ocean", "rivers", "vegetation", "desert", "snow", "space", "interiors"), ("environment", "weather", "sky")),
        _m("game", "scene_entities", "Scenes & Entities", "Scene graph, entity and component authoring.", ("entities", "components", "prefabs", "collections", "spawn", "despawn", "tags", "layers", "triggers", "zones"), ("entity", "component", "scene")),
        _m("game", "gameplay", "Gameplay Systems", "Reusable mechanics and interaction components.", ("interaction", "pickup", "doors", "switches", "checkpoints", "health", "damage", "abilities", "cooldowns", "objectives", "score"), ("gameplay", "mechanic", "system")),
        _m("game", "movement", "Movement & Controllers", "Player and vehicle controller systems.", ("walk", "run", "jump", "crouch", "climb", "swim", "fly", "dash", "grapple", "vehicle", "mount", "zero gravity"), ("movement", "controller")),
        _m("game", "combat", "Combat", "Combat, weapon and damage systems.", ("melee", "projectile", "hitscan", "combo", "block", "dodge", "parry", "lock on", "weapon slots", "damage types", "status effects"), ("combat", "weapon", "damage")),
        _m("game", "inventory_crafting", "Inventory & Crafting", "Item, inventory and crafting systems.", ("inventory", "equipment", "stacking", "loot", "containers", "craft recipes", "resource gathering", "durability", "shops", "currency"), ("inventory", "craft", "item")),
        _m("game", "quests_dialogue", "Quests & Dialogue", "Narrative and progression logic.", ("dialogue tree", "choices", "quest graph", "objectives", "branching", "conditions", "reputation", "journal", "cutscene triggers"), ("quest", "dialogue", "story")),
        _m("game", "ai_npc", "AI & NPCs", "Bounded agent and NPC behaviour systems.", ("state machine", "behaviour tree", "utility AI", "navigation", "perception", "combat AI", "schedules", "companions", "crowds", "conversation hooks"), ("npc", "ai", "behavior")),
        _m("game", "camera", "Camera", "Gameplay and cinematic camera systems.", ("follow", "orbit", "first person", "third person", "top down", "rail", "shake", "collision", "camera zones", "cinematic shots", "photo mode"), ("camera", "cinematic")),
        _m("game", "animation_rig", "Animation & Rigging", "Character and object animation systems.", ("skeleton", "rig", "IK", "retarget", "state machine", "blend tree", "root motion", "facial", "ragdoll blend", "procedural animation"), ("animation", "rig", "ik")),
        _m("game", "physics_destruction", "Physics & Destruction", "Physical simulation and breakable systems.", ("rigid body", "colliders", "constraints", "ragdoll", "cloth", "rope", "buoyancy", "vehicles", "destruction", "fracture", "debris"), ("physics", "destruction", "collision")),
        _m("game", "materials_shaders", "Materials & Shaders", "Editable real-time material and shader graphs.", ("PBR", "toon", "water", "glass", "hologram", "dissolve", "outline", "terrain", "decal", "post material", "procedural texture"), ("material", "shader", "pbr")),
        _m("game", "vfx_particles", "VFX & Particles", "Real-time effect systems and emitters.", ("fire", "smoke", "explosion", "sparks", "magic", "trail", "beam", "lightning", "weather", "shockwave", "dissolve", "portal", "impact", "environment VFX"), ("vfx", "particle", "effect")),
        _m("game", "lighting_post", "Lighting & Post", "Real-time lighting and camera post effects.", ("directional", "point", "spot", "area", "baked lighting", "GI adapters", "fog", "bloom", "colour grade", "depth of field", "motion blur", "exposure"), ("lighting", "post process")),
        _m("game", "audio_adaptive", "Audio & Adaptive Music", "Interactive audio systems.", ("3D audio", "attenuation", "reverb zones", "footsteps", "surface sounds", "adaptive score", "music states", "stingers", "dialogue audio", "mix snapshots"), ("audio", "adaptive music", "sfx")),
        _m("game", "ui_hud", "UI & HUD", "Game interface and menu systems.", ("HUD", "menus", "inventory UI", "dialogue UI", "map", "minimap", "quest tracker", "health bars", "tooltips", "controller navigation", "accessibility"), ("ui", "hud", "menu")),
        _m("game", "save_progression", "Save & Progression", "Persistence and progression systems.", ("save slots", "autosave", "checkpoints", "settings", "achievements", "XP", "levels", "skills", "unlocks", "cloud-save adapter"), ("save", "progression", "achievement")),
        _m("game", "network_multiplayer", "Networking & Multiplayer", "Server-authoritative and peer-session adapters where approved.", ("lobby", "rooms", "matchmaking", "replication", "prediction", "interpolation", "ownership", "RPC", "chat", "party", "spectator", "reconnect"), ("multiplayer", "network", "lobby")),
        _m("game", "live_ops", "Live Ops", "Operational systems for published games.", ("events", "seasons", "remote configuration", "feature flags", "announcements", "telemetry", "leaderboards", "challenges", "content rotation"), ("live ops", "season", "event")),
        _m("game", "procedural", "Procedural Generation", "Seeded and rule-driven content generation.", ("dungeon", "terrain", "roads", "rooms", "loot", "quests", "encounters", "vegetation", "cities", "planet systems", "random seeds"), ("procedural", "generator", "seed")),
        _m("game", "runtime_live_creation", "Live Creation Runtime", "Voice/text-directed bounded world mutation while a game is running.", ("live scenery change", "spawn bounded entity", "weather shift", "lighting shift", "music state", "quest event", "scene transition", "persistent world delta", "undo rollback"), ("live creation", "voice world", "runtime aura")),
        _m("game", "portals_teleport", "Portals & Teleport", "Composable traversal and transition systems.", ("portal", "teleport", "wormhole", "dissolve", "star travel", "arrival shockwave", "destination validation", "network replication"), ("portal", "teleport", "wormhole")),
        _m("game", "visual_scripting", "Visual Scripting", "Typed event and gameplay graphs.", ("events", "conditions", "actions", "variables", "timers", "loops", "state", "signals", "component calls", "debug trace"), ("node", "visual script", "graph")),
        _m("game", "code_tools", "Code & Aura Copilot", "Project-scoped code tools with diff and test review.", ("editor", "completion", "refactor", "explain", "generate component", "test generation", "diff review", "API reference", "sandbox build"), ("code", "script", "aura")),
        _m("game", "test_debug", "Test & Debug", "Playtest, validation and diagnostic tools.", ("playtest", "console", "profiler", "collision view", "network stats", "AI debug", "save inspector", "automated tests", "world integrity scan"), ("test", "debug", "profile")),
        _m("game", "package_publish", "Package & Publish", "Build, validate and publish eligible games.", ("web build", "desktop package", "mobile package", "versioning", "content validation", "rights scan", "store metadata", "marketplace publish"), ("build", "publish", "package"), graph_editable=False),
        _m("game", "advanced_engine", "Advanced Engine Lane", "Entitlement-gated advanced-engine project orchestration without rebranding third-party engines.", ("advanced project handoff", "asset pipeline", "build orchestration", "source project export", "engine adapter", "licence boundary"), ("advanced engine", "high fidelity"), graph_editable=False),
    ),
    "voice": (
        _m("voice", "record_import", "Record & Import", "Capture and ingest speech or singing audio.", ("record", "upload", "mic selection", "latency", "takes", "waveform"), ("record", "voice")),
        _m("voice", "cleanup", "Cleanup", "Speech and vocal restoration.", ("denoise", "dehum", "declick", "declip", "dereverb", "plosive reduction", "breath control", "speech isolation"), ("cleanup", "studio sound")),
        _m("voice", "eq_dynamics", "Tone & Dynamics", "Voice-focused EQ and dynamics.", ("EQ", "compressor", "de-esser", "gate", "expander", "limiter", "presence", "air", "warmth"), ("eq", "compressor", "deess")),
        _m("voice", "pitch_formant", "Pitch & Formant", "Corrective and creative pitch/timbre controls.", ("pitch correction", "manual pitch", "formant", "gender-neutral timbre shift", "harmoniser", "octave", "robotic"), ("pitch", "formant")),
        _m("voice", "timing", "Timing & Delivery", "Edit timing while preserving intelligibility.", ("time stretch", "pause edit", "word timing", "alignment", "filler removal", "silence tightening", "cadence"), ("timing", "pause", "cadence")),
        _m("voice", "speech_generation", "Speech Generation", "Consent-aware multilingual speech generation.", ("text to speech", "style", "speed", "stability", "pronunciation", "multilingual", "long form"), ("tts", "speech")),
        _m("voice", "singing", "Singing", "Consent-controlled singing and performance workflows.", ("singing synthesis", "melody guide", "lyrics timing", "harmony", "vibrato", "dynamics", "approved voice profile"), ("sing", "vocal")),
        _m("voice", "voice_to_voice", "Voice to Voice", "Performance-preserving timbre conversion with consent checks.", ("voice conversion", "prosody preservation", "emotion preservation", "accent preservation", "character timbre", "strength"), ("voice changer", "voice conversion")),
        _m("voice", "emotion_prosody", "Emotion & Prosody", "Control delivery, pacing and expression.", ("emotion", "energy", "pace", "emphasis", "whisper", "narration", "character performance", "reference performance"), ("emotion", "prosody", "delivery")),
        _m("voice", "dubbing_translation", "Dubbing & Translation", "Localised speech workflows.", ("transcribe", "translate", "speaker diarisation", "timing match", "approved speaker voice", "dub", "language package"), ("dub", "translate", "language")),
        _m("voice", "transcription", "Transcription", "Time-aligned speech recognition and text editing.", ("speech to text", "timestamps", "diarisation", "speaker labels", "word confidence", "caption export", "transcript edit"), ("transcribe", "stt")),
        _m("voice", "lipsync", "Lip Sync & Visemes", "Create phoneme/viseme timing for 2D/3D targets.", ("phonemes", "visemes", "mouth cues", "timing", "avatar lipsync", "video lipsync"), ("lip sync", "viseme")),
        _m("voice", "spatial", "Spatial Voice", "Place and move voices in a sound field.", ("pan", "distance", "room", "early reflections", "binaural", "movement", "radio", "telephone", "megaphone"), ("spatial", "room", "radio")),
        _m("voice", "profiles_consent", "Voice Profiles & Consent", "Purpose-scoped identity, consent and revocation controls.", ("voice profile", "consent evidence", "allowed purposes", "revocation", "provenance", "ownership verification", "audit"), ("consent", "profile", "rights"), graph_editable=False),
        _m("voice", "export", "Export", "Render speech, stems and localisation packages.", ("WAV", "MP3", "stems", "transcript", "subtitles", "dub package", "viseme data"), ("export", "render"), graph_editable=False),
    ),
    "live": (
        _m("live", "scenes", "Scenes", "Build and switch live production scenes.", ("scene", "scene collection", "transition", "preview", "program", "hotkey", "scene automation"), ("scene", "switch")),
        _m("live", "sources", "Sources", "Add bounded live production sources.", ("camera", "screen", "window", "media", "image", "browser source", "text", "audio", "game capture adapter"), ("source", "camera", "screen")),
        _m("live", "overlays", "Overlays", "Reusable visual overlay systems.", ("lower third", "frame", "chat box", "supporter feed", "leaderboard", "ticker", "logo", "watermark", "CTA"), ("overlay", "lower third")),
        _m("live", "video_filters", "Video Filters", "Source-level visual processing.", ("crop", "scale", "colour correction", "LUT", "chroma key", "luma key", "sharpen", "blur", "mask", "background"), ("filter", "camera")),
        _m("live", "audio_filters", "Audio Filters", "Live audio cleanup and processing.", ("gain", "noise suppression", "noise gate", "compressor", "limiter", "EQ", "de-esser", "expander", "ducking", "delay"), ("mic", "audio filter")),
        _m("live", "alerts", "Alerts", "Event-driven supporter and channel alerts.", ("follow", "subscribe", "gift", "milestone", "raid-style event", "custom event", "sound", "animation", "queue"), ("alert", "gift", "supporter")),
        _m("live", "goals", "Goals & Progress", "Progress visualisations for live objectives.", ("goal bar", "counter", "streak", "milestone", "timer", "progress ring", "team goal"), ("goal", "progress")),
        _m("live", "widgets", "Widgets", "Interactive bounded live widgets.", ("clock", "timer", "countdown", "wheel", "poll", "scoreboard", "leaderboard", "now playing", "QR", "CTA"), ("widget", "timer", "wheel")),
        _m("live", "captions_titles", "Captions & Titles", "Real-time captions and text graphics.", ("live captions", "speaker labels", "translation captions", "lower thirds", "topic banner", "lyrics", "ticker"), ("caption", "title")),
        _m("live", "particles_reactive", "Reactive Effects", "Audio/event-reactive visual systems.", ("hearts", "confetti", "stars", "sparks", "energy", "weather", "spectrum", "waveform", "beat pulse", "gift-triggered effect"), ("particle", "reactive", "visualizer")),
        _m("live", "virtual_camera", "Camera & Keying", "Camera presentation and background controls.", ("virtual camera", "chroma key", "background blur", "background replace", "portrait frame", "camera crop", "camera movement"), ("virtual camera", "green screen")),
        _m("live", "moderation", "Guardian & Moderation", "Creator-authorised moderation assistance within provider permissions.", ("chat signals", "risk cues", "blocked terms", "human review", "moderation suggestions", "provider-authorised action", "audit"), ("guardian", "moderation", "safety"), graph_editable=False),
        _m("live", "automation", "Automation", "Typed Trigger → Condition → Approved Action live workflows.", ("event trigger", "timer trigger", "audio trigger", "scene action", "overlay action", "approved media action", "cooldown", "rate limit"), ("automation", "trigger", "action")),
        _m("live", "record_replay", "Record & Replay", "Capture and reuse live moments.", ("record", "replay buffer", "clip marker", "highlight", "scene recording", "ISO-style source recording where supported"), ("record", "replay", "clip")),
        _m("live", "analytics", "Live Analytics", "Freshness-labelled live performance analytics.", ("viewers", "retention", "supporter events", "chat rate", "goal progress", "scene timeline", "post-show report"), ("analytics", "retention"), graph_editable=False),
    ),
    "social": (
        _m("social", "composer", "Composer", "Create channel-ready posts from project assets.", ("text", "image", "video", "carousel", "caption", "CTA", "mentions", "links"), ("post", "composer")),
        _m("social", "calendar", "Calendar", "Plan and organise campaigns and posts.", ("calendar", "campaign", "draft", "approval", "schedule", "reminder", "content pillar"), ("calendar", "schedule"), graph_editable=False),
        _m("social", "repurpose", "Repurpose & Resize", "Turn one source into platform-specific variants.", ("aspect ratios", "crop", "reframe", "duration variants", "caption variants", "thumbnail", "cover", "safe areas"), ("repurpose", "resize", "variant")),
        _m("social", "clips", "Clips & Hooks", "Extract and optimise short-form clips.", ("highlight", "hook", "AI clipper", "silence trim", "speaker focus", "caption opening", "loop ending", "CTA ending"), ("clip", "hook", "short")),
        _m("social", "captions", "Captions & Copy", "Generate editable copy and caption variants.", ("caption", "headline", "description", "CTA", "tone variants", "length variants", "accessibility text", "alt text"), ("caption", "copy", "cta")),
        _m("social", "hashtags_seo", "Discovery & SEO", "Research and organise discoverability metadata.", ("hashtags", "keywords", "search phrases", "title optimisation", "description", "topic clusters", "metadata"), ("hashtag", "seo", "keyword"), graph_editable=False),
        _m("social", "covers", "Thumbnails & Covers", "Create platform-aware visual packaging.", ("thumbnail", "cover", "poster frame", "title card", "series identity", "episode number", "face-safe layout"), ("thumbnail", "cover")),
        _m("social", "brand", "Brand Kits", "Reusable brand systems across outputs.", ("logos", "fonts", "colours", "templates", "lower thirds", "watermarks", "CTA", "voice and tone"), ("brand", "kit")),
        _m("social", "campaign_templates", "Campaign Templates", "Multi-post reusable campaign structures.", ("launch", "event", "announcement", "countdown", "education series", "testimonial", "creator recruitment", "product campaign"), ("campaign", "template")),
        _m("social", "ads", "Ads & Promotions", "Create and test promotional variants.", ("ad creative", "headline variants", "CTA variants", "product creative", "testimonial", "offer card", "A/B variants"), ("ad", "promotion", "ab")),
        _m("social", "product", "Product Creative", "Commerce-oriented social creative systems.", ("product photo", "product video", "catalogue card", "feature callout", "price card", "demo", "before after"), ("product", "commerce")),
        _m("social", "publish", "Publish & Schedule", "Provider-aware publishing with truthful success state.", ("schedule", "queue", "manual reminder", "provider publish", "retry", "failed state", "published confirmation"), ("publish", "schedule"), graph_editable=False),
        _m("social", "analytics", "Analytics", "Freshness-labelled performance reporting.", ("views", "watch time", "retention", "engagement", "clicks", "followers", "conversion", "content comparison"), ("analytics", "performance"), graph_editable=False),
        _m("social", "trends", "Trends & Research", "Research current formats, topics and opportunities.", ("trend research", "topic signals", "format patterns", "competitor public content research", "seasonality", "opportunity score"), ("trend", "research"), graph_editable=False),
        _m("social", "growth", "Aura Growth Coach", "Project-aware creator growth recommendations.", ("content plan", "posting cadence", "live promotion", "hook suggestions", "retention suggestions", "repurpose plan", "experiment plan"), ("growth", "coach", "aura"), graph_editable=False),
        _m("social", "approvals", "Approvals & Team", "Controlled review and approval workflows.", ("draft", "review", "approve", "reject", "comment", "version", "role boundary", "audit"), ("approval", "review", "team"), graph_editable=False),
    ),
}


PUBLIC_BLOCKED_BRAND_TERMS = (
    "capcut", "adobe", "premiere", "after effects", "photoshop", "davinci", "resolve",
    "filmora", "canva", "ableton", "logic pro", "unreal", "unity", "godot", "obs",
    "elevenlabs", "descript",
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
            public_text = " ".join((menu.id, menu.label, menu.description, *menu.feature_families, *menu.search_terms)).casefold()
            if any(term in public_text for term in PUBLIC_BLOCKED_BRAND_TERMS):
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
    "STUDIO_MENUS",
    "DiscoverySection",
    "EffectBand",
    "StudioMenu",
    "public_studio_catalogue",
    "validate_studio_menus",
]
