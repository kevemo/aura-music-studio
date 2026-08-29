from __future__ import annotations

import re
from dataclasses import dataclass

POLICY_VERSION = "esp-creative-abuse-v1-2026-08-29"


@dataclass(frozen=True)
class AbuseDecision:
    allowed: bool
    category: str = "allowed"
    reason: str = "No high-confidence prohibited creation intent was detected."
    safe_alternative: str = ""
    policy_version: str = POLICY_VERSION


# This is a narrow deterministic preflight, not a classifier and not a legal decision engine.
# It intentionally targets high-confidence creation intent and leaves neutral discussion,
# safeguarding education and defensive anti-fraud work available.
_CREATION_VERB = re.compile(
    r"\b(?:create|generate|make|render|draw|produce|synthesi[sz]e|edit|transform|animate|depict|show|build)\b",
    re.I,
)
_MINOR = re.compile(
    r"\b(?:child(?:ren)?|minor|underage|under[- ]?18|(?:[0-9]|1[0-7])[- ]?year[- ]?old|(?:[0-9]|1[0-7])\s*yo)\b",
    re.I,
)
_SEXUAL_OR_INTIMATE = re.compile(
    r"\b(?:nude|nudity|naked|porn(?:ography|ographic)?|sexual(?:ly|ised|ized)?|sexually explicit|"
    r"intimate image|explicit image|nudify|undress|remove (?:his|her|their|the) clothes|genitals?)\b",
    re.I,
)
_PROTECTIVE_CONTEXT = re.compile(
    r"\b(?:prevent|prevention|detect|detection|report|reporting|safeguard|safeguarding|awareness|"
    r"education(?:al)?|training|policy|law|legal guidance|moderation|protect|protection|anti[- ]fraud|counter[- ]fraud)\b",
    re.I,
)

_INTIMATE_SYNTHETIC = re.compile(
    r"\b(?:deepfake|nudify|fake nude|synthetic nude|intimate deepfake|sexual deepfake|"
    r"remove (?:his|her|their|the) clothes from (?:a |the )?(?:photo|image|video)|"
    r"make (?:him|her|them|this person|that person) (?:nude|naked))\b",
    re.I,
)
_REAL_PERSON_SIGNAL = re.compile(
    r"\b(?:real person|actual person|celebrity|public figure|creator|influencer|actor|actress|singer|"
    r"politician|teacher|boss|coworker|co-worker|ex[- ]?(?:partner|girlfriend|boyfriend|wife|husband)|"
    r"him|her|them|this person|that person|their face|his face|her face)\b",
    re.I,
)
_NONCONSENT_SIGNAL = re.compile(
    r"\b(?:without (?:their|his|her)?\s*(?:consent|permission)|non[- ]consensual|secretly|without them knowing)\b",
    re.I,
)

_IMPERSONATION = re.compile(
    r"\b(?:clone|deepfake|impersonate|pretend to be|pose as|fake|spoof|synthetic)\b.{0,55}"
    r"\b(?:voice|video|face|identity|person|caller|executive|ceo|cfo|manager|bank|relative|family member)\b|"
    r"\b(?:voice|video|identity)\b.{0,45}\b(?:clone|deepfake|impersonation|spoof)\b",
    re.I | re.S,
)
_FINANCIAL_OR_CREDENTIAL_OBJECTIVE = re.compile(
    r"\b(?:bank transfer|wire transfer|payment|send(?:ing)? money|transfer(?:ring)? money|invoice|gift card|"
    r"crypto(?:currency)?|account number|bank details|card details|credit card|debit card|password|passcode|"
    r"one[- ]time (?:passcode|code)|otp|login|credential|security code|authentication code|financial account|"
    r"authori[sz](?:e|ing) (?:a )?payment)\b",
    re.I,
)
_DECEPTIVE_OBJECTIVE = re.compile(
    r"\b(?:trick|deceive|convince|fool|mislead|scam|defraud|steal|obtain|extract|pressure)\b",
    re.I,
)
_EXTORTION = re.compile(r"\b(?:blackmail|extort|extortion|ransom|threaten to share|threaten to post)\b", re.I)


