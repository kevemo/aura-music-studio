from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal

from fastapi import APIRouter, HTTPException, Request

from .fx_presets import public_presets as public_audio_fx_presets
from .instrument_catalog import public_catalog as public_instrument_catalog
from .mastering import public_mastering_presets

router = APIRouter(prefix="/command-center/api/universal-library", tags=["Universal Creative Library"])

ImplementationStatus = Literal["existing", "contract_ready", "renderer_required", "external_provider_required"]


@dataclass(frozen=True, slots=True)
class CatalogItem:
    id: str
    domain: str
    category: str
    label: str
    description: str
    input_types: tuple[str, ...]
    output_types: tuple[str, ...]
    parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    tier: str = "free"
    implementation_status: ImplementationStatus = "contract_ready"
    renderer_ids: tuple[str, ...] = ()
    provider_task: str = ""
    preview_kind: str = "none"
    licence: str = "ESP-original-schema"
    provenance_required: bool = True
    accessible_name: str = ""
    safety_notes: str = ""
    version: int = 1
    deprecated_by: str = ""

    def public(self) -> dict:
        row = asdict(self)
        for key in ("input_types", "output_types", "tags", "renderer_ids"):
            row[key] = list(row[key])
        return row


def _p(kind: str, default: Any, *, minimum: float | None = None, maximum: float | None = None, choices: Iterable[str] = ()) -> dict[str, Any]:
    row: dict[str, Any] = {"type": kind, "default": default}
    if minimum is not None:
        row["minimum"] = minimum
    if maximum is not None:
        row["maximum"] = maximum
    if choices:
        row["choices"] = list(choices)
    return row


def _item(
    id: str,
    domain: str,
    category: str,
    label: str,
    description: str,
    *,
    input_types: Iterable[str],
    output_types: Iterable[str],
    parameters: dict[str, dict[str, Any]] | None = None,
    tags: Iterable[str] = (),
    tier: str = "free",
    implementation_status: ImplementationStatus = "contract_ready",
    renderer_ids: Iterable[str] = (),
    provider_task: str = "",
    preview_kind: str = "none",
    safety_notes: str = "",
) -> CatalogItem:
    return CatalogItem(
        id=id,
        domain=domain,
        category=category,
        label=label,
        description=description,
        input_types=tuple(input_types),
        output_types=tuple(output_types),
        parameters=parameters or {},
        tags=tuple(tags),
        tier=tier,
        implementation_status=implementation_status,
        renderer_ids=tuple(renderer_ids),
        provider_task=provider_task,
        preview_kind=preview_kind,
        accessible_name=label,
        safety_notes=safety_notes,
    )


