from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class Capability:
    id: str
    domain: str
    category: str
    label: str
    implementation: str
    status: str = "planned_original"
    surfaces: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    notes: str = ""

    def public(self) -> dict:
        row = asdict(self)
        row["surfaces"] = list(self.surfaces)
        row["dependencies"] = list(self.dependencies)
        return row


def _c(
    id: str,
    domain: str,
    category: str,
    label: str,
    implementation: str,
    *,
    status: str = "planned_original",
    surfaces: Iterable[str] = (),
    dependencies: Iterable[str] = (),
    notes: str = "",
) -> Capability:
    return Capability(
        id=id,
        domain=domain,
        category=category,
        label=label,
        implementation=implementation,
        status=status,
        surfaces=tuple(surfaces),
        dependencies=tuple(dependencies),
        notes=notes,
    )


# This registry intentionally describes capability families rather than competitor brands.
# It is the product-level contract for building original ESP/Aura equivalents without
# copying third-party source code, protected assets, closed model weights or branding.
CAPABILITIES: tuple[Capability, ...] = (
    # Music creation / DAW / recording
    _c("music.text_to_song", "music", "generation", "Text / lyrics to complete song", "Provider-neutral generation job with editable arrangement handoff", status="existing_foundation", surfaces=("music_studio", "aura")),
    _c("music.audio_to_arrangement", "music", "generation", "Build a full arrangement around uploaded audio", "Song-DNA analysis + section-aware multitrack generation", status="existing_foundation", surfaces=("music_studio",)),
    _c("music.song_dna", "music", "analysis", "Tempo, key, chord, section and groove analysis", "Local analysis pipeline with editable metadata", status="existing_foundation", surfaces=("music_studio", "aura")),
    _c("music.variable_tempo_map", "music", "editing", "Performance-following variable tempo map", "Transient/onset map + editable tempo anchors", status="existing_foundation", surfaces=("music_studio",)),
    _c("music.multitrack_daw", "music", "editing", "Browser multitrack DAW", "Non-destructive session model, clips, buses, routing and automation", status="existing_foundation", surfaces=("music_studio",)),
    _c("music.instrument_library", "music", "library", "Instrument and ensemble library", "Typed instrument catalogue with plan-aware availability", status="existing_foundation", surfaces=("music_studio", "game_forge")),
    _c("music.effects_library", "music", "library", "Audio effects and pedal library", "Safe DSP effect registry + presets + AI chain designer", status="existing_foundation", surfaces=("music_studio", "live")),
    _c("music.plugin_rack", "music", "editing", "Approved local plugin rack", "Allowlisted plugin discovery and render boundary", status="existing_foundation", surfaces=("music_studio",)),
    _c("music.stem_separation", "music", "editing", "2/4/6/detailed stem separation", "Model-adapter separator with tenant-bound outputs", status="existing_foundation", surfaces=("music_studio", "video_studio")),
    _c("music.audio_to_midi", "music", "analysis", "Audio to MIDI / note extraction", "Polyphonic pitch-event extraction with confidence editing", surfaces=("music_studio",)),
    _c("music.chord_assistant", "music", "composition", "Chord, scale and progression assistant", "Theory engine + style-conditioned suggestions", surfaces=("music_studio", "aura")),
    _c("music.melody_assistant", "music", "composition", "Melody, bass and counter-melody generator", "Constraint-based phrase generator with regenerate-by-region", surfaces=("music_studio", "aura")),
    _c("music.rhythm_generator", "music", "composition", "Groove, drum and percussion generator", "Pattern engine with swing/humanisation and genre templates", surfaces=("music_studio",)),
    _c("music.smart_warp", "music", "editing", "Elastic audio, time-stretch and pitch-shift", "Transient-aware warping with formant-preserving options", surfaces=("music_studio",)),
    _c("music.vocal_tune", "music", "vocals", "Pitch correction and creative vocal tuning", "Key-aware Aura Tune modes and manual note correction", status="existing_foundation", surfaces=("music_studio",)),
    _c("music.vocal_alignment", "music", "vocals", "Backing-vocal timing alignment", "Guide-to-double alignment with phrase controls", surfaces=("music_studio",)),
    _c("music.harmony_generator", "music", "vocals", "Backing harmony and choir arrangement", "Harmony planner + consent-safe voice renderer", surfaces=("music_studio",)),
    _c("music.voice_conversion", "music", "vocals", "Consent-approved singing voice conversion", "Voice profile registry + explicit consent + provenance", status="existing_foundation", surfaces=("music_studio", "voice_studio")),
    _c("music.automix", "music", "mixing", "AI-assisted mixing", "Track analysis + reversible level/pan/EQ/dynamics suggestions", status="existing_foundation", surfaces=("music_studio",)),
    _c("music.mastering", "music", "mastering", "Single and album mastering", "Character presets, loudness targets, reference mastering and translation report", status="existing_foundation", surfaces=("music_studio",)),
    _c("music.remote_collaboration", "music", "collaboration", "Remote session review and timestamp comments", "Project roles, versioned reviews and lossless preview links", surfaces=("music_studio", "projects")),
    _c("music.live_recording", "music", "recording", "Multi-input browser recording", "Device selection, latency calibration, takes, punch-in and comping", surfaces=("music_studio",)),
    _c("music.soundid_reference", "music", "monitoring", "Reference-monitor simulation and translation checks", "Headphone/speaker target profiles without claiming third-party room models", surfaces=("music_studio",)),
    _c("music.sample_manager", "music", "library", "Semantic sample and loop manager", "Local embeddings + acoustic descriptors + rights metadata", surfaces=("music_studio", "video_studio", "game_forge")),
    _c("music.adaptive_score", "music", "generation", "Adaptive / interactive score system", "Stems and intensity states driven by timeline or game events", surfaces=("game_forge", "video_studio")),

    # Voice / speech / dubbing
    _c("voice.tts", "voice", "speech", "Multilingual text to speech", "Provider-neutral TTS router with local/provider adapters", status="existing_foundation", surfaces=("aura", "voice_studio", "video_studio", "live")),
    _c("voice.clone", "voice", "speech", "Consent-controlled personal voice clone", "Verified ownership/consent, dataset QA, model/profile lifecycle", status="existing_foundation", surfaces=("voice_studio", "aura")),
    _c("voice.voice_to_voice", "voice", "speech", "Voice-to-voice timbre conversion", "Prosody-preserving conversion adapter with consent enforcement", surfaces=("voice_studio", "music_studio")),
    _c("voice.emotion_control", "voice", "speech", "Emotion, delivery and performance control", "Style tokens / reference-performance controls where supported", surfaces=("voice_studio", "aura")),
    _c("voice.realtime_changer", "voice", "realtime", "Low-latency real-time voice transformation", "Native audio bridge + bounded model pipeline", surfaces=("live", "voice_studio"), dependencies=("native_audio_client",)),
    _c("voice.dubbing", "voice", "localisation", "Translation, dubbing and speaker matching", "Transcript segmentation + translation + timing + approved speaker profiles", status="existing_foundation", surfaces=("video_studio", "voice_studio")),
    _c("voice.lipsync", "voice", "localisation", "Audio-driven lip synchronization", "Phoneme/viseme timeline + 2D/3D renderer adapters", status="existing_foundation", surfaces=("video_studio", "aura")),
    _c("voice.cleanup", "voice", "restoration", "Speech isolation, de-reverb and denoise", "Local DSP/model adapters with before/after preview", surfaces=("voice_studio", "video_studio", "music_studio")),
    _c("voice.transcription", "voice", "speech", "Speech-to-text, diarisation and timestamps", "Local/provider STT router with speaker labels", status="existing_foundation", surfaces=("voice_studio", "video_studio", "aura", "creator_hub")),

    # Video creation / editing / VFX
    _c("video.timeline", "video", "editing", "Professional multi-track video timeline", "Non-destructive clips, nested sequences, keyframes and proxies", status="existing_foundation", surfaces=("video_studio",)),
    _c("video.text_to_video", "video", "generation", "Text to video", "Provider-neutral generation router + provenance + project asset import", status="existing_foundation", surfaces=("video_studio", "aura")),
    _c("video.image_to_video", "video", "generation", "Image to video / motion", "Reference image + camera/motion controls + generation adapters", status="existing_foundation", surfaces=("video_studio", "image_studio")),
    _c("video.video_to_video", "video", "generation", "Video restyle / guided transformation", "Structure-preserving model adapter with strength masks", surfaces=("video_studio",)),
    _c("video.object_remove", "video", "vfx", "Object removal and inpainting", "Tracked masks + temporal inpaint workflow", surfaces=("video_studio",)),
    _c("video.rotoscope", "video", "vfx", "Smart masking and rotoscoping", "Tracked segmentation masks with manual refine tools", surfaces=("video_studio", "image_studio")),
    _c("video.background_remove", "video", "vfx", "Background removal / replacement", "Human/object segmentation + spill cleanup", status="existing_foundation", surfaces=("video_studio", "live")),
    _c("video.smart_reframe", "video", "editing", "Automatic reframing for vertical/square/wide", "Subject/speaker tracking + safe-title zones", surfaces=("video_studio", "social_manager")),
    _c("video.auto_captions", "video", "editing", "Caption generation and style library", "Transcript-to-caption timeline + reusable animated presets", status="existing_foundation", surfaces=("video_studio", "social_manager", "live")),
    _c("video.silence_bad_take_cut", "video", "editing", "Silence, filler and bad-take detection", "Transcript/acoustic heuristics with reversible edit suggestions", surfaces=("video_studio",)),
    _c("video.text_edit", "video", "editing", "Transcript-based video editing", "Text selections map to source-time edits", surfaces=("video_studio",)),
    _c("video.scene_detection", "video", "analysis", "Scene, shot and speaker detection", "Visual/audio boundary analysis with chapter markers", surfaces=("video_studio",)),
    _c("video.multicam_sync", "video", "editing", "Multi-camera synchronization", "Waveform/timecode alignment + angle switching", surfaces=("video_studio", "live")),
    _c("video.speed_ramp", "video", "editing", "Speed ramps, optical flow and frame interpolation", "Keyframed retiming with quality modes", surfaces=("video_studio",)),
    _c("video.upscale_restore", "video", "restoration", "Upscale, denoise, deblur and deinterlace", "Pluggable restoration pipeline with model/version provenance", surfaces=("video_studio", "image_studio")),
    _c("video.color_grading", "video", "editing", "Color correction, grading and LUT library", "Scopes, curves, wheels, LUT import and Aura matching assistant", surfaces=("video_studio",)),
    _c("video.transitions", "video", "library", "Transition and motion preset library", "Schema-defined keyframed transitions with searchable tags", surfaces=("video_studio", "social_manager")),
    _c("video.effects_catalog", "video", "library", "VFX, filters, particles and compositing catalogue", "Declarative effect graph + safe shader/plugin adapters", surfaces=("video_studio", "live")),
    _c("video.audio_design", "video", "audio", "Automatic foley and sound-design placement", "Event detection + rights-safe SFX search/generation", surfaces=("video_studio", "game_forge")),
    _c("video.social_clips", "video", "repurpose", "Long-form to shorts/highlights", "Hook scoring, speaker tracking, reframing and caption templates", status="existing_foundation", surfaces=("video_studio", "social_manager")),
    _c("video.brand_templates", "video", "library", "Brand kits, title systems and reusable templates", "Project/brand scoped template registry", surfaces=("video_studio", "image_studio", "social_manager")),
    _c("video.localization", "video", "localisation", "Multi-language subtitle and dubbing packages", "Translation memory + captions + approved voice profiles", surfaces=("video_studio",)),
    _c("video.avatar_actor", "video", "generation", "Talking avatar / digital presenter", "Consent-controlled avatar profile + TTS/lipsync + scene compositor", surfaces=("video_studio", "aura")),
    _c("video.cgi_actor_replace", "video", "vfx", "Tracked character replacement / CGI performer", "Pose/camera solve + user-owned/licensed 3D character workflow", surfaces=("video_studio", "game_forge")),

    # Image / design
    _c("image.text_to_image", "image", "generation", "Text to image", "Provider-neutral image generation with project provenance", status="existing_foundation", surfaces=("image_studio", "aura")),
    _c("image.image_edit", "image", "editing", "Instruction-based image editing", "Mask/reference-aware edit router with undo history", status="existing_foundation", surfaces=("image_studio",)),
    _c("image.inpaint_outpaint", "image", "editing", "Inpainting, generative fill and outpainting", "Mask/canvas expansion workflow", status="existing_foundation", surfaces=("image_studio",)),
    _c("image.background", "image", "editing", "Background remove / replace / generate", "Segmentation + generative background + edge refinement", status="existing_foundation", surfaces=("image_studio",)),
    _c("image.typography", "image", "design", "Poster and typography layout engine", "Editable text layers, templates, font metadata and safe-area rules", status="existing_foundation", surfaces=("image_studio", "social_manager")),
    _c("image.batch", "image", "workflow", "Batch resize, crop, upscale and export", "Queued non-destructive variants with naming rules", surfaces=("image_studio", "social_manager")),
    _c("image.vector", "image", "design", "Vector shapes, paths and logo construction", "SVG-native editable layer model", surfaces=("image_studio",)),
    _c("image.asset_styles", "image", "library", "Style, texture, brush and material libraries", "Taggable preset registry with user/brand packs", surfaces=("image_studio", "game_forge")),
    _c("image.product_mockups", "image", "design", "Mockups and scene placement", "Perspective-aware smart objects / scene templates", surfaces=("image_studio", "marketplace")),
    _c("image.consistent_character", "image", "generation", "Consistent character / subject generation", "Reference identity pack + style controls + provenance", surfaces=("image_studio", "game_forge", "video_studio")),

    # 3D / game creation
    _c("game.text_to_game", "game", "generation", "Prompt to playable game prototype", "Game spec -> asset plan -> code graph -> sandbox build", status="existing_foundation", surfaces=("game_forge", "aura")),
    _c("game.visual_scripting", "game", "authoring", "Node / event visual scripting", "Typed event graph compiling to bounded runtime actions", status="existing_foundation", surfaces=("game_forge",)),
    _c("game.code_editor", "game", "authoring", "Integrated code editor and AI copilot", "Project-scoped code generation, diff review, tests and sandbox", status="existing_foundation", surfaces=("game_forge", "aura")),
    _c("game.scene_editor", "game", "authoring", "2D/3D scene and level editor", "Entity/component scene graph + gizmos + undo/redo", status="existing_foundation", surfaces=("game_forge",)),
    _c("game.asset_3d_generation", "game", "assets", "Text/image to 3D asset", "Provider/self-host adapter -> validation -> optimisation -> library", status="existing_foundation", surfaces=("game_forge", "video_studio")),
    _c("game.rig_animation", "game", "assets", "Rigging, retargeting and animation", "Humanoid skeleton mapper + clip library + IK", status="existing_foundation", surfaces=("game_forge", "aura")),
    _c("game.texture_material", "game", "assets", "PBR texture and material generation", "Tileable maps + PBR channel validation", status="existing_foundation", surfaces=("game_forge", "image_studio")),
    _c("game.skybox_world", "game", "world", "Skybox, terrain and world generation", "Procedural terrain graph + 360 environment adapters", surfaces=("game_forge",)),
    _c("game.procgen", "game", "world", "Procedural levels, cities, dungeons and biomes", "Seeded rule graph with editable constraints", surfaces=("game_forge",)),
    _c("game.npc_ai", "game", "ai", "Conversational NPC memory and goals", "Bounded character agent with state, lore and moderation", status="existing_foundation", surfaces=("game_forge",)),
    _c("game.dialogue_quests", "game", "narrative", "Branching dialogue, quest and story editor", "Graph-based narrative state machine + Aura authoring", surfaces=("game_forge",)),
    _c("game.physics", "game", "runtime", "Physics, collision and character controllers", "Runtime abstraction supporting web and export targets", status="existing_foundation", surfaces=("game_forge",)),
    _c("game.multiplayer", "game", "runtime", "Authoritative multiplayer services", "Rooms, presence, matchmaking, state replication and moderation", status="existing_foundation", surfaces=("game_forge",)),
    _c("game.liveops", "game", "runtime", "Leaderboards, achievements, inventory and live ops", "Tenant-aware game backend service", status="existing_foundation", surfaces=("game_forge",)),
    _c("game.exports", "game", "release", "Web/mobile/desktop engine export pipeline", "Target-specific validated export adapters", status="existing_foundation", surfaces=("game_forge",)),
    _c("game.audio_middleware", "game", "audio", "Interactive spatial and adaptive audio", "Event/state driven music/SFX graph", surfaces=("game_forge", "music_studio")),

    # LIVE / overlays / streaming
    _c("live.scene_builder", "live", "production", "Scene and overlay builder", "Browser-source scene graph with templates and permissions", status="existing_foundation", surfaces=("live",)),
    _c("live.alerts", "live", "engagement", "Alerts, goals, tickers and event lists", "Normalized event bus -> declarative widgets", status="existing_foundation", surfaces=("live",)),
    _c("live.reactive_fx", "live", "engagement", "Reactive particles, sounds and visual effects", "Bounded event-triggered effect catalogue", status="existing_foundation", surfaces=("live",)),
    _c("live.chat_aggregation", "live", "chat", "Multi-source live chat aggregation", "Approved provider connectors -> normalized chat model", surfaces=("live",)),
    _c("live.moderation", "live", "chat", "Chat moderation, filters and escalation", "Rules + model-assisted review + human controls", surfaces=("live", "creator_hub")),
    _c("live.interactive_games", "live", "engagement", "Trivia, polls, wheels, bingo and audience games", "Reusable widget/game runtime consuming normalized events", status="existing_foundation", surfaces=("live",)),
    _c("live.leaderboards", "live", "engagement", "Supporter leaderboards and milestones", "Tenant-scoped event aggregation and display", status="existing_foundation", surfaces=("live",)),
    _c("live.audio_visualizer", "live", "audio", "Audio spectrum and now-playing widgets", "WebAudio visualizers + approved media metadata", surfaces=("live", "music_studio")),
    _c("live.multistream", "live", "production", "Multi-destination broadcast routing", "RTMP/SRT relay control plane with provider-policy gates", surfaces=("live",), dependencies=("stream_relay_service",)),
    _c("live.avatar_tracking", "live", "avatar", "2D/3D avatar face/body tracking", "Web/native tracking adapters -> standardized rig signals", surfaces=("live", "aura")),
    _c("live.hardware_reactions", "live", "engagement", "Smart-light / device reactions", "Explicitly paired local bridge with action allowlist", surfaces=("live",), dependencies=("native_device_bridge",)),
    _c("live.compliance_guardian", "live", "safety", "Aura LIVE compliance guardian", "Advisory cueing, logging and human-controlled actions only", status="existing_foundation", surfaces=("live", "creator_hub")),

    # Aura / chat / agent workspace
    _c("aura.multimodal_chat", "aura", "assistant", "Multimodal persistent Aura workspace", "Project-aware chat, attachments, tools and result cards", status="existing_foundation", surfaces=("aura",)),
    _c("aura.memory", "aura", "assistant", "Permissioned project and user memory", "Scoped memory store with review/delete controls", status="existing_foundation", surfaces=("aura",)),
    _c("aura.tool_orchestration", "aura", "agent", "Tool-using workflow orchestration", "Typed tool registry, approvals, audit and retries", status="existing_foundation", surfaces=("aura", "owner_hub")),
    _c("aura.multi_agent", "aura", "agent", "Specialist agent teams", "Role-scoped planner/executor/reviewer graph with shared project state", surfaces=("aura",)),
    _c("aura.rag", "aura", "knowledge", "Private knowledge search / RAG", "Tenant-scoped hybrid search with citations and permissions", status="existing_foundation", surfaces=("aura", "creator_hub", "agent_hub", "owner_hub")),
    _c("aura.voice_realtime", "aura", "assistant", "Realtime duplex voice conversation", "Streaming STT/LLM/TTS with interruption and turn detection", surfaces=("aura",), dependencies=("realtime_voice_provider_or_selfhost",)),
    _c("aura.embodied_3d", "aura", "avatar", "Embodied 3D Aura companion", "VRM/GLB runtime, expression, gaze, gesture, navigation and energy states", status="existing_foundation", surfaces=("aura", "live")),
    _c("aura.translation", "aura", "assistant", "Live multilingual translation", "Speech/text translation router with speaker/context preservation", status="existing_foundation", surfaces=("aura", "creator_hub")),
    _c("aura.workflow_canvas", "aura", "agent", "Visual AI workflow / node canvas", "Typed nodes, data contracts, human approvals and versioning", surfaces=("aura", "automation")),
    _c("aura.local_models", "aura", "models", "Local/open model runtime", "Model registry supporting local inference endpoints and approved cloud providers", status="existing_foundation", surfaces=("aura", "owner_hub")),

    # Automation / browser / operations
    _c("automation.workflow_builder", "automation", "workflow", "No-code/low-code automation builder", "Trigger/condition/action graph with schemas, secrets vault and retries", status="existing_foundation", surfaces=("automation", "owner_hub")),
    _c("automation.browser_agent", "automation", "browser", "Supervised browser task agent", "Policy-bounded browser actions with domain permissions and human approval", surfaces=("automation", "aura")),
    _c("automation.rpa", "automation", "desktop", "Desktop/native RPA", "Signed local agent with explicit action allowlist and audit", surfaces=("automation",), dependencies=("signed_native_agent",)),
    _c("automation.scraping", "automation", "data", "Policy-compliant web extraction", "Robots/terms-aware fetch/browser pipelines with rate limits", surfaces=("automation", "owner_hub")),
    _c("automation.schedulers", "automation", "workflow", "Scheduled and conditional jobs", "Durable scheduler, idempotency, retries and notifications", status="existing_foundation", surfaces=("automation", "aura")),
    _c("automation.connectors", "automation", "integration", "App/API connector framework", "OAuth/API-key connector registry with least privilege", status="existing_foundation", surfaces=("automation", "aura")),
    _c("automation.human_approval", "automation", "safety", "Human-in-the-loop approval gates", "Risk-classed approval tokens + immutable audit", status="existing_foundation", surfaces=("automation", "owner_hub", "aura_sec")),

    # Social / creator-network / agency operations
    _c("social.calendar", "social", "publishing", "Cross-platform content calendar", "Channel-aware drafts, approvals and scheduling", status="existing_foundation", surfaces=("social_manager", "creator_hub")),
    _c("social.repurpose", "social", "content", "Cross-platform repurposing", "Aspect, duration, caption, copy and thumbnail variants", status="existing_foundation", surfaces=("social_manager",)),
    _c("social.inbox", "social", "community", "Unified social inbox and triage", "Connector-backed messages/comments with assignment and SLA", surfaces=("social_manager", "agent_hub")),
    _c("social.analytics", "social", "analytics", "Performance analytics and recommendations", "Normalized metrics + benchmarks + Aura insights", status="existing_foundation", surfaces=("social_manager", "creator_hub", "agent_hub")),
    _c("social.listening", "social", "analytics", "Brand/social listening", "Approved APIs/search sources + sentiment/topic clustering", surfaces=("social_manager", "owner_hub")),
    _c("social.crm", "social", "agency", "Creator/brand CRM", "Relationship, notes, campaigns, tasks and permissioned outreach records", status="existing_foundation", surfaces=("agent_hub", "owner_hub")),
    _c("social.creator_vetting", "social", "agency", "Creator eligibility and brand-safety workflow", "Evidence-based checks, consent, manual review and audit", status="existing_foundation", surfaces=("agent_hub", "owner_hub")),
    _c("social.recruiting", "social", "agency", "Creator recruiting pipeline", "Human-controlled lead queues, templates, attribution and compliance gates", status="existing_foundation", surfaces=("agent_hub",)),
    _c("social.training", "social", "agency", "Creator/agent training and academy", "Courses, quizzes, progress, certifications and coaching notes", status="existing_foundation", surfaces=("creator_hub", "agent_hub")),
    _c("social.commissions", "social", "agency", "Commission and incentive tracking", "Policy versioning, attribution, approval and payout ledger", status="existing_foundation", surfaces=("creator_hub", "agent_hub", "owner_hub")),

    # Asset management / commerce / collaboration
    _c("assets.mam", "assets", "management", "AI media asset management", "Tenant library, metadata, semantic search, versions, rights and provenance", status="existing_foundation", surfaces=("projects", "music_studio", "video_studio", "image_studio", "game_forge")),
    _c("assets.review", "assets", "collaboration", "Timestamped review, annotations and approvals", "Version-aware review threads and status gates", surfaces=("projects", "video_studio", "music_studio")),
    _c("assets.marketplace", "assets", "commerce", "Creator asset marketplace", "Listings, licences, previews, settlement and moderation", status="existing_foundation", surfaces=("marketplace",)),
    _c("assets.brand_kits", "assets", "management", "Brand kit and reusable project templates", "Logos, fonts, colours, intros, captions, lower-thirds and export profiles", surfaces=("projects", "image_studio", "video_studio", "live")),

    # Security / governance / release
    _c("security.account", "security", "identity", "Account, MFA/passkey and session security", "Fail-closed auth, passkeys, session controls and audit", status="existing_foundation", surfaces=("core", "aura_sec")),
    _c("security.device", "security", "endpoint", "Aura Sec device control plane", "Signed bounded commands, device identity, heartbeats and verified receipts", status="existing_foundation", surfaces=("aura_sec",)),
    _c("security.native_endpoint", "security", "endpoint", "Commercial native endpoint protection", "Separate signed native clients, updater, telemetry and protection engines", status="external_release_gate", surfaces=("aura_sec",), dependencies=("private_native_security_product",)),
    _c("security.provenance", "security", "governance", "AI/media provenance and consent records", "Source/model/version/consent/licence metadata carried with assets", status="existing_foundation", surfaces=("all",)),
    _c("security.release_evidence", "security", "release", "Exact-SHA production release evidence", "External evidence registry for TLS, secrets, monitoring, backup, rollback, capacity, privacy, incident, provider/payment and production AI/data", status="existing_foundation", surfaces=("owner_hub",)),
)


