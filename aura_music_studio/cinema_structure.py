from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .cinema_production import CinemaProductionStore
from .plans import BASIC_TIMELINE
from .tenant_storage import project_path

router = APIRouter(prefix="/creative", tags=["Cinema Structure"])

STRUCTURE_FILENAME = "cinema_structure.json"
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")

CutState = Literal["rough_cut", "fine_cut", "picture_locked", "mastered"]
TakeStatus = Literal["captured", "circle", "selected", "rejected", "approved"]
DeliveryPurpose = Literal["master", "trailer", "teaser", "promo", "social_cutdown", "alternate"]
DeliveryState = Literal["planned", "editing", "ready", "archived"]
AspectRatio = Literal["16:9", "9:16", "1:1", "4:5", "4:3", "21:9", "custom"]
GenerationType = Literal["captured", "imported", "generated", "edited", "composite"]
RightsState = Literal["unverified", "owned", "licensed", "generated_with_rights", "restricted"]
ConsentState = Literal["not_required", "pending", "granted", "revoked"]
ResourceType = Literal[
    "video",
    "image",
    "music",
    "font",
    "template",
    "voice_profile",
    "visual_likeness",
    "character_model",
    "stock_asset",
    "other",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _clean(value: str, *, field: str, maximum: int) -> str:
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        raise ValueError(f"{field} is required")
    if len(clean) > maximum:
        raise ValueError(f"{field} is too long")
    return clean


def _safe_ref(value: str | None, *, field: str, required: bool = False) -> str | None:
    clean = str(value or "").strip()
    if not clean:
        if required:
            raise ValueError(f"{field} is required")
        return None
    if not _SAFE_REF.fullmatch(clean):
        raise ValueError(f"{field} must be a canonical identifier, not a path or URL")
    return clean


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CinemaAct(BaseModel):
    id: str = Field(default_factory=lambda: _id("act"))
    film_id: str = Field(min_length=1, max_length=200)
    act_number: int = Field(ge=1, le=100)
    title: str = Field(min_length=1, max_length=240)
    synopsis: str = Field(default="", max_length=12000)
    sequence_ids: list[str] = Field(default_factory=list, max_length=1000)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class CinemaNarrativeSequence(BaseModel):
    id: str = Field(default_factory=lambda: _id("nseq"))
    film_id: str = Field(min_length=1, max_length=200)
    act_id: str = Field(min_length=1, max_length=200)
    sequence_number: int = Field(ge=1, le=10000)
    title: str = Field(min_length=1, max_length=240)
    synopsis: str = Field(default="", max_length=12000)
    scene_ids: list[str] = Field(default_factory=list, max_length=5000)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class CinemaCharacter(BaseModel):
    id: str = Field(default_factory=lambda: _id("character"))
    film_id: str = Field(min_length=1, max_length=200)
    character_key: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=12000)
    visual_identity_asset_id: str | None = Field(default=None, max_length=200)
    default_language: str = Field(default="en", min_length=2, max_length=32)
    performance_baseline: str = Field(default="", max_length=4000)
    wardrobe_baseline: str = Field(default="", max_length=4000)
    continuity_notes: str = Field(default="", max_length=8000)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class CinemaTake(BaseModel):
    id: str = Field(default_factory=lambda: _id("take"))
    film_id: str = Field(min_length=1, max_length=200)
    scene_id: str = Field(min_length=1, max_length=200)
    shot_id: str = Field(min_length=1, max_length=200)
    take_number: int = Field(ge=1, le=10000)
    slate: str = Field(default="", max_length=240)
    notes: str = Field(default="", max_length=8000)
    rating: int | None = Field(default=None, ge=0, le=5)
    status: TakeStatus = "captured"
    editor_sequence_id: str | None = Field(default=None, max_length=200)
    source_asset_id: str | None = Field(default=None, max_length=200)
    generation_type: GenerationType = "captured"
    provider_runtime_class: str = Field(default="", max_length=160)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class CinemaCutRecord(BaseModel):
    id: str = Field(default_factory=lambda: _id("cut"))
    film_id: str = Field(min_length=1, max_length=200)
    state: CutState
    editor_sequence_id: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=8000)
    picture_locked_at: str | None = None
    created_at: str = Field(default_factory=_now)