# Cross-studio native catalogue. Existing audio preset/instrument/mastering catalogues are
# adapted dynamically below so there is one discovery API without duplicating their data.
BUILTIN_ITEMS: tuple[CatalogItem, ...] = (
    # Video transforms and grading
    _item("video.transform.2d", "video", "transform", "2D Transform", "Position, scale, rotation, anchor and crop controls.", input_types=("video", "image"), output_types=("video",), parameters={"x": _p("number", 0), "y": _p("number", 0), "scale": _p("number", 1, minimum=0.01, maximum=20), "rotation_deg": _p("number", 0, minimum=-3600, maximum=3600), "opacity": _p("number", 1, minimum=0, maximum=1)}, renderer_ids=("ffmpeg", "webgl"), implementation_status="existing", tags=("motion", "crop", "keyframe"), preview_kind="video"),
    _item("video.grade.basic", "video", "colour", "Primary Colour Grade", "Exposure, contrast, saturation, temperature and tint.", input_types=("video", "image"), output_types=("video", "image"), parameters={"exposure": _p("number", 0, minimum=-5, maximum=5), "contrast": _p("number", 0, minimum=-1, maximum=1), "saturation": _p("number", 0, minimum=-1, maximum=3), "temperature": _p("number", 0, minimum=-1, maximum=1), "tint": _p("number", 0, minimum=-1, maximum=1)}, renderer_ids=("ffmpeg", "webgl"), implementation_status="contract_ready", tags=("colour", "grading"), preview_kind="video"),
    _item("video.grade.lut", "video", "colour", "LUT Grade", "Apply an approved user/ESP LUT with intensity control.", input_types=("video", "image", "lut"), output_types=("video", "image"), parameters={"lut_asset_id": _p("asset_ref", ""), "intensity": _p("number", 1, minimum=0, maximum=1)}, tier="base", renderer_ids=("ffmpeg",), tags=("lut", "look"), preview_kind="video"),
    _item("video.fx.blur", "video", "effect", "Blur", "Gaussian-style blur with bounded radius.", input_types=("video", "image"), output_types=("video", "image"), parameters={"radius": _p("number", 8, minimum=0, maximum=100)}, renderer_ids=("ffmpeg", "webgl"), implementation_status="existing", tags=("blur", "privacy"), preview_kind="video"),
    _item("video.fx.glow", "video", "effect", "Glow / Bloom", "Soft highlight bloom for titles, subjects and light sources.", input_types=("video", "image"), output_types=("video", "image"), parameters={"threshold": _p("number", .75, minimum=0, maximum=1), "radius": _p("number", 18, minimum=0, maximum=100), "intensity": _p("number", .5, minimum=0, maximum=3)}, renderer_ids=("webgl",), tags=("glow", "bloom", "light"), preview_kind="video"),
    _item("video.fx.film_grain", "video", "effect", "Film Grain", "Procedural grain with size and intensity controls.", input_types=("video", "image"), output_types=("video", "image"), parameters={"amount": _p("number", .15, minimum=0, maximum=1), "size": _p("number", 1, minimum=.25, maximum=8)}, renderer_ids=("ffmpeg", "webgl"), tags=("film", "texture"), preview_kind="video"),
    _item("video.fx.chromatic_aberration", "video", "effect", "Chromatic Aberration", "Subtle RGB channel offset for optical or glitch looks.", input_types=("video", "image"), output_types=("video", "image"), parameters={"offset_px": _p("number", 2, minimum=0, maximum=50)}, renderer_ids=("webgl",), tags=("lens", "glitch"), preview_kind="video"),
    _item("video.fx.vignette", "video", "effect", "Vignette", "Edge darkening/brightening with feather control.", input_types=("video", "image"), output_types=("video", "image"), parameters={"amount": _p("number", .25, minimum=-1, maximum=1), "feather": _p("number", .6, minimum=0, maximum=1)}, renderer_ids=("ffmpeg", "webgl"), tags=("cinematic", "focus"), preview_kind="video"),
    _item("video.fx.vhs", "video", "stylise", "VHS / CRT", "Original retro scanline, noise, colour drift and time-jitter treatment.", input_types=("video", "image"), output_types=("video",), parameters={"scanlines": _p("number", .5, minimum=0, maximum=1), "noise": _p("number", .25, minimum=0, maximum=1), "jitter": _p("number", .1, minimum=0, maximum=1)}, renderer_ids=("webgl",), tier="base", tags=("retro", "vhs", "crt"), preview_kind="video"),
    _item("video.fx.neon", "video", "stylise", "Neon Edge", "Edge-derived emissive neon treatment with configurable strength.", input_types=("video", "image"), output_types=("video", "image"), parameters={"edge_strength": _p("number", .7, minimum=0, maximum=2), "glow": _p("number", .8, minimum=0, maximum=3)}, renderer_ids=("webgl",), tier="base", tags=("neon", "cyber"), preview_kind="video"),
    _item("video.mask.smart_subject", "video", "mask", "Smart Subject Mask", "Tracked subject segmentation with editable refinement mask.", input_types=("video", "image"), output_types=("mask", "video"), parameters={"subject_hint": _p("string", "primary subject"), "feather": _p("number", 2, minimum=0, maximum=50)}, tier="pro", implementation_status="external_provider_required", provider_task="segmentation.track", tags=("mask", "rotoscope", "tracking"), preview_kind="video"),
    _item("video.edit.object_remove", "video", "generative_edit", "Tracked Object Removal", "Track a selected object and inpaint it temporally.", input_types=("video", "mask"), output_types=("video",), parameters={"strength": _p("number", .8, minimum=0, maximum=1)}, tier="pro", implementation_status="external_provider_required", provider_task="video.inpaint", tags=("remove", "inpaint"), preview_kind="video", safety_notes="Generated edits retain provenance metadata."),
    # Transitions
    _item("video.transition.dissolve", "video", "transition", "Dissolve", "Cross-dissolve between adjacent clips.", input_types=("video", "image"), output_types=("video",), parameters={"duration_ms": _p("integer", 500, minimum=50, maximum=10000)}, renderer_ids=("ffmpeg", "webgl"), implementation_status="existing", tags=("transition", "classic"), preview_kind="video"),
    _item("video.transition.slide", "video", "transition", "Slide", "Directional slide transition with easing.", input_types=("video", "image"), output_types=("video",), parameters={"direction": _p("enum", "left", choices=("left", "right", "up", "down")), "duration_ms": _p("integer", 500, minimum=50, maximum=5000), "easing": _p("enum", "ease_in_out", choices=("linear", "ease_in", "ease_out", "ease_in_out"))}, renderer_ids=("webgl",), tags=("transition", "motion"), preview_kind="video"),
    _item("video.transition.zoom", "video", "transition", "Zoom Through", "Zoom-and-blur transition centred on a configurable focus point.", input_types=("video", "image"), output_types=("video",), parameters={"duration_ms": _p("integer", 450, minimum=50, maximum=5000), "blur": _p("number", .35, minimum=0, maximum=1)}, renderer_ids=("webgl",), tier="base", tags=("transition", "zoom"), preview_kind="video"),
    _item("video.transition.whip", "video", "transition", "Whip Pan", "Fast directional pan with motion blur.", input_types=("video", "image"), output_types=("video",), parameters={"direction": _p("enum", "left", choices=("left", "right", "up", "down")), "duration_ms": _p("integer", 300, minimum=80, maximum=2000)}, renderer_ids=("webgl",), tier="base", tags=("transition", "whip", "motion_blur"), preview_kind="video"),
    _item("video.transition.glitch", "video", "transition", "Glitch Cut", "Short deterministic digital displacement transition.", input_types=("video", "image"), output_types=("video",), parameters={"duration_ms": _p("integer", 260, minimum=80, maximum=1500), "intensity": _p("number", .6, minimum=0, maximum=1)}, renderer_ids=("webgl",), tier="pro", tags=("transition", "glitch"), preview_kind="video"),
    # Captions / titles / particles
    _item("video.caption.word_pop", "video", "caption", "Word Pop Captions", "Word-timed captions with scale emphasis and safe-area layout.", input_types=("transcript", "video"), output_types=("video", "caption_track"), parameters={"max_words": _p("integer", 5, minimum=1, maximum=12), "emphasis_scale": _p("number", 1.15, minimum=1, maximum=2)}, renderer_ids=("webgl",), tags=("captions", "shorts"), preview_kind="video"),
    _item("video.title.lower_third", "video", "title", "Lower Third", "Editable name/title lower-third with brand-safe margins.", input_types=("text", "video"), output_types=("video",), parameters={"headline": _p("string", "Name"), "subline": _p("string", "Role"), "duration_ms": _p("integer", 5000, minimum=500, maximum=60000)}, renderer_ids=("webgl",), tags=("title", "branding"), preview_kind="video"),
    _item("live.particle.hearts", "live", "particle", "Heart Fountain", "Audio/event-reactive heart particles.", input_types=("live_event",), output_types=("overlay",), parameters={"count": _p("integer", 24, minimum=1, maximum=500), "lifetime_ms": _p("integer", 1800, minimum=100, maximum=10000)}, renderer_ids=("browser_overlay",), implementation_status="existing", tags=("heart", "likes", "particles"), preview_kind="overlay"),
    _item("live.particle.confetti", "live", "particle", "Confetti Burst", "Bounded celebratory confetti particle burst.", input_types=("live_event",), output_types=("overlay",), parameters={"count": _p("integer", 80, minimum=1, maximum=1000), "gravity": _p("number", .5, minimum=-2, maximum=4)}, renderer_ids=("browser_overlay",), tags=("celebration", "particles"), preview_kind="overlay"),
    _item("live.visualizer.spectrum", "live", "visualizer", "Spectrum Visualizer", "WebAudio frequency bars or radial spectrum.", input_types=("audio_stream",), output_types=("overlay",), parameters={"mode": _p("enum", "bars", choices=("bars", "radial", "wave")), "smoothing": _p("number", .7, minimum=0, maximum=.99)}, renderer_ids=("browser_overlay",), tags=("music", "audio_reactive"), preview_kind="overlay"),
    # Image and design
    _item("image.filter.cinematic", "image", "filter", "Cinematic Contrast", "Original editable tone curve and selective saturation preset.", input_types=("image",), output_types=("image",), parameters={"strength": _p("number", .7, minimum=0, maximum=1)}, renderer_ids=("webgl",), tags=("cinematic", "grade"), preview_kind="image"),
    _item("image.filter.duotone", "image", "filter", "Duotone", "Two-tone luminance mapping with editable colours.", input_types=("image",), output_types=("image",), parameters={"shadow": _p("color", "#111111"), "highlight": _p("color", "#f2c86f")}, renderer_ids=("webgl",), tags=("poster", "duotone"), preview_kind="image"),
    _item("image.layout.poster", "image", "template", "Poster Layout System", "Editable title, subtitle, subject, background and CTA zones.", input_types=("image", "text"), output_types=("image", "design_document"), parameters={"aspect": _p("enum", "4:5", choices=("1:1", "4:5", "9:16", "16:9", "A4")), "safe_margin_percent": _p("number", 5, minimum=0, maximum=20)}, renderer_ids=("design_canvas",), implementation_status="existing", tags=("poster", "social", "typography"), preview_kind="image"),
    _item("image.mockup.perspective", "image", "mockup", "Perspective Mockup", "Place artwork into a four-corner perspective surface.", input_types=("image",), output_types=("image",), parameters={"surface_asset_id": _p("asset_ref", ""), "opacity": _p("number", 1, minimum=0, maximum=1)}, renderer_ids=("design_canvas",), tier="base", tags=("mockup", "perspective"), preview_kind="image"),
    _item("image.texture.tileable", "image", "material", "Tileable Texture Generator", "Generate seamless texture variants with provenance.", input_types=("text", "image"), output_types=("image", "texture"), parameters={"prompt": _p("string", ""), "resolution": _p("enum", "1024", choices=("512", "1024", "2048"))}, tier="pro", implementation_status="external_provider_required", provider_task="image.texture.generate", tags=("texture", "game", "material"), preview_kind="image"),
    # 3D / Game
    _item("game.template.platformer2d", "game", "template", "2D Platformer Foundation", "Original scene/entity template for movement, camera, collision, checkpoints and UI.", input_types=("game_project",), output_types=("game_scene", "code"), parameters={"gravity": _p("number", 980, minimum=0, maximum=5000), "move_speed": _p("number", 260, minimum=1, maximum=5000), "jump_speed": _p("number", 520, minimum=1, maximum=5000)}, renderer_ids=("esp_game_runtime",), implementation_status="existing", tags=("platformer", "starter"), preview_kind="game"),
    _item("game.template.topdown", "game", "template", "Top-Down Adventure Foundation", "Scene/entity template for 8-way movement, camera, interaction and triggers.", input_types=("game_project",), output_types=("game_scene", "code"), parameters={"move_speed": _p("number", 220, minimum=1, maximum=5000)}, renderer_ids=("esp_game_runtime",), implementation_status="existing", tags=("topdown", "starter"), preview_kind="game"),
    _item("game.procgen.dungeon", "game", "procedural", "Seeded Dungeon Generator", "Room/corridor graph generator with deterministic seed and constraints.", input_types=("game_project",), output_types=("game_scene", "level_data"), parameters={"seed": _p("integer", 0), "rooms": _p("integer", 12, minimum=2, maximum=500), "branching": _p("number", .35, minimum=0, maximum=1)}, renderer_ids=("esp_game_runtime",), tier="base", tags=("procedural", "dungeon"), preview_kind="game"),
    _item("game.material.pbr", "game", "material", "PBR Material Pack", "Base-colour, normal, roughness, metallic and AO material record.", input_types=("image", "texture"), output_types=("material",), parameters={"normal_strength": _p("number", 1, minimum=0, maximum=4), "roughness": _p("number", .5, minimum=0, maximum=1), "metallic": _p("number", 0, minimum=0, maximum=1)}, renderer_ids=("webgl", "gltf"), tags=("pbr", "3d"), preview_kind="3d"),
    _item("game.animation.state_machine", "game", "animation", "Animation State Machine", "Typed animation states and transitions driven by events/conditions.", input_types=("rig", "animation_clip"), output_types=("animation_graph",), parameters={"default_state": _p("string", "idle")}, renderer_ids=("esp_game_runtime",), tags=("animation", "state_machine"), preview_kind="3d"),
    _item("game.dialogue.branch", "game", "narrative", "Branching Dialogue Node", "Choice-based dialogue node with conditions, variables and consequences.", input_types=("text", "game_state"), output_types=("dialogue_graph",), parameters={"speaker": _p("string", "NPC"), "text": _p("string", ""), "max_choices": _p("integer", 4, minimum=1, maximum=12)}, renderer_ids=("esp_game_runtime",), tags=("dialogue", "quest"), preview_kind="game"),
    # Workflow / automation primitives
    _item("workflow.trigger.webhook", "automation", "trigger", "Webhook Trigger", "Authenticated event ingress trigger with replay/idempotency protection.", input_types=("http_event",), output_types=("workflow_event",), parameters={"schema_id": _p("string", ""), "idempotency_key_path": _p("string", "")}, renderer_ids=("workflow_engine",), implementation_status="existing", tags=("webhook", "trigger")),
    _item("workflow.trigger.schedule", "automation", "trigger", "Schedule Trigger", "Timezone-aware recurring or one-shot trigger.", input_types=("schedule",), output_types=("workflow_event",), parameters={"ical": _p("string", "")}, renderer_ids=("workflow_engine",), implementation_status="existing", tags=("schedule", "trigger")),
    _item("workflow.logic.condition", "automation", "logic", "Condition", "Typed comparison branch with explicit true/false outputs.", input_types=("workflow_value",), output_types=("workflow_branch",), parameters={"operator": _p("enum", "equals", choices=("equals", "not_equals", "contains", "gt", "gte", "lt", "lte", "exists")), "value": _p("json", None)}, renderer_ids=("workflow_engine",), implementation_status="existing", tags=("branch", "logic")),
    _item("workflow.logic.approval", "automation", "safety", "Human Approval Gate", "Risk-classed human approval before a high-impact action continues.", input_types=("workflow_action",), output_types=("approval_evidence",), parameters={"risk": _p("enum", "confirmation", choices=("confirmation", "strong_reauth")), "expires_minutes": _p("integer", 10, minimum=1, maximum=60)}, renderer_ids=("workflow_engine",), implementation_status="existing", tags=("approval", "human_in_loop")),
    # Voice / localisation
    _item("voice.cleanup.dialogue", "voice", "restoration", "Dialogue Cleanup", "Denoise, de-reverb and speech-presence chain with before/after preview.", input_types=("audio",), output_types=("audio",), parameters={"denoise": _p("number", .5, minimum=0, maximum=1), "dereverb": _p("number", .4, minimum=0, maximum=1)}, tier="base", implementation_status="renderer_required", renderer_ids=("audio_cleanup",), tags=("speech", "restore"), preview_kind="audio"),
    _item("voice.dubbing.multilingual", "voice", "localisation", "Multilingual Dubbing", "Segment, translate, synthesize approved speakers and time-align output.", input_types=("audio", "video", "transcript"), output_types=("audio", "subtitle_track"), parameters={"target_locale": _p("string", "en-GB"), "speaker_policy": _p("enum", "approved_only", choices=("approved_only", "stock_voice"))}, tier="pro", implementation_status="external_provider_required", provider_task="voice.dubbing", tags=("translate", "dub"), preview_kind="audio", safety_notes="Voice identity use requires explicit consent/rights."),
)


