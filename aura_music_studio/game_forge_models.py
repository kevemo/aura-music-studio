from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


GameDimension = Literal["2d", "3d"]
# Aura owns the primary runtime. External engines are compatibility/export targets only.
GameEngine = Literal["aura2d", "aura3d", "phaser4", "playcanvas", "babylon", "godot"]
GameStatus = Literal["draft", "building", "review_ready", "approved_test", "public_test", "archived"]
ContentIntensity = Literal["none", "mild", "moderate", "strong", "graphic"]


class GameContentDisclosure(BaseModel):
    violence: ContentIntensity = "none"
    blood_gore: ContentIntensity = "none"
    fear_horror: ContentIntensity = "none"
    language: ContentIntensity = "none"
    sexual_content: ContentIntensity = "none"
    nudity: ContentIntensity = "none"
    drugs: ContentIntensity = "none"
    alcohol_tobacco: ContentIntensity = "none"
    gambling_simulation: ContentIntensity = "none"
    in_game_purchases: bool = False
    paid_random_items: bool = False
    real_money_gambling: bool = False
    online_multiplayer: bool = False
    user_chat: bool = False
    user_generated_content: bool = False
    shares_location: bool = False
    unrestricted_internet: bool = False
    collects_personal_data: bool = False
    child_directed: bool = False
    advertising: bool = False
    profiling_ads: bool = False
    moderation_controls: bool = False
    report_and_block_controls: bool = False
    parental_controls: bool = False
    privacy_policy_ready: bool = False
    age_assurance_ready: bool = False


class GameRatingAssessment(BaseModel):
    assessment_version: str = "pulsar-game-rating-preflight-v1"
    generated_at: str = Field(default_factory=_now)
    content_hash: str
    provisional_only: bool = True
    official_rating: bool = False
    suggested_age_floor: int = 3
    suggested_age_band: str = "3+"
    regional_estimates: dict[str, str] = Field(default_factory=dict)
    content_descriptors: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    public_test_allowed: bool = False
    legal_review_recommended: bool = True
    note: str = (
        "Pulsar Safety & Rating Assessment only. This is not an official ESRB, PEGI, IARC, USK, ACB/Australian Classification or other authority rating."
    )


class VerifiedOfficialRating(BaseModel):
    authority: str = Field(min_length=1, max_length=80)
    rating: str = Field(min_length=1, max_length=80)
    certification_reference: str = Field(min_length=1, max_length=240)
    verified_external_result: bool = False
    verified_at: str | None = None


class GameBuild(BaseModel):
    build_id: str = Field(default_factory=lambda: f"build_{uuid4().hex}")
    content_hash: str
    runtime: str = "aura_game_runtime_v1"
    requested_engine: GameEngine
    created_at: str = Field(default_factory=_now)
    private_playtest_ready: bool = True
    arbitrary_server_code_executed: bool = False
    network_access_enabled: bool = False


class GameDNA(BaseModel):
    schema_version: int = 1
    id: str = Field(default_factory=lambda: f"game_{uuid4().hex}")
    title: str = Field(min_length=1, max_length=160)
    prompt: str = Field(default="", max_length=12000)
    genre: str = Field(default="adventure", max_length=120)
    niches: list[str] = Field(default_factory=list, max_length=30)
    dimension: GameDimension = "2d"
    engine_target: GameEngine = "aura2d"
    target_platforms: list[str] = Field(default_factory=lambda: ["browser"], max_length=12)
    camera: str = Field(default="auto", max_length=80)
    synopsis: str = Field(default="", max_length=8000)
    mechanics: list[str] = Field(default_factory=list, max_length=80)
    controls: list[str] = Field(default_factory=list, max_length=40)
    scenes: list[str] = Field(default_factory=list, max_length=80)
    art_direction: str = Field(default="", max_length=4000)
    audio_direction: str = Field(default="", max_length=4000)
    npc_direction: str = Field(default="", max_length=4000)
    multiplayer_direction: str = Field(default="", max_length=4000)
    rights_confirmed: bool = False
    rights_attestation: str = Field(default="", max_length=2000)
    content: GameContentDisclosure = Field(default_factory=GameContentDisclosure)
    status: GameStatus = "draft"
    version: int = 1
    rating_assessment: GameRatingAssessment | None = None
    official_ratings: list[VerifiedOfficialRating] = Field(default_factory=list, max_length=12)
    latest_build: GameBuild | None = None
    public_id: str | None = None
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    metadata: dict = Field(default_factory=dict)

    def touch(self) -> None:
        self.version += 1
        self.updated_at = _now()

    @property
    def actively_editable(self) -> bool:
        return self.status in {"draft", "building", "review_ready"}


ENGINE_REGISTRY: dict[str, dict] = {
    "aura2d": {
        "label": "Aura Game Engine 2D",
        "dimension": "2d",
        "license": "Pulsar native",
        "role": "Primary Aura-owned browser 2D runtime, scene system, controls, physics abstraction and playtest target",
        "commercially_usable": True,
        "native": True,
        "adapter_stage": "foundation",
    },
    "aura3d": {
        "label": "Aura Game Engine 3D",
        "dimension": "3d",
        "license": "Pulsar native",
        "role": "Primary Aura-owned WebGPU/WebGL 3D runtime and scene/material/animation abstraction",
        "commercially_usable": True,
        "native": True,
        "adapter_stage": "foundation",
    },
    "phaser4": {
        "label": "Phaser 4 export adapter",
        "dimension": "2d",
        "license": "MIT",
        "role": "Optional compatibility/export adapter; never the Aura Game Forge core",
        "commercially_usable": True,
        "native": False,
        "adapter_stage": "planned",
    },
    "playcanvas": {
        "label": "PlayCanvas export adapter",
        "dimension": "3d",
        "license": "MIT",
        "role": "Optional WebGPU/WebGL compatibility/export adapter; never the Aura Game Forge core",
        "commercially_usable": True,
        "native": False,
        "adapter_stage": "planned",
    },
    "babylon": {
        "label": "Babylon.js export adapter",
        "dimension": "3d",
        "license": "Apache-2.0",
        "role": "Optional advanced WebGPU/WebXR compatibility/export adapter",
        "commercially_usable": True,
        "native": False,
        "adapter_stage": "planned",
    },
    "godot": {
        "label": "Godot export adapter",
        "dimension": "2d/3d",
        "license": "MIT",
        "role": "Optional native desktop/mobile/console-oriented compatibility/export adapter",
        "commercially_usable": True,
        "native": False,
        "adapter_stage": "planned",
    },
}
