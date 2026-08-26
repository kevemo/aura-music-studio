from __future__ import annotations

import hashlib
import json

from .content_safety import evaluate_text
from .game_forge_models import GameDNA, GameRatingAssessment

_INTENSITY_SCORE = {"none": 0, "mild": 1, "moderate": 2, "strong": 3, "graphic": 4}


def rating_content_hash(game: GameDNA) -> str:
    """Hash every creator-controlled field that can materially affect a rating/playtest decision."""
    payload = {
        "title": game.title,
        "prompt": game.prompt,
        "genre": game.genre,
        "niches": game.niches,
        "dimension": game.dimension,
        "engine_target": game.engine_target,
        "target_platforms": game.target_platforms,
        "camera": game.camera,
        "synopsis": game.synopsis,
        "mechanics": game.mechanics,
        "controls": game.controls,
        "scenes": game.scenes,
        "art_direction": game.art_direction,
        "audio_direction": game.audio_direction,
        "npc_direction": game.npc_direction,
        "multiplayer_direction": game.multiplayer_direction,
        "rights_confirmed": game.rights_confirmed,
        "rights_attestation": game.rights_attestation,
        "content": game.content.model_dump(mode="json"),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _age_band(age: int) -> str:
    if age >= 18:
        return "18+"
    if age >= 16:
        return "16+"
    if age >= 12:
        return "12+"
    if age >= 7:
        return "7+"
    return "3+"


def _esrb_estimate(age: int) -> str:
    if age >= 18:
        return "Likely Mature 17+ / higher-review range"
    if age >= 16:
        return "Likely Mature 17+ range"
    if age >= 12:
        return "Likely Teen range"
    if age >= 7:
        return "Likely Everyone 10+ range"
    return "Likely Everyone range"


def _pegi_estimate(age: int) -> str:
    if age >= 18:
        return "Likely PEGI 18 range"
    if age >= 16:
        return "Likely PEGI 16 range"
    if age >= 12:
        return "Likely PEGI 12 range"
    if age >= 7:
        return "Likely PEGI 7 range"
    return "Likely PEGI 3 range"


def _australia_estimate(age: int, game: GameDNA) -> str:
    c = game.content
    # Australian Classification rules from 22 Sep 2024 impose these specific floors.
    if c.real_money_gambling or _INTENSITY_SCORE[c.gambling_simulation] >= 2:
        return "At least R 18+ review range due to simulated/real-money gambling"
    if c.paid_random_items:
        return "At least M review range due to paid chance-based items"
    if age >= 18:
        return "Likely R 18+ review range"
    if age >= 16:
        return "Likely MA 15+ review range"
    if age >= 12:
        return "Likely M/PG review range"
    if age >= 7:
        return "Likely PG review range"
    return "Likely G review range"


def assess_game(game: GameDNA) -> GameRatingAssessment:
    """Create a conservative internal preflight, never an official rating or legal certification."""
    c = game.content
    descriptors: list[str] = []
    blockers: list[str] = []
    warnings: list[str] = []
    requirements: list[str] = []
    age = 3

    combined_text = "\n".join(
        [
            game.title,
            game.prompt,
            game.genre,
            game.synopsis,
            game.art_direction,
            game.audio_direction,
            game.npc_direction,
            game.multiplayer_direction,
            *game.mechanics,
            *game.scenes,
        ]
    )
    policy = evaluate_text(combined_text)
    if not policy.allowed:
        blockers.append(f"ESP creation-safety policy: {policy.reason}")

    def content_level(field: str, label: str, *, mild_age: int, moderate_age: int, strong_age: int, graphic_age: int = 18):
        nonlocal age
        value = getattr(c, field)
        score = _INTENSITY_SCORE[value]
        if score:
            descriptors.append(f"{label}: {value}")
        floors = {1: mild_age, 2: moderate_age, 3: strong_age, 4: graphic_age}
        if score:
            age = max(age, floors[score])

    content_level("violence", "Violence", mild_age=7, moderate_age=12, strong_age=16)
    content_level("blood_gore", "Blood/gore", mild_age=12, moderate_age=16, strong_age=18)
    content_level("fear_horror", "Fear/horror", mild_age=7, moderate_age=12, strong_age=16)
    content_level("language", "Language", mild_age=7, moderate_age=12, strong_age=16)
    content_level("sexual_content", "Sexual content", mild_age=12, moderate_age=16, strong_age=18)
    content_level("nudity", "Nudity", mild_age=12, moderate_age=16, strong_age=18)
    content_level("drugs", "Drugs", mild_age=12, moderate_age=16, strong_age=18)
    content_level("alcohol_tobacco", "Alcohol/tobacco", mild_age=7, moderate_age=12, strong_age=16)
    content_level("gambling_simulation", "Simulated gambling", mild_age=12, moderate_age=18, strong_age=18)

    if c.in_game_purchases:
        descriptors.append("In-game purchases")
        requirements.append("Clearly disclose real-money purchases before public/commercial release.")
    if c.paid_random_items:
        descriptors.append("Paid random items / loot-box mechanics")
        age = max(age, 16)
        warnings.append("Paid chance-based rewards trigger enhanced rating and consumer-law review in multiple regions.")
        requirements.append("Run jurisdiction-specific monetisation review before commercial release.")
    if c.real_money_gambling:
        descriptors.append("Real-money gambling")
        age = 18
        blockers.append("Real-money gambling is not eligible for Pulsar public playtesting in this foundation release.")

    if c.online_multiplayer:
        descriptors.append("Online multiplayer")
    if c.user_chat:
        descriptors.append("Users interact / chat")
        age = max(age, 12)
        if not (c.moderation_controls and c.report_and_block_controls):
            blockers.append("User chat requires moderation plus report/block controls before public testing.")
    if c.user_generated_content:
        descriptors.append("User-generated content")
        if not (c.moderation_controls and c.report_and_block_controls):
            blockers.append("UGC requires moderation plus report/block controls before public testing.")
    if c.shares_location:
        descriptors.append("Shares location")
        requirements.append("Location sharing needs explicit privacy review and age-appropriate defaults.")
    if c.unrestricted_internet:
        descriptors.append("Unrestricted internet access")
        blockers.append("Unrestricted internet access is disabled for Pulsar public playtests.")
    if c.collects_personal_data:
        descriptors.append("Collects personal data")
        if not c.privacy_policy_ready:
            blockers.append("Personal-data collection requires a privacy policy before public testing.")
    if c.advertising:
        descriptors.append("Advertising")
    if c.profiling_ads:
        descriptors.append("Profiling/targeted advertising")
        if c.child_directed:
            blockers.append("Child-directed games cannot enable profiling advertising in Pulsar public playtests.")
    if c.child_directed:
        descriptors.append("Child-directed / likely child audience")
        if c.collects_personal_data and not c.age_assurance_ready:
            blockers.append("Child-directed personal-data collection requires an approved age/consent design before public testing.")
        if (c.user_chat or c.user_generated_content) and not c.parental_controls:
            warnings.append("Child-accessible social features should include age-appropriate parental/safety controls and high-privacy defaults.")

    if not game.rights_confirmed:
        blockers.append("Creator rights/provenance attestation is required before public testing.")

    # A creator declaration cannot turn this internal result into an official authority rating.
    for record in game.official_ratings:
        if not record.verified_external_result:
            warnings.append(f"Unverified external rating record ignored: {record.authority} {record.rating}.")

    if c.online_multiplayer or c.user_chat or c.user_generated_content or c.collects_personal_data:
        requirements.append("Complete regional privacy, minors-safety, moderation and retention review before commercial release.")
    requirements.append("Obtain official storefront/authority ratings where required for distribution; IARC is accessed through participating storefronts.")
    requirements.append("Re-run this assessment after any material content, monetisation, networking or privacy change.")

    age = 18 if age >= 18 else 16 if age >= 16 else 12 if age >= 12 else 7 if age >= 7 else 3
    return GameRatingAssessment(
        content_hash=rating_content_hash(game),
        suggested_age_floor=age,
        suggested_age_band=_age_band(age),
        regional_estimates={
            "North America / ESRB-like estimate": _esrb_estimate(age),
            "Europe / PEGI-like estimate": _pegi_estimate(age),
            "Australia / ACB-like estimate": _australia_estimate(age, game),
        },
        content_descriptors=sorted(set(descriptors)),
        blockers=blockers,
        warnings=warnings,
        requirements=requirements,
        public_test_allowed=not blockers,
        legal_review_recommended=True,
    )
