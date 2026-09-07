from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Literal

from .effects import compile_ffmpeg_chain
from .session import Effect

CatalogueStatus = Literal[
    "RESEARCHED",
    "SPECIFIED",
    "CONTRACT_READY",
    "RUNTIME_REQUIRED",
    "BACKEND_FUNCTIONAL",
    "UI_FUNCTIONAL",
    "WORKFLOW_FUNCTIONAL",
    "INTEGRATED",
    "TESTED",
    "RELEASE_CANDIDATE",
    "PRODUCTION_VERIFIED",
]
EntitlementBand = Literal["core", "silver", "gold"]
RuntimeKind = Literal["ffmpeg_audio"]
SourceKind = Literal[
    "esp_original_runtime_mapping",
    "member_original",
    "third_party",
    "generated",
    "unknown",
]
RightsMetadataStatus = Literal["not_asserted", "record_required", "record_linked"]


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    id: str
    label: str
    kind: Literal["number", "boolean", "string"]
    default: float | bool | str
    minimum: float | None = None
    maximum: float | None = None
    unit: str | None = None

    def public(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CreativeCatalogueItem:
    id: str
    studio: str
    menu: str
    category: str
    subcategory: str
    label: str
    description: str
    input_types: tuple[str, ...]
    output_types: tuple[str, ...]
    parameters: tuple[ParameterSpec, ...]
    tags: tuple[str, ...]
    search_aliases: tuple[str, ...]
    runtime: RuntimeKind
    runtime_effect_type: str
    preview_type: str
    status: CatalogueStatus
    version: int
    provenance: str
    accessibility: str
    safety: str
    performance_hint: str
    entitlement: EntitlementBand
    ccc_price: int
    localization_key: str
    metadata_schema_version: int = 1
    source_kind: SourceKind = "unknown"
    source_author: str = ""
    license_id: str | None = None
    rights_status: RightsMetadataStatus = "not_asserted"
    rights_record_id: str | None = None
    rights_notice: str = (
        "Catalogue presence and entitlement do not establish copyright, licence, consent, or commercial-use rights."
    )
    runtime_requirements: tuple[str, ...] = ()
    platform_requirements: tuple[str, ...] = ()
    renderer_compatibility: tuple[str, ...] = ()
    provider_compatibility: tuple[str, ...] = ()
    model_compatibility: tuple[str, ...] = ()
    example_commands: tuple[str, ...] = ()
    deprecated: bool = False
    replacement_id: str | None = None
    deprecation_note: str | None = None

    def __post_init__(self) -> None:
        if self.metadata_schema_version < 1:
            raise ValueError("Catalogue metadata schema version must be positive")
        if self.version < 1:
            raise ValueError("Catalogue item version must be positive")
        if self.ccc_price < 0:
            raise ValueError("Catalogue item price cannot be negative")
        if self.rights_status == "record_linked" and not self.rights_record_id:
            raise ValueError("Linked catalogue rights metadata requires rights_record_id")
        if self.rights_status != "record_linked" and self.rights_record_id:
            raise ValueError("rights_record_id cannot be asserted without record_linked rights status")
        if self.replacement_id == self.id:
            raise ValueError("Deprecated catalogue item cannot replace itself")
        if self.deprecated and not self.deprecation_note:
            raise ValueError("Deprecated catalogue item requires a deprecation note")
        if not self.deprecated and (self.replacement_id or self.deprecation_note):
            raise ValueError("Active catalogue item cannot advertise deprecation migration metadata")

    def public(self) -> dict:
        row = asdict(self)
        for key in (
            "input_types",
            "output_types",
            "tags",
            "search_aliases",
            "runtime_requirements",
            "platform_requirements",
            "renderer_compatibility",
            "provider_compatibility",
            "model_compatibility",
            "example_commands",
        ):
            row[key] = list(row[key])
        row["parameters"] = [parameter.public() for parameter in self.parameters]
        return row

    def build_effect(self, parameters: dict | None = None, *, enabled: bool = True, mix: float = 1.0) -> Effect:
        supplied = dict(parameters or {})
        normalized: dict[str, float | bool | str] = {}
        for spec in self.parameters:
            value = supplied.pop(spec.id, spec.default)
            if spec.kind == "number":
                value = float(value)
                if spec.minimum is not None:
                    value = max(spec.minimum, value)
                if spec.maximum is not None:
                    value = min(spec.maximum, value)
            elif spec.kind == "boolean":
                value = bool(value)
            else:
                value = str(value)
            normalized[spec.id] = value
        if supplied:
            unknown = ", ".join(sorted(supplied))
            raise ValueError(f"Unsupported parameters for {self.id}: {unknown}")
        return Effect(type=self.runtime_effect_type, enabled=enabled, mix=mix, parameters=normalized)

    def preview_filter_chain(self, parameters: dict | None = None) -> str:
        return compile_ffmpeg_chain([self.build_effect(parameters)])


def _n(
    id: str,
    label: str,
    default: float,
    minimum: float,
    maximum: float,
    unit: str | None = None,
) -> ParameterSpec:
    return ParameterSpec(id=id, label=label, kind="number", default=default, minimum=minimum, maximum=maximum, unit=unit)


def _audio_item(
    id: str,
    label: str,
    description: str,
    effect_type: str,
    parameters: Iterable[ParameterSpec],
    *,
    category: str,
    subcategory: str,
    tags: Iterable[str],
    aliases: Iterable[str] = (),
    entitlement: EntitlementBand = "core",
) -> CreativeCatalogueItem:
    price = {"core": 0, "silver": 200, "gold": 500}[entitlement]
    return CreativeCatalogueItem(
        id=id,
        studio="music",
        menu="effects",
        category=category,
        subcategory=subcategory,
        label=label,
        description=description,
        input_types=("audio",),
        output_types=("audio",),
        parameters=tuple(parameters),
        tags=tuple(tags),
        search_aliases=tuple(aliases),
        runtime="ffmpeg_audio",
        runtime_effect_type=effect_type,
        preview_type="audio_ab",
        status="BACKEND_FUNCTIONAL",
        version=1,
        provenance="ESP_ORIGINAL_RUNTIME_MAPPING",
        accessibility="Parameters have text labels and bounded numeric values.",
        safety="No identity, biometric, or user-secret processing.",
        performance_hint="Compiled into the existing FFmpeg real-audio render chain.",
        entitlement=entitlement,
        ccc_price=price,
        localization_key=f"catalogue.{id}",
        metadata_schema_version=1,
        source_kind="esp_original_runtime_mapping",
        source_author="Elevate Souls Productions",
        license_id=None,
        rights_status="not_asserted",
        rights_record_id=None,
        rights_notice=(
            "This catalogue record describes an ESP runtime mapping only. Entitlement or catalogue presence "
            "does not establish copyright, licence, consent, or commercial-use rights for member inputs/outputs."
        ),
        runtime_requirements=("ffmpeg_audio_renderer",),
        platform_requirements=("server",),
        renderer_compatibility=("ffmpeg_audio",),
        provider_compatibility=(),
        model_compatibility=(),
        example_commands=(f"Preview {label}", f"Apply {label}"),
        deprecated=False,
        replacement_id=None,
        deprecation_note=None,
    )


CATALOGUE_ITEMS: tuple[CreativeCatalogueItem, ...] = (
    _audio_item(
        "music.fx.gain",
        "Gain",
        "Adjust signal level in decibels.",
        "gain",
        (_n("db", "Gain", 0.0, -60.0, 18.0, "dB"),),
        category="Dynamics & Level",
        subcategory="Level",
        tags=("gain", "volume", "level"),
    ),
    _audio_item(
        "music.fx.highpass",
        "High-Pass Filter",
        "Attenuate low-frequency content below the selected cutoff.",
        "highpass",
        (_n("hz", "Cutoff", 70.0, 20.0, 1000.0, "Hz"),),
        category="EQ & Filters",
        subcategory="Filters",
        tags=("eq", "filter", "low cut"),
        aliases=("high pass", "low cut"),
    ),
    _audio_item(
        "music.fx.lowpass",
        "Low-Pass Filter",
        "Attenuate high-frequency content above the selected cutoff.",
        "lowpass",
        (_n("hz", "Cutoff", 18000.0, 1000.0, 22000.0, "Hz"),),
        category="EQ & Filters",
        subcategory="Filters",
        tags=("eq", "filter", "high cut"),
        aliases=("low pass", "high cut"),
    ),
    _audio_item(
        "music.fx.compressor",
        "Compressor",
        "Reduce dynamic range using threshold, ratio, attack and release controls.",
        "compressor",
        (
            _n("threshold_db", "Threshold", -18.0, -60.0, 0.0, "dB"),
            _n("ratio", "Ratio", 2.5, 1.0, 20.0, ":1"),
            _n("attack_ms", "Attack", 15.0, 0.1, 200.0, "ms"),
            _n("release_ms", "Release", 160.0, 5.0, 2000.0, "ms"),
        ),
        category="Dynamics & Level",
        subcategory="Compression",
        tags=("compressor", "dynamics", "level"),
    ),
    _audio_item(
        "music.fx.limiter",
        "Limiter",
        "Control peaks with a bounded output ceiling.",
        "limiter",
        (
            _n("ceiling_db", "Ceiling", -1.0, -12.0, 0.0, "dB"),
            _n("attack_ms", "Attack", 5.0, 0.1, 100.0, "ms"),
            _n("release_ms", "Release", 50.0, 5.0, 1000.0, "ms"),
        ),
        category="Dynamics & Level",
        subcategory="Limiting",
        tags=("limiter", "peak", "mastering"),
        entitlement="silver",
    ),
    _audio_item(
        "music.fx.reverb",
        "Reverb",
        "Add a bounded ambience/echo-derived reverb treatment using the real audio renderer.",
        "reverb",
        (
            _n("predelay_ms", "Pre-delay", 30.0, 1.0, 500.0, "ms"),
            _n("mix", "Decay Mix", 0.18, 0.01, 0.8),
        ),
        category="Space & Delay",
        subcategory="Reverb",
        tags=("reverb", "space", "ambience"),
    ),
    _audio_item(
        "music.fx.delay",
        "Delay",
        "Create a timed echo with bounded feedback.",
        "delay",
        (
            _n("delay_ms", "Delay", 240.0, 1.0, 2000.0, "ms"),
            _n("feedback", "Feedback", 0.25, 0.0, 0.9),
        ),
        category="Space & Delay",
        subcategory="Delay",
        tags=("delay", "echo", "time"),
    ),
    _audio_item(
        "music.fx.saturation",
        "Saturation",
        "Apply bounded soft-clipping drive for harmonic colour.",
        "saturation",
        (_n("drive", "Drive", 1.35, 1.0, 12.0),),
        category="Tone & Character",
        subcategory="Saturation",
        tags=("saturation", "warmth", "drive"),
        entitlement="silver",
    ),
    _audio_item(
        "music.fx.chorus",
        "Chorus",
        "Apply modulation-based thickening with delay, decay, rate and depth controls.",
        "chorus",
        (
            _n("delay_ms", "Delay", 18.0, 5.0, 40.0, "ms"),
            _n("decay", "Decay", 0.35, 0.05, 0.9),
            _n("rate_hz", "Rate", 0.8, 0.1, 5.0, "Hz"),
            _n("depth", "Depth", 2.0, 0.1, 10.0),
        ),
        category="Modulation",
        subcategory="Chorus",
        tags=("chorus", "modulation", "width"),
        entitlement="silver",
    ),
    _audio_item(
        "music.fx.stereo_width",
        "Stereo Width",
        "Adjust side-channel level while retaining the mid channel.",
        "stereo_width",
        (_n("width", "Width", 1.0, 0.0, 2.0),),
        category="Stereo & Spatial",
        subcategory="Width",
        tags=("stereo", "width", "mid side"),
        aliases=("m/s", "stereo image"),
        entitlement="gold",
    ),
)


_BY_ID = {item.id: item for item in CATALOGUE_ITEMS}
if len(_BY_ID) != len(CATALOGUE_ITEMS):  # pragma: no cover - import-time integrity guard
    raise RuntimeError("Duplicate creative catalogue IDs")


def get_catalogue_item(item_id: str) -> CreativeCatalogueItem:
    try:
        return _BY_ID[item_id]
    except KeyError as exc:
        raise KeyError(f"Unknown creative catalogue item: {item_id}") from exc


def search_catalogue(
    query: str = "",
    *,
    studio: str | None = None,
    entitlement: EntitlementBand | None = None,
) -> list[CreativeCatalogueItem]:
    terms = [part for part in query.casefold().split() if part]
    matches: list[CreativeCatalogueItem] = []
    for item in CATALOGUE_ITEMS:
        if studio and item.studio != studio:
            continue
        if entitlement and item.entitlement != entitlement:
            continue
        haystack = " ".join(
            (
                item.id,
                item.label,
                item.description,
                item.category,
                item.subcategory,
                item.source_author,
                *item.tags,
                *item.search_aliases,
            )
        ).casefold()
        if all(term in haystack for term in terms):
            matches.append(item)
    return matches


def public_catalogue(query: str = "", *, studio: str | None = None) -> list[dict]:
    return [item.public() for item in search_catalogue(query, studio=studio)]


__all__ = [
    "CATALOGUE_ITEMS",
    "CatalogueStatus",
    "CreativeCatalogueItem",
    "EntitlementBand",
    "ParameterSpec",
    "RightsMetadataStatus",
    "RuntimeKind",
    "SourceKind",
    "get_catalogue_item",
    "public_catalogue",
    "search_catalogue",
]