class CinemaDeliveryVariant(BaseModel):
    id: str = Field(default_factory=lambda: _id("delivery"))
    film_id: str = Field(min_length=1, max_length=200)
    purpose: DeliveryPurpose
    label: str = Field(min_length=1, max_length=240)
    editor_sequence_id: str = Field(min_length=1, max_length=200)
    aspect_ratio: AspectRatio = "16:9"
    custom_aspect_ratio: str | None = Field(default=None, max_length=32)
    language: str = Field(default="en", min_length=2, max_length=32)
    captions: bool = False
    watermark_variant: str = Field(default="", max_length=120)
    state: DeliveryState = "planned"
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class CinemaRightsRecord(BaseModel):
    id: str = Field(default_factory=lambda: _id("rights"))
    film_id: str = Field(min_length=1, max_length=200)
    resource_type: ResourceType
    asset_id: str = Field(min_length=1, max_length=200)
    rights_state: RightsState
    consent_state: ConsentState = "not_required"
    evidence_ref: str | None = Field(default=None, max_length=200)
    commercial_use_allowed: bool
    notes: str = Field(default="", max_length=4000)
    created_at: str = Field(default_factory=_now)


class CinemaStructureDocument(BaseModel):
    schema_version: int = 1
    acts: list[CinemaAct] = Field(default_factory=list)
    narrative_sequences: list[CinemaNarrativeSequence] = Field(default_factory=list)
    characters: list[CinemaCharacter] = Field(default_factory=list)
    takes: list[CinemaTake] = Field(default_factory=list)
    cut_records: list[CinemaCutRecord] = Field(default_factory=list)
    delivery_variants: list[CinemaDeliveryVariant] = Field(default_factory=list)
    rights_records: list[CinemaRightsRecord] = Field(default_factory=list)
    updated_at: str = Field(default_factory=_now)


class ActRequest(StrictRequest):
    act_number: int = Field(ge=1, le=100)
    title: str = Field(min_length=1, max_length=240)
    synopsis: str = Field(default="", max_length=12000)


class NarrativeSequenceRequest(StrictRequest):
    sequence_number: int = Field(ge=1, le=10000)
    title: str = Field(min_length=1, max_length=240)
    synopsis: str = Field(default="", max_length=12000)


class SceneAssignmentRequest(StrictRequest):
    scene_id: str = Field(min_length=1, max_length=200)


class CharacterRequest(StrictRequest):
    character_key: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=12000)
    visual_identity_asset_id: str | None = Field(default=None, max_length=200)
    default_language: str = Field(default="en", min_length=2, max_length=32)
    performance_baseline: str = Field(default="", max_length=4000)
    wardrobe_baseline: str = Field(default="", max_length=4000)
    continuity_notes: str = Field(default="", max_length=8000)


class CharacterPatchRequest(StrictRequest):
    name: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=12000)
    visual_identity_asset_id: str | None = Field(default=None, max_length=200)
    default_language: str | None = Field(default=None, min_length=2, max_length=32)
    performance_baseline: str | None = Field(default=None, max_length=4000)
    wardrobe_baseline: str | None = Field(default=None, max_length=4000)
    continuity_notes: str | None = Field(default=None, max_length=8000)


class TakeRequest(StrictRequest):
    take_number: int = Field(ge=1, le=10000)
    slate: str = Field(default="", max_length=240)
    notes: str = Field(default="", max_length=8000)
    rating: int | None = Field(default=None, ge=0, le=5)
    status: TakeStatus = "captured"
    editor_sequence_id: str | None = Field(default=None, max_length=200)
    source_asset_id: str | None = Field(default=None, max_length=200)
    generation_type: GenerationType = "captured"
    provider_runtime_class: str = Field(default="", max_length=160)