def _audio_adapter_items() -> list[dict]:
    rows: list[dict] = []
    try:
        for preset in public_audio_fx_presets():
            preset_id = str(preset.get("id") or "unknown")
            rows.append({
                "id": f"audio.fx_preset.{preset_id}",
                "domain": "music",
                "category": "audio_fx_preset",
                "label": str(preset.get("name") or preset_id),
                "description": "Existing Command Center audio FX preset.",
                "input_types": ["audio"],
                "output_types": ["audio"],
                "parameters": {},
                "tags": ["audio", "fx", "existing"],
                "tier": str(preset.get("tier") or "free"),
                "implementation_status": "existing",
                "renderer_ids": ["audio_effects"],
                "provider_task": "",
                "preview_kind": "audio",
                "licence": "ESP-existing-catalogue",
                "provenance_required": True,
                "accessible_name": str(preset.get("name") or preset_id),
                "safety_notes": "",
                "version": 1,
                "deprecated_by": "",
                "source_catalogue": "fx_presets",
            })
    except Exception:
        pass
    try:
        for preset in public_mastering_presets():
            preset_id = str(preset.get("id") or "unknown")
            rows.append({
                "id": f"audio.mastering.{preset_id}",
                "domain": "music",
                "category": "mastering_preset",
                "label": str(preset.get("name") or preset_id),
                "description": "Existing Command Center mastering character.",
                "input_types": ["audio"],
                "output_types": ["audio"],
                "parameters": {},
                "tags": ["audio", "mastering", "existing"],
                "tier": "free",
                "implementation_status": "existing",
                "renderer_ids": ["mastering"],
                "provider_task": "",
                "preview_kind": "audio",
                "licence": "ESP-existing-catalogue",
                "provenance_required": True,
                "accessible_name": str(preset.get("name") or preset_id),
                "safety_notes": "",
                "version": 1,
                "deprecated_by": "",
                "source_catalogue": "mastering",
            })
    except Exception:
        pass
    try:
        families = public_instrument_catalog()
        for family, items in families.items():
            for instrument in items:
                type_id = str(instrument.get("id") or "unknown")
                rows.append({
                    "id": f"audio.instrument.{family}.{type_id}",
                    "domain": "music",
                    "category": "instrument",
                    "label": str(instrument.get("label") or type_id),
                    "description": f"Existing Command Center {family} instrument type.",
                    "input_types": ["midi", "score", "generation_instruction"],
                    "output_types": ["audio"],
                    "parameters": {},
                    "tags": ["instrument", str(family), "existing"],
                    "tier": "pro" if instrument.get("pro_only") else "free",
                    "implementation_status": "existing",
                    "renderer_ids": ["instrument_renderer"],
                    "provider_task": "",
                    "preview_kind": "audio",
                    "licence": "ESP-existing-catalogue",
                    "provenance_required": True,
                    "accessible_name": str(instrument.get("label") or type_id),
                    "safety_notes": "",
                    "version": 1,
                    "deprecated_by": "",
                    "source_catalogue": "instrument_catalog",
                })
    except Exception:
        pass
    return rows


