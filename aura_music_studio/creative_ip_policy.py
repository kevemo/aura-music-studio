from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

POLICY_VERSION = "esp-creative-ip-v1-2026-08-28"

IpInputKind = Literal[
    "lyrics",
    "audio_reference",
    "voice_reference",
    "image_reference",
    "video_reference",
    "other_reference",
]


@dataclass(frozen=True)
class IpDecision:
    allowed: bool
    category: str = "allowed"
    reason: str = "No deterministic copyright/likeness conflict was detected."
    safe_alternative: str = ""
    policy_version: str = POLICY_VERSION


# Deterministic high-confidence intent patterns. These are deliberately narrower than a
# general named-entity filter so ordinary discussion of artists, songs, brands and public
# figures is not blocked. Ambiguous similarity still requires human/legal review.
_EXISTING_WORK_REPRODUCTION = re.compile(
    r"\b(?:recreate|replicate|duplicate|copy|remake|reproduce)\b.{0,70}"
    r"\b(?:existing|original|copyrighted|released|commercial)?\s*"
    r"(?:song|track|recording|melody|lyrics|chorus|verse|music video|video|artwork|cover art|image|photo)\b",
    re.I | re.S,
)
_COPY_EXISTING_LYRICS = re.compile(
    r"\b(?:use|copy|paste|include|sing|quote)\b.{0,35}\b(?:lyrics|chorus|verse)\b.{0,35}"
    r"\b(?:from|of)\b",
    re.I | re.S,
)
_DIRECT_ARTIST_IMITATION = re.compile(
    r"\b(?:in the (?:exact )?style of|sound exactly like|look exactly like|"
    r"indistinguishable from|sing exactly like|write exactly like|produce exactly like|"
    r"make (?:it|this|the song|the track|the image|the video) (?:sound|look) like)\b",
    re.I,
)
_VOICE_OR_LIKENESS_CLONE = re.compile(
    r"\b(?:clone|deepfake|replicate|copy|impersonate|imitate)\b.{0,45}"
    r"\b(?:voice|vocal|face|likeness|appearance)\b|"
    r"\b(?:use|make|create)\b.{0,30}\b(?:the )?(?:voice|vocal|face|likeness) of\b",
    re.I | re.S,
)
_BYPASS_RIGHTS = re.compile(
    r"\b(?:without (?:permission|consent|a license)|ignore (?:copyright|licensing|rights)|"
    r"bypass (?:copyright|rights|licensing)|remove (?:copyright|watermark|provenance) protection)\b",
    re.I,
)


def evaluate_ip_text(
    text: str | None,
    *,
    voice_or_likeness_consent_confirmed: bool = False,
) -> IpDecision:
    value = (text or "").strip()
    if not value:
        return IpDecision(True)

    if _BYPASS_RIGHTS.search(value):
        return IpDecision(
            False,
            "rights_bypass",
            "The request asks the creation system to bypass copyright, licensing, consent or provenance safeguards.",
            "Use original material or material you own/have licensed, and keep the rights/provenance record attached.",
        )

    if _VOICE_OR_LIKENESS_CLONE.search(value) and not voice_or_likeness_consent_confirmed:
        return IpDecision(
            False,
            "unauthorized_voice_or_likeness",
            "The request appears to clone or imitate a real person's voice or likeness without verified authorization.",
            "Use an Aura Voice Profile or likeness reference with explicit owner consent and a verified rights record, or request a fictional/generic voice or appearance.",
        )

    if _DIRECT_ARTIST_IMITATION.search(value):
        return IpDecision(
            False,
            "direct_artist_or_creator_imitation",
            "The request asks for direct imitation of a named creator/person rather than an original work.",
            "Describe neutral creative attributes instead: genre, era, tempo, instrumentation, vocal range, production texture, lighting, camera language, palette, mood and structure.",
        )

    if _COPY_EXISTING_LYRICS.search(value):
        return IpDecision(
            False,
            "existing_lyrics_reproduction",
            "The request appears to copy lyrics from an existing work without a verified rights basis.",
            "Write new lyrics, use lyrics you authored, or attach a rights-cleared lyric source with permission evidence.",
        )

    if _EXISTING_WORK_REPRODUCTION.search(value):
        return IpDecision(
            False,
            "existing_work_reproduction",
            "The request appears to reproduce an existing protected work rather than create an original work.",
            "Create a new work from high-level attributes and original material, or attach a rights-cleared source for an authorized transformation.",
        )

    return IpDecision(True)


def enforce_creation_ip_policy(
    *texts: str | None,
    context: str = "creation",
    voice_or_likeness_consent_confirmed: bool = False,
) -> None:
    for text in texts:
        decision = evaluate_ip_text(
            text,
            voice_or_likeness_consent_confirmed=voice_or_likeness_consent_confirmed,
        )
        if not decision.allowed:
            suffix = f" Safe alternative: {decision.safe_alternative}" if decision.safe_alternative else ""
            raise ValueError(
                f"{context} blocked by {decision.policy_version} ({decision.category}): "
                f"{decision.reason}{suffix}"
            )


def require_input_rights(
    kind: IpInputKind,
    *,
    provided: bool,
    rights_confirmed: bool,
) -> None:
    if provided and not rights_confirmed:
        labels = {
            "lyrics": "lyrics",
            "audio_reference": "audio/reference recording",
            "voice_reference": "voice reference",
            "image_reference": "image/reference artwork",
            "video_reference": "video/reference footage",
            "other_reference": "reference material",
        }
        raise ValueError(
            f"Confirm that you own or have permission/license to use the {labels[kind]} before generation."
        )


def build_clearance_record(
    *,
    user_lyrics_provided: bool = False,
    lyrics_rights_confirmed: bool = False,
    reference_audio_provided: bool = False,
    reference_audio_rights_confirmed: bool = False,
    approved_voice_requested: bool = False,
) -> dict:
    return {
        "policy_version": POLICY_VERSION,
        "pre_generation_ip_firewall": True,
        "user_lyrics": {
            "provided": bool(user_lyrics_provided),
            "rights_confirmed": bool(lyrics_rights_confirmed) if user_lyrics_provided else None,
        },
        "reference_audio": {
            "provided": bool(reference_audio_provided),
            "rights_confirmed": bool(reference_audio_rights_confirmed) if reference_audio_provided else None,
        },
        "approved_voice_requested": bool(approved_voice_requested),
        "approved_voice_requires_active_consent_profile": bool(approved_voice_requested),
        "direct_imitation_prohibited": True,
        "existing_work_reproduction_prohibited_without_rights": True,
        "commercial_use_is_not_a_copyright_guarantee": True,
        "copyright_protectability_varies_by_jurisdiction": True,
        "automatic_legal_clearance": False,
    }


def public_ip_policy_summary() -> dict:
    return {
        "version": POLICY_VERSION,
        "principles": [
            "Original creation rather than direct imitation",
            "No recreation of existing songs, recordings, lyrics, melodies, artwork or video without a verified rights basis",
            "No cloning or impersonation of another person's voice or likeness without explicit authorization",
            "User-provided lyrics and references require ownership/license confirmation before generation",
            "Commercial-use permission does not guarantee copyright protection or non-infringement",
            "Rights/provenance evidence should remain attached to creative projects and exports",
        ],
        "automatic_legal_clearance": False,
        "note": (
            "This deterministic preflight is a production safety gate, not a copyright search engine or legal opinion. "
            "Similarity and jurisdiction-specific questions can still require human/legal review."
        ),
    }