class CutStateRequest(StrictRequest):
    state: CutState
    editor_sequence_id: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=8000)
    unlock_picture: bool = False


class DeliveryVariantRequest(StrictRequest):
    purpose: DeliveryPurpose
    label: str = Field(min_length=1, max_length=240)
    editor_sequence_id: str = Field(min_length=1, max_length=200)
    aspect_ratio: AspectRatio = "16:9"
    custom_aspect_ratio: str | None = Field(default=None, max_length=32)
    language: str = Field(default="en", min_length=2, max_length=32)
    captions: bool = False
    watermark_variant: str = Field(default="", max_length=120)
    state: DeliveryState = "planned"


class RightsRequest(StrictRequest):
    resource_type: ResourceType
    asset_id: str = Field(min_length=1, max_length=200)
    rights_state: RightsState
    consent_state: ConsentState = "not_required"
    evidence_ref: str | None = Field(default=None, max_length=200)
    notes: str = Field(default="", max_length=4000)


class CinemaStructureStore:
    """Auxiliary cinema structure registry that references, but never duplicates, canonical film/scene/shot objects."""

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / "work" / STRUCTURE_FILENAME
        self.production = CinemaProductionStore(self.project_dir)

    def load(self) -> CinemaStructureDocument:
        if not self.path.is_file():
            return CinemaStructureDocument()
        return CinemaStructureDocument.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, document: CinemaStructureDocument) -> CinemaStructureDocument:
        self._validate(document)
        document.updated_at = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(document.model_dump(mode="json"), indent=2, ensure_ascii=False)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(payload + "\n", encoding="utf-8")
        temporary.replace(self.path)
        return document

    def _base(self):
        return self.production.load()

    def _film(self, film_id: str):
        film = next((row for row in self._base().films if row.id == film_id), None)
        if film is None:
            raise KeyError(film_id)
        return film

    def _scene(self, scene_id: str):
        scene = next((row for row in self._base().scenes if row.id == scene_id), None)
        if scene is None:
            raise KeyError(scene_id)
        return scene

    def _shot(self, shot_id: str):
        shot = next((row for row in self._base().shots if row.id == shot_id), None)
        if shot is None:
            raise KeyError(shot_id)
        return shot

    def _validate(self, document: CinemaStructureDocument) -> None:
        base = self._base()
        films = {row.id: row for row in base.films}
        scenes = {row.id: row for row in base.scenes}
        shots = {row.id: row for row in base.shots}
        acts = {row.id: row for row in document.acts}
        sequences = {row.id: row for row in document.narrative_sequences}
        characters = {row.id: row for row in document.characters}
        takes = {row.id: row for row in document.takes}
        variants = {row.id: row for row in document.delivery_variants}
        rights = {row.id: row for row in document.rights_records}
        for label, mapping, rows in (
            ("act", acts, document.acts),
            ("narrative sequence", sequences, document.narrative_sequences),
            ("character", characters, document.characters),
            ("take", takes, document.takes),
            ("delivery variant", variants, document.delivery_variants),
            ("rights record", rights, document.rights_records),
        ):
            if len(mapping) != len(rows):
                raise ValueError(f"Duplicate cinema {label} id")

        assigned_scenes: dict[str, str] = {}
        for act in document.acts:
            if act.film_id not in films:
                raise ValueError("Cinema act references a missing film")
            for sequence_id in act.sequence_ids:
                sequence = sequences.get(sequence_id)
                if sequence is None or sequence.act_id != act.id or sequence.film_id != act.film_id:
                    raise ValueError("Act/narrative-sequence relationship is inconsistent")
        for sequence in document.narrative_sequences:
            act = acts.get(sequence.act_id)
            if act is None or act.film_id != sequence.film_id:
                raise ValueError("Narrative sequence references an inconsistent act")
            for scene_id in sequence.scene_ids:
                scene = scenes.get(scene_id)
                if scene is None or scene.film_id != sequence.film_id:
                    raise ValueError("Narrative sequence references a scene outside its film")
                previous = assigned_scenes.setdefault(scene_id, sequence.id)
                if previous != sequence.id:
                    raise ValueError("A cinema scene cannot belong to multiple narrative sequences")
        for character in document.characters:
            if character.film_id not in films:
                raise ValueError("Cinema character references a missing film")
            _safe_ref(character.visual_identity_asset_id, field="Visual identity asset id")
        for take in document.takes:
            shot = shots.get(take.shot_id)
            scene = scenes.get(take.scene_id)
            if shot is None or scene is None or shot.scene_id != scene.id:
                raise ValueError("Cinema take references a missing shot or scene")
            if take.film_id != shot.film_id or scene.film_id != take.film_id:
                raise ValueError("Cinema take film/scene/shot relationship is inconsistent")
            if take.editor_sequence_id:
                self.production.sequence(take.editor_sequence_id, kind="video")
            _safe_ref(take.source_asset_id, field="Take source asset id")
        for record in document.cut_records:
            film = films.get(record.film_id)
            if film is None:
                raise ValueError("Cinema cut record references a missing film")
            self.production.sequence(record.editor_sequence_id, kind="video")
        for variant in document.delivery_variants:
            if variant.film_id not in films:
                raise ValueError("Cinema delivery variant references a missing film")
            self.production.sequence(variant.editor_sequence_id, kind="video")
            if variant.aspect_ratio == "custom" and not str(variant.custom_aspect_ratio or "").strip():
                raise ValueError("Custom delivery aspect ratio requires custom_aspect_ratio")
            if variant.aspect_ratio != "custom" and variant.custom_aspect_ratio is not None:
                raise ValueError("custom_aspect_ratio is only valid for custom delivery variants")
        for record in document.rights_records:
            if record.film_id not in films:
                raise ValueError("Cinema rights record references a missing film")
            _safe_ref(record.asset_id, field="Rights asset id", required=True)
            _safe_ref(record.evidence_ref, field="Rights evidence ref")
            expected = self._commercial_use_allowed(record.rights_state, record.consent_state)
            if record.commercial_use_allowed is not expected:
                raise ValueError("Cinema rights commercial-use state is inconsistent")

    @staticmethod
    def _commercial_use_allowed(rights_state: RightsState, consent_state: ConsentState) -> bool:
        rights_ok = rights_state in {"owned", "licensed", "generated_with_rights"}
        consent_ok = consent_state in {"not_required", "granted"}
        return rights_ok and consent_ok

    def create_act(self, film_id: str, body: ActRequest) -> CinemaAct:
        self._film(film_id)
        document = self.load()
        if any(row.film_id == film_id and row.act_number == body.act_number for row in document.acts):
            raise ValueError("That act number already exists for this film")
        act = CinemaAct(
            film_id=film_id,
            act_number=body.act_number,
            title=_clean(body.title, field="Act title", maximum=240),
            synopsis=str(body.synopsis or "").strip(),
        )
        document.acts.append(act)
        self.save(document)
        return act

    def create_sequence(self, act_id: str, body: NarrativeSequenceRequest) -> CinemaNarrativeSequence:
        document = self.load()
        act = next((row for row in document.acts if row.id == act_id), None)
        if act is None:
            raise KeyError(act_id)
        if any(
            row.film_id == act.film_id and row.sequence_number == body.sequence_number
            for row in document.narrative_sequences
        ):
            raise ValueError("That narrative sequence number already exists for this film")
        sequence = CinemaNarrativeSequence(
            film_id=act.film_id,
            act_id=act.id,
            sequence_number=body.sequence_number,
            title=_clean(body.title, field="Sequence title", maximum=240),
            synopsis=str(body.synopsis or "").strip(),
        )
        document.narrative_sequences.append(sequence)
        act.sequence_ids.append(sequence.id)
        act.updated_at = _now()
        self.save(document)
        return sequence

    def assign_scene(self, sequence_id: str, scene_id: str) -> CinemaNarrativeSequence:
        document = self.load()
        sequence = next((row for row in document.narrative_sequences if row.id == sequence_id), None)
        if sequence is None:
            raise KeyError(sequence_id)
        scene = self._scene(scene_id)
        if scene.film_id != sequence.film_id:
            raise ValueError("Cannot assign a scene from another film")
        for other in document.narrative_sequences:
            if scene_id in other.scene_ids and other.id != sequence.id:
                raise ValueError("A cinema scene cannot belong to multiple narrative sequences")
        if scene_id not in sequence.scene_ids:
            sequence.scene_ids.append(scene_id)
            sequence.updated_at = _now()
            self.save(document)
        return sequence

    def create_character(self, film_id: str, body: CharacterRequest) -> CinemaCharacter:
        self._film(film_id)
        document = self.load()
        key = _clean(body.character_key, field="Character key", maximum=120).lower().replace(" ", "-")
        if any(row.film_id == film_id and row.character_key == key for row in document.characters):
            raise ValueError("That character key already exists for this film")
        character = CinemaCharacter(
            film_id=film_id,
            character_key=key,
            name=_clean(body.name, field="Character name", maximum=240),
            description=str(body.description or "").strip(),
            visual_identity_asset_id=_safe_ref(body.visual_identity_asset_id, field="Visual identity asset id"),
            default_language=str(body.default_language or "en").strip(),
            performance_baseline=str(body.performance_baseline or "").strip(),
            wardrobe_baseline=str(body.wardrobe_baseline or "").strip(),
            continuity_notes=str(body.continuity_notes or "").strip(),
        )
        document.characters.append(character)
        self.save(document)
        return character

    def patch_character(self, character_id: str, body: CharacterPatchRequest) -> CinemaCharacter:
        document = self.load()
        character = next((row for row in document.characters if row.id == character_id), None)
        if character is None:
            raise KeyError(character_id)
        changes = body.model_dump(exclude_unset=True)
        if "name" in changes and changes["name"] is not None:
            changes["name"] = _clean(changes["name"], field="Character name", maximum=240)
        if "visual_identity_asset_id" in changes:
            changes["visual_identity_asset_id"] = _safe_ref(
                changes["visual_identity_asset_id"], field="Visual identity asset id"
            )
        payload = character.model_dump(mode="json")
        payload.update(changes)
        payload["updated_at"] = _now()
        updated = CinemaCharacter.model_validate(payload)
        index = next(i for i, row in enumerate(document.characters) if row.id == character_id)
        document.characters[index] = updated
        self.save(document)
        return updated

    def create_take(self, shot_id: str, body: TakeRequest) -> CinemaTake:
        shot = self._shot(shot_id)
        document = self.load()
        if any(row.shot_id == shot_id and row.take_number == body.take_number for row in document.takes):
            raise ValueError("That take number already exists for this shot")
        if body.editor_sequence_id:
            self.production.sequence(body.editor_sequence_id, kind="video")
        take = CinemaTake(
            film_id=shot.film_id,
            scene_id=shot.scene_id,
            shot_id=shot.id,
            take_number=body.take_number,
            slate=str(body.slate or "").strip(),
            notes=str(body.notes or "").strip(),
            rating=body.rating,
            status=body.status,
            editor_sequence_id=body.editor_sequence_id,
            source_asset_id=_safe_ref(body.source_asset_id, field="Take source asset id"),
            generation_type=body.generation_type,
            provider_runtime_class=str(body.provider_runtime_class or "").strip(),
        )
        if take.status == "selected":
            for existing in document.takes:
                if existing.shot_id == shot.id and existing.status == "selected":
                    existing.status = "circle"
                    existing.updated_at = _now()
        document.takes.append(take)
        self.save(document)
        return take

    def select_take(self, take_id: str) -> CinemaTake:
        document = self.load()
        selected = next((row for row in document.takes if row.id == take_id), None)
        if selected is None:
            raise KeyError(take_id)
        for row in document.takes:
            if row.shot_id == selected.shot_id and row.id != selected.id and row.status == "selected":
                row.status = "circle"
                row.updated_at = _now()
        selected.status = "selected"
        selected.updated_at = _now()
        self.save(document)
        return selected

    def set_cut_state(self, film_id: str, body: CutStateRequest) -> CinemaCutRecord:
        film = self._film(film_id)
        self.production.sequence(body.editor_sequence_id, kind="video")
        document = self.load()
        prior = [row for row in document.cut_records if row.film_id == film_id]
        current = prior[-1] if prior else None
        if current and current.state in {"picture_locked", "mastered"} and body.state in {"rough_cut", "fine_cut"}:
            if not body.unlock_picture:
                raise ValueError("Rolling back a picture lock requires unlock_picture=true")
        if body.state in {"picture_locked", "mastered"}:
            if not film.master_sequence_id:
                raise ValueError("Picture lock/mastering requires the film master sequence")
            if body.editor_sequence_id != film.master_sequence_id:
                raise ValueError("Picture lock/mastering must target the film master sequence")
        record = CinemaCutRecord(
            film_id=film_id,
            state=body.state,
            editor_sequence_id=body.editor_sequence_id,
            notes=str(body.notes or "").strip(),
            picture_locked_at=_now() if body.state in {"picture_locked", "mastered"} else None,
        )
        document.cut_records.append(record)
        self.save(document)
        return record

    def create_delivery_variant(self, film_id: str, body: DeliveryVariantRequest) -> CinemaDeliveryVariant:
        self._film(film_id)
        self.production.sequence(body.editor_sequence_id, kind="video")
        if body.aspect_ratio == "custom":
            _clean(body.custom_aspect_ratio or "", field="Custom aspect ratio", maximum=32)
        elif body.custom_aspect_ratio is not None:
            raise ValueError("custom_aspect_ratio is only valid when aspect_ratio is custom")
        document = self.load()
        variant = CinemaDeliveryVariant(
            film_id=film_id,
            purpose=body.purpose,
            label=_clean(body.label, field="Delivery variant label", maximum=240),
            editor_sequence_id=body.editor_sequence_id,
            aspect_ratio=body.aspect_ratio,
            custom_aspect_ratio=body.custom_aspect_ratio,
            language=str(body.language or "en").strip(),
            captions=body.captions,
            watermark_variant=str(body.watermark_variant or "").strip(),
            state=body.state,
        )
        document.delivery_variants.append(variant)
        self.save(document)
        return variant

    def add_rights_record(self, film_id: str, body: RightsRequest) -> CinemaRightsRecord:
        self._film(film_id)
        asset_id = _safe_ref(body.asset_id, field="Rights asset id", required=True)
        evidence_ref = _safe_ref(body.evidence_ref, field="Rights evidence ref")
        allowed = self._commercial_use_allowed(body.rights_state, body.consent_state)
        record = CinemaRightsRecord(
            film_id=film_id,
            resource_type=body.resource_type,
            asset_id=asset_id or "",
            rights_state=body.rights_state,
            consent_state=body.consent_state,
            evidence_ref=evidence_ref,
            commercial_use_allowed=allowed,
            notes=str(body.notes or "").strip(),
        )
        document = self.load()
        document.rights_records.append(record)
        self.save(document)
        return record

    def shot_list(self, film_id: str) -> dict:
        film = self._film(film_id)
        base = self._base()
        document = self.load()
        scenes = sorted(
            [row for row in base.scenes if row.film_id == film.id],
            key=lambda row: row.scene_number,
        )
        result = []
        for scene in scenes:
            shots = sorted(
                [row for row in base.shots if row.scene_id == scene.id],
                key=lambda row: row.shot_number,
            )
            for shot in shots:
                takes = sorted(
                    [row for row in document.takes if row.shot_id == shot.id],
                    key=lambda row: row.take_number,
                )
                selected = next((row for row in takes if row.status == "selected"), None)
                result.append(
                    {
                        "scene_id": scene.id,
                        "scene_number": scene.scene_number,
                        "slugline": scene.slugline,
                        "shot_id": shot.id,
                        "shot_number": shot.shot_number,
                        "title": shot.title,
                        "shot_size": shot.shot_size,
                        "camera_movement": shot.camera_movement,
                        "lens_mm": shot.lens_mm,
                        "target_duration_seconds": shot.target_duration_seconds,
                        "take_count": len(takes),
                        "selected_take_id": selected.id if selected else None,
                        "storyboard_panel_count": len(shot.storyboard_panel_ids),
                    }
                )
        return {"film_id": film.id, "shots": result, "count": len(result)}

    def readiness(self, film_id: str) -> dict:
        film = self._film(film_id)
        document = self.load()
        cut = next((row for row in reversed(document.cut_records) if row.film_id == film.id), None)
        latest_rights: dict[str, CinemaRightsRecord] = {}
        for row in document.rights_records:
            if row.film_id == film.id:
                latest_rights[row.asset_id] = row
        rights_blockers = [
            {
                "asset_id": row.asset_id,
                "rights_state": row.rights_state,
                "consent_state": row.consent_state,
            }
            for row in latest_rights.values()
            if not row.commercial_use_allowed
        ]
        return {
            "film_id": film.id,
            "cut_state": cut.state if cut else None,
            "picture_locked": bool(cut and cut.state in {"picture_locked", "mastered"}),
            "master_sequence_present": bool(film.master_sequence_id),
            "rights_records_present": bool(latest_rights),
            "rights_blockers": rights_blockers,
            "commercial_rights_clear_from_recorded_evidence": bool(latest_rights) and not rights_blockers,
            "delivery_variant_count": len([row for row in document.delivery_variants if row.film_id == film.id]),
            "truth": "planning_readiness_only_not_distribution_clearance",
        }


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    if not member.plan.has(BASIC_TIMELINE):
        raise HTTPException(403, "Cinema production workflows unlock on the Basic membership tier")
    return member