def validate_builtin_catalog() -> None:
    ids = [item.id for item in BUILTIN_ITEMS]
    if len(ids) != len(set(ids)):
        raise ValueError("Universal creative library IDs must be unique")
    for item in BUILTIN_ITEMS:
        if item.id.count(".") < 2:
            raise ValueError(f"Catalogue ID must be namespaced: {item.id}")
        if not item.input_types or not item.output_types:
            raise ValueError(f"Catalogue item must define media compatibility: {item.id}")
        for name, spec in item.parameters.items():
            if "type" not in spec or "default" not in spec:
                raise ValueError(f"Parameter {item.id}.{name} must declare type/default")
            if spec.get("minimum") is not None and spec.get("maximum") is not None and spec["minimum"] > spec["maximum"]:
                raise ValueError(f"Invalid parameter bounds: {item.id}.{name}")


validate_builtin_catalog()


def catalogue(*, domain: str | None = None, category: str | None = None, status: str | None = None, include_existing_audio: bool = True) -> list[dict]:
    rows = [item.public() for item in BUILTIN_ITEMS]
    if include_existing_audio:
        rows.extend(_audio_adapter_items())
    if domain:
        rows = [row for row in rows if row["domain"] == domain]
    if category:
        rows = [row for row in rows if row["category"] == category]
    if status:
        rows = [row for row in rows if row["implementation_status"] == status]
    return sorted(rows, key=lambda row: row["id"])


