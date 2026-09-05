from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .creative_abuse_policy import enforce_creation_abuse_policy, public_abuse_policy_summary
from .creative_ip_policy import enforce_creation_ip_policy, public_ip_policy_summary
from .safeguarding_runtime import assert_self_hosted_core_ready

POLICY_VERSION = "esp-professional-creation-v2-2026-08-29"

# The creation-safety module is imported during normal application assembly. Keep the core
# safeguarding boundary fail-closed: a future change that makes a critical safeguard remote-only
# or permits external evidence to override a local Aura block must prevent startup/release.
assert_self_hosted_core_ready()

# These are intent patterns, not an exhaustive moderation system. Production deployments
# can add owner-managed blocked terms (including slurs) without committing them to source.
_BLOCK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hate_or_dehumanisation", re.compile(r"\b(kill|exterminate|wipe out|eradicate)\s+(all\s+)?(people|men|women|immigrants|foreigners|a race|an ethnicity|a religion|a nationality)\b", re.I)),
    ("hate_or_dehumanisation", re.compile(r"\b(racial|ethnic)\s+(superiority|inferiority|purity)\b", re.I)),
    ("hate_or_dehumanisation", re.compile(r"\b(ethnic cleansing|genocide is good|praise genocide|celebrate genocide)\b", re.I)),
    ("targeted_harassment", re.compile(r"\b(bully|harass|dogpile|mob|humiliate|terrorise|terrorize)\s+(him|her|them|this person|that person|the creator|the user)\b", re.I)),
    ("targeted_harassment", re.compile(r"\b(ruin|destroy)\s+(his|her|their)\s+(life|reputation|career)\b", re.I)),
    ("targeted_harassment", re.compile(r"\b(doxx?|publish)\s+(his|her|their|someone'?s)\s+(address|phone|private info|personal information)\b", re.I)),
    ("drama_or_division", re.compile(r"\b(start|create|stir up|manufacture)\s+(drama|a feud|conflict)\b", re.I)),
    ("drama_or_division", re.compile(r"\b(rage bait|hate campaign|smear campaign|pile on)\b", re.I)),
    ("violent_conflict_glorification", re.compile(r"\b(glorify|celebrate|promote|cheer for)\s+(war|civilian deaths|mass killing|terror attacks?)\b", re.I)),
    ("violent_conflict_glorification", re.compile(r"\b(war propaganda|celebrate civilian casualties|praise mass killing)\b", re.I)),
)


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    category: str = "allowed"
    reason: str = "Content is within the ESP professional-creation baseline."
    policy_version: str = POLICY_VERSION


def _owner_blocked_terms() -> list[str]:
    terms: list[str] = []
    env_terms = os.getenv("ESP_BLOCKED_TERMS", "")
    terms.extend(part.strip().casefold() for part in env_terms.split(",") if part.strip())
    configured_file = os.getenv("ESP_BLOCKED_TERMS_FILE", "")
    if configured_file:
        path = Path(configured_file)
        if path.is_file():
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    value = line.strip()
                    if value and not value.startswith("#"):
                        terms.append(value.casefold())
            except OSError:
                pass
    # Longest first prevents a shorter substring from hiding the more specific match.
    return sorted(set(terms), key=len, reverse=True)


def evaluate_text(text: str | None) -> SafetyDecision:
    value = (text or "").strip()
    if not value:
        return SafetyDecision(True)
    folded = value.casefold()
    for term in _owner_blocked_terms():
        if term and term in folded:
            return SafetyDecision(
                False,
                "owner_blocked_term",
                "This request contains language blocked by the ESP owner safety list.",
            )
    for category, pattern in _BLOCK_PATTERNS:
        if pattern.search(value):
            return SafetyDecision(
                False,
                category,
                "This request conflicts with ESP professional standards for respectful, non-hateful, non-harassing and non-divisive creation.",
            )
    return SafetyDecision(True)


def enforce_creation_policy(*texts: str | None, context: str = "creation") -> None:
    # High-severity illegal-use/abuse intent is checked first so a prohibited renderer request
    # cannot be downgraded into an ordinary professional-conduct or IP warning.
    enforce_creation_abuse_policy(*texts, context=context)
    for text in texts:
        decision = evaluate_text(text)
        if not decision.allowed:
            raise ValueError(
                f"{context} blocked by {decision.policy_version}: {decision.reason} "
                "Reframe the idea so it does not target or demean people, provoke harassment/drama, promote hate, or glorify violent conflict."
            )
    # Copyright/IP/likeness preflight is part of the same creation boundary so all callers
    # already using enforce_creation_policy inherit the protection before a renderer/model
    # can be reached. This does not assert legal clearance or perform similarity matching.
    enforce_creation_ip_policy(*texts, context=context)


def public_policy_summary() -> dict:
    return {
        "version": POLICY_VERSION,
        "principles": [
            "No sexualised or intimate creation involving children or minors",
            "No non-consensual or unverified real-person intimate synthetic media",
            "No synthetic identity/voice/video used for fraud or financial deception",
            "No hate or dehumanisation",
            "No racial or protected-class abuse",
            "No targeted bullying, harassment or doxxing",
            "No deliberate drama/feud/rage-bait campaigns against people",
            "No violent-conflict propaganda or glorification",
            "Respectful professional creator conduct",
            "Platform/community rules remain an additional requirement",
        ],
        "creative_abuse": public_abuse_policy_summary(),
        "creative_ip": public_ip_policy_summary(),
        "owner_blocked_terms_enabled": bool(_owner_blocked_terms()),
        "automatic_legal_clearance": False,
        "note": (
            "This local policy is a strict creation baseline, not a substitute for official platform moderation, qualified legal review, "
            "copyright review or evolving laws/platform rules. Owner-managed blocked terms can extend it without source-code changes."
        ),
    }