def _project(project_name: str) -> Path:
    try:
        return project_path(project_name, must_exist=True)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc


def _store(project_name: str) -> CinemaStructureStore:
    return CinemaStructureStore(_project(project_name))


def _execute(callable_):
    try:
        return callable_()
    except KeyError as exc:
        raise HTTPException(404, f"Cinema structure resource not found: {exc.args[0]}") from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/projects/{project_name}/cinema/structure")
def cinema_structure_state(project_name: str, request: Request):
    _member(request)
    store = _store(project_name)
    document = _execute(store.load)
    return {
        "structure": document.model_dump(mode="json"),
        "canonical_production_model": "cinema_production.json",
        "duplicates_film_scene_shot_objects": False,
        "voice_contract_state": "not_owned_by_this_module",
    }


@router.post("/projects/{project_name}/cinema/films/{film_id}/acts")
def create_act(project_name: str, film_id: str, body: ActRequest, request: Request):
    _member(request)
    act = _execute(lambda: _store(project_name).create_act(film_id, body))
    return {"act": act.model_dump(mode="json")}


@router.post("/projects/{project_name}/cinema/acts/{act_id}/sequences")
def create_narrative_sequence(project_name: str, act_id: str, body: NarrativeSequenceRequest, request: Request):
    _member(request)
    sequence = _execute(lambda: _store(project_name).create_sequence(act_id, body))
    return {"narrative_sequence": sequence.model_dump(mode="json")}