def catalogue_summary() -> dict:
    rows = catalogue()
    domains: dict[str, int] = {}
    statuses: dict[str, int] = {}
    categories: dict[str, int] = {}
    for row in rows:
        domains[row["domain"]] = domains.get(row["domain"], 0) + 1
        statuses[row["implementation_status"]] = statuses.get(row["implementation_status"], 0) + 1
        categories[row["category"]] = categories.get(row["category"], 0) + 1
    return {"total": len(rows), "domains": dict(sorted(domains.items())), "statuses": dict(sorted(statuses.items())), "categories": dict(sorted(categories.items()))}


def _require_member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    return member


@router.get("")
def universal_library(request: Request, domain: str | None = None, category: str | None = None, status: str | None = None):
    member = _require_member(request)
    rows = catalogue(domain=domain, category=category, status=status)
    # Preserve catalogue discoverability while making entitlement a UI/runtime concern.
    for row in rows:
        tier = row.get("tier") or "free"
        row["locked"] = bool(tier == "pro" and member.plan.id != "pro")
    return {"items": rows, "summary": catalogue_summary(), "plan": member.plan.id}


@router.get("/{item_id:path}")
def universal_library_item(item_id: str, request: Request):
    _require_member(request)
    row = next((row for row in catalogue() if row["id"] == item_id), None)
    if row is None:
        raise HTTPException(404, "Universal creative library item not found")
    return {"item": row}


__all__ = ["router", "BUILTIN_ITEMS", "CatalogItem", "catalogue", "catalogue_summary"]