def capability_index(*, domain: str | None = None, status: str | None = None) -> list[dict]:
    rows = CAPABILITIES
    if domain:
        rows = tuple(row for row in rows if row.domain == domain)
    if status:
        rows = tuple(row for row in rows if row.status == status)
    return [row.public() for row in rows]


def capability_summary() -> dict:
    domains: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for capability in CAPABILITIES:
        domains[capability.domain] = domains.get(capability.domain, 0) + 1
        statuses[capability.status] = statuses.get(capability.status, 0) + 1
    return {
        "total": len(CAPABILITIES),
        "domains": dict(sorted(domains.items())),
        "statuses": dict(sorted(statuses.items())),
        "policy": {
            "original_implementation_only": True,
            "copy_proprietary_source": False,
            "copy_protected_assets": False,
            "copy_closed_model_weights": False,
            "competitor_names_are_research_only": True,
        },
    }


def validate_registry() -> None:
    ids = [capability.id for capability in CAPABILITIES]
    if len(ids) != len(set(ids)):
        raise ValueError("Universal capability IDs must be unique")
    allowed_statuses = {"existing_foundation", "planned_original", "external_release_gate", "research_only"}
    invalid = sorted({row.status for row in CAPABILITIES} - allowed_statuses)
    if invalid:
        raise ValueError(f"Unsupported capability status: {invalid}")
    for capability in CAPABILITIES:
        if "." not in capability.id:
            raise ValueError(f"Capability ID must be namespaced: {capability.id}")
        if not capability.implementation.strip():
            raise ValueError(f"Capability requires implementation contract: {capability.id}")


validate_registry()