@router.post("/projects/{project_name}/cinema/sequences/{sequence_id}/scenes")
def assign_scene_to_sequence(project_name: str, sequence_id: str, body: SceneAssignmentRequest, request: Request):
    _member(request)
    sequence = _execute(lambda: _store(project_name).assign_scene(sequence_id, body.scene_id))
    return {"narrative_sequence": sequence.model_dump(mode="json")}


@router.post("/projects/{project_name}/cinema/films/{film_id}/characters")
def create_character(project_name: str, film_id: str, body: CharacterRequest, request: Request):
    _member(request)
    character = _execute(lambda: _store(project_name).create_character(film_id, body))
    return {"character": character.model_dump(mode="json")}


@router.patch("/projects/{project_name}/cinema/characters/{character_id}")
def patch_character(project_name: str, character_id: str, body: CharacterPatchRequest, request: Request):
    _member(request)
    character = _execute(lambda: _store(project_name).patch_character(character_id, body))
    return {"character": character.model_dump(mode="json")}


@router.post("/projects/{project_name}/cinema/shots/{shot_id}/takes")
def create_take(project_name: str, shot_id: str, body: TakeRequest, request: Request):
    _member(request)
    take = _execute(lambda: _store(project_name).create_take(shot_id, body))
    return {"take": take.model_dump(mode="json")}