def evaluate_abuse_text(text: str | None) -> AbuseDecision:
    value = (text or "").strip()
    if not value:
        return AbuseDecision(True)

    protective = bool(_PROTECTIVE_CONTEXT.search(value))

    # Sexualised/indecent creation involving a minor is never an eligible creative workflow.
    # A narrow protective-context carve-out permits policy/education/defensive discussion where
    # the text is not itself asking to render explicit material.
    if _MINOR.search(value) and _SEXUAL_OR_INTIMATE.search(value) and _CREATION_VERB.search(value):
        explicit_render = bool(re.search(r"\b(?:nude|naked|nudify|porn(?:ography|ographic)?|sexually explicit|explicit image)\b", value, re.I))
        if explicit_render or not protective:
            return AbuseDecision(
                False,
                "sexualised_minor_or_csam_creation",
                "Sexual or intimate creation involving a child or minor is prohibited.",
                "Use age-appropriate, non-sexual material. Safeguarding education may discuss prevention and reporting without depicting sexualised minors.",
            )

    intimate_synthesis = bool(_INTIMATE_SYNTHETIC.search(value))
    if intimate_synthesis and _EXTORTION.search(value):
        return AbuseDecision(
            False,
            "synthetic_intimate_extortion",
            "The request combines synthetic intimate media with blackmail, extortion or a threat to distribute it.",
            "Create lawful non-coercive content instead. Safety reporting tools should be used for actual threats or abuse.",
        )

    if intimate_synthesis and (_REAL_PERSON_SIGNAL.search(value) or _NONCONSENT_SIGNAL.search(value)):
        return AbuseDecision(
            False,
            "nonconsensual_or_unverified_intimate_synthetic_person",
            "The request appears to create or alter intimate synthetic media of a real person without an approved consent pathway.",
            "Use a fictional adult or non-intimate transformation. Real-person intimate synthesis is not available through the ordinary creation workflow.",
        )

    if (
        _IMPERSONATION.search(value)
        and _FINANCIAL_OR_CREDENTIAL_OBJECTIVE.search(value)
        and (_DECEPTIVE_OBJECTIVE.search(value) or not protective)
    ):
        return AbuseDecision(
            False,
            "fraud_or_financial_impersonation",
            "The request appears to use synthetic identity, voice or video to obtain money, credentials or financial authorization through deception.",
            "Use clearly disclosed fictional/synthetic identities, or build defensive fraud-detection and awareness tooling without impersonating a real person to obtain value or credentials.",
        )

    return AbuseDecision(True)


def enforce_creation_abuse_policy(*texts: str | None, context: str = "creation") -> None:
    for text in texts:
        decision = evaluate_abuse_text(text)
        if not decision.allowed:
            suffix = f" Safe alternative: {decision.safe_alternative}" if decision.safe_alternative else ""
            raise ValueError(
                f"{context} blocked by {decision.policy_version} ({decision.category}): "
                f"{decision.reason}{suffix}"
            )


def public_abuse_policy_summary() -> dict:
    return {
        "version": POLICY_VERSION,
        "platform_scope": "global",
        "principles": [
            "No sexualised or intimate creation involving children or minors",
            "No real-person intimate deepfakes or nudification through the ordinary creation workflow",
            "No synthetic intimate media for blackmail, extortion or coercion",
            "No voice, video or identity cloning to deceive people into providing money, credentials or financial authorization",
            "Lawful general coding, defensive anti-fraud work and safeguarding education remain allowed",
        ],
        "automatic_legal_determination": False,
        "legal_advice": False,
        "legal_coverage_complete": False,
        "note": (
            "These are strict platform safeguards, not a declaration that every blocked request is criminal in every jurisdiction "
            "or that every allowed request is legally cleared. Applicable law, consent, rights and platform rules still require review."
        ),
    }


__all__ = [
    "POLICY_VERSION",
    "AbuseDecision",
    "enforce_creation_abuse_policy",
    "evaluate_abuse_text",
    "public_abuse_policy_summary",
]
