from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

POLICY_VERSION = "esp-professional-creation-v1"

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
    for text in texts:
        decision = evaluate_text(text)
        if not decision.allowed:
            raise ValueError(
                f"{context} blocked by {decision.policy_version}: {decision.reason} "
                "Reframe the idea so it does not target or demean people, provoke harassment/drama, promote hate, or glorify violent conflict."
            )


def public_policy_summary() -> dict:
    return {
        "version": POLICY_VERSION,
        "principles": [
            "No hate or dehumanisation",
            "No racial or protected-class abuse",
            "No targeted bullying, harassment or doxxing",
            "No deliberate drama/feud/rage-bait campaigns against people",
            "No violent-conflict propaganda or glorification",
            "Respectful professional creator conduct",
            "Platform/community rules remain an additional requirement",
        ],
        "owner_blocked_terms_enabled": bool(_owner_blocked_terms()),
        "note": (
            "This local policy is a baseline, not a substitute for official platform moderation or evolving platform rules. "
            "Owner-managed blocked terms can extend it without source-code changes."
        ),
    }