@router.post("/projects/{project_name}/cinema/takes/{take_id}/select")
def select_take(project_name: str, take_id: str, request: Request):
    _member(request)
    take = _execute(lambda: _store(project_name).select_take(take_id))
    return {"take": take.model_dump(mode="json")}


@router.post("/projects/{project_name}/cinema/films/{film_id}/cut-state")
def set_cut_state(project_name: str, film_id: str, body: CutStateRequest, request: Request):
    _member(request)
    cut = _execute(lambda: _store(project_name).set_cut_state(film_id, body))
    return {"cut": cut.model_dump(mode="json")}


@router.post("/projects/{project_name}/cinema/films/{film_id}/delivery-variants")
def create_delivery_variant(project_name: str, film_id: str, body: DeliveryVariantRequest, request: Request):
    _member(request)
    variant = _execute(lambda: _store(project_name).create_delivery_variant(film_id, body))
    return {"delivery_variant": variant.model_dump(mode="json")}


@router.post("/projects/{project_name}/cinema/films/{film_id}/rights")
def add_rights_record(project_name: str, film_id: str, body: RightsRequest, request: Request):
    _member(request)
    record = _execute(lambda: _store(project_name).add_rights_record(film_id, body))
    return {"rights_record": record.model_dump(mode="json")}


@router.get("/projects/{project_name}/cinema/films/{film_id}/shot-list")
def get_shot_list(project_name: str, film_id: str, request: Request):
    _member(request)
    return {"shot_list": _execute(lambda: _store(project_name).shot_list(film_id))}


@router.get("/projects/{project_name}/cinema/films/{film_id}/readiness")
def get_cinema_readiness(project_name: str, film_id: str, request: Request):
    _member(request)
    return {"readiness": _execute(lambda: _store(project_name).readiness(film_id))}


__all__ = [
    "ActRequest",
    "CharacterPatchRequest",
    "CharacterRequest",
    "CinemaAct",
    "CinemaCharacter",
    "CinemaCutRecord",
    "CinemaDeliveryVariant",
    "CinemaNarrativeSequence",
    "CinemaRightsRecord",
    "CinemaStructureDocument",
    "CinemaStructureStore",
    "CinemaTake",
    "CutStateRequest",
    "DeliveryVariantRequest",
    "NarrativeSequenceRequest",
    "RightsRequest",
    "SceneAssignmentRequest",
    "TakeRequest",
    "router",
]
