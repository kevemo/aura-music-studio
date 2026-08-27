from __future__ import annotations

import json
import os
import sqlite3
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .owner_auth import owner_authorized

COMPLIANCE_VERSION = "2026-08-27.1"
PASS_PERCENT = 90

member_router = APIRouter(prefix="/esp/compliance", tags=["ESP Compliance Training"])
owner_router = APIRouter(prefix="/owner/esp/compliance", tags=["Owner ESP Compliance"])

POLICY_PACK = {
    "community_safety": {
        "title": "Community safety: no hate, bullying, harassment or threats",
        "source": "https://www.tiktok.com/community-guidelines/",
    },
    "live_host_responsibility": {
        "title": "LIVE host responsibility, guests and third-party tools",
        "source": "https://www.tiktok.com/community-guidelines/en/accounts-features",
    },
    "live_age_and_eligibility": {
        "title": "LIVE age, account standing and feature eligibility",
        "source": "https://www.tiktok.com/community-guidelines/en/accounts-features",
    },
    "gifting_integrity": {
        "title": "No pressure, deception or manipulation around Gifts",
        "source": "https://www.tiktok.com/community-guidelines/en/accounts-features",
    },
    "commercial_disclosure": {
        "title": "Commercial content and disclosure",
        "source": "https://www.tiktok.com/community-guidelines/en/accounts-features",
    },
    "child_and_vulnerable_person_safety": {
        "title": "Children and vulnerable-person safeguarding",
        "source": "https://www.tiktok.com/community-guidelines/",
    },
    "privacy_and_personal_data": {
        "title": "Privacy, personal data and location safety",
        "source": "/compliance/manifest.json",
    },
    "ip_and_originality": {
        "title": "Copyright, IP, authenticity and original content",
        "source": "/compliance/manifest.json",
    },
    "ai_and_professional_boundaries": {
        "title": "AI transparency and medical/legal/financial professional boundaries",
        "source": "/compliance/manifest.json",
    },
    "esp_professional_conduct": {
        "title": "ESP professional conduct and Creator Network role boundaries",
        "source": "/compliance",
    },
}

# The assessment is an education/evidence gate, not a substitute for TikTok or legal review.
# `correct` is kept server-side and removed from member-facing question responses.
ASSESSMENT_QUESTIONS = (
    {"id": "q01", "section": "LIVE responsibility", "prompt": "Who remains responsible for policy violations caused by a third-party translation, voice-to-text or on-screen comment tool during a LIVE?", "options": {"A": "The tool provider only", "B": "The LIVE creator/host", "C": "Nobody if it was automated"}, "correct": "B", "critical": True},
    {"id": "q02", "section": "LIVE responsibility", "prompt": "If a guest breaks TikTok rules in a multi-guest LIVE, what should the host do?", "options": {"A": "Actively manage/remove the violating guest and keep the room safe", "B": "Ignore it because guests are solely responsible", "C": "Encourage viewers to decide"}, "correct": "A", "critical": False},
    {"id": "q03", "section": "LIVE eligibility", "prompt": "What age should an ESP creator be before ESP treats them as eligible for TikTok LIVE hosting?", "options": {"A": "16+", "B": "17+", "C": "18+ and independently verified where required"}, "correct": "C", "critical": True},
    {"id": "q04", "section": "LIVE eligibility", "prompt": "Does passing this ESP assessment prove TikTok has enabled LIVE or monetization on the account?", "options": {"A": "Yes", "B": "No", "C": "Only for Pro members"}, "correct": "B", "critical": False},
    {"id": "q05", "section": "LIVE eligibility", "prompt": "What is the safe response if the creator's TikTok account standing or LIVE eligibility is uncertain?", "options": {"A": "Treat it as verified", "B": "Fail closed and obtain evidence/review", "C": "Create another LIVE account"}, "correct": "B", "critical": True},
    {"id": "q06", "section": "LIVE operations", "prompt": "Can a static image replace an absent host while presenting the stream as a normal hosted ESP LIVE?", "options": {"A": "Yes", "B": "Only if Gifts are disabled", "C": "No; use a compliant format and remain within current platform rules"}, "correct": "C", "critical": False},
    {"id": "q07", "section": "LIVE operations", "prompt": "Should a creator monitor comments, guests and connected tools throughout a LIVE?", "options": {"A": "Yes", "B": "No", "C": "Only during battles"}, "correct": "A", "critical": False},
    {"id": "q08", "section": "LIVE operations", "prompt": "If a LIVE contains a serious safety violation, what takes priority?", "options": {"A": "Keeping engagement high", "B": "Safety, stopping/escalating the harmful conduct and following platform procedures", "C": "Finishing the scheduled session"}, "correct": "B", "critical": True},

    {"id": "q09", "section": "Hate and harassment", "prompt": "Is attacking a person because of a protected characteristic acceptable ESP content?", "options": {"A": "No", "B": "Yes if framed as comedy", "C": "Yes in a battle"}, "correct": "A", "critical": False},
    {"id": "q10", "section": "Hate and harassment", "prompt": "Can creators use their LIVE to repeatedly humiliate or mobilize viewers against another creator?", "options": {"A": "Yes", "B": "No", "C": "Only if the other person is not in ESP"}, "correct": "B", "critical": False},
    {"id": "q11", "section": "Hate and harassment", "prompt": "What is ESP's baseline for bullying and harassment?", "options": {"A": "Allowed if no profanity is used", "B": "No bullying, harassment, hate or targeted abuse", "C": "Allowed between agencies"}, "correct": "B", "critical": True},
    {"id": "q12", "section": "Violence", "prompt": "How should a credible threat of violence be handled?", "options": {"A": "As entertainment", "B": "As a safety issue requiring prompt moderation/escalation and emergency help when appropriate", "C": "By asking viewers to vote"}, "correct": "B", "critical": True},
    {"id": "q13", "section": "Self-harm", "prompt": "If someone appears at immediate risk of self-harm, should Aura or an ESP creator pretend to be an emergency clinician?", "options": {"A": "Yes", "B": "No; encourage appropriate emergency/professional assistance and use platform safety tools", "C": "Only after midnight"}, "correct": "B", "critical": False},
    {"id": "q14", "section": "Sexual safety", "prompt": "Should sexually exploitative or non-consensual content be tolerated to preserve engagement?", "options": {"A": "No", "B": "Yes", "C": "Only in age-restricted rooms"}, "correct": "A", "critical": False},
    {"id": "q15", "section": "Child safety", "prompt": "What should happen when content presents a credible child-safety concern?", "options": {"A": "Ignore it unless a Gift is sent", "B": "Use safeguarding/reporting processes and escalate appropriately", "C": "Publicly investigate the child"}, "correct": "B", "critical": False},
    {"id": "q16", "section": "Dangerous conduct", "prompt": "Should creators stage unsafe fights, weapons misuse or dangerous acts for Gifts or views?", "options": {"A": "No", "B": "Yes with a disclaimer", "C": "Yes if the audience requests it"}, "correct": "A", "critical": True},

    {"id": "q17", "section": "Gifts", "prompt": "Is it acceptable to shame a viewer for not sending Gifts?", "options": {"A": "Yes", "B": "No", "C": "Only during battles"}, "correct": "B", "critical": False},
    {"id": "q18", "section": "Gifts", "prompt": "Can a creator use deceptive claims or coercive pressure to obtain Gifts?", "options": {"A": "No", "B": "Yes if the claim is temporary", "C": "Yes if a moderator approves"}, "correct": "A", "critical": True},
    {"id": "q19", "section": "Gifts", "prompt": "Should Gift interactions remain entertainment/community participation rather than coercion?", "options": {"A": "Yes", "B": "No", "C": "Only for new creators"}, "correct": "A", "critical": False},
    {"id": "q20", "section": "Commercial disclosure", "prompt": "When LIVE content promotes a brand or involves payment/perks, what should the creator do?", "options": {"A": "Hide the relationship", "B": "Use the applicable TikTok commercial/content disclosure tools and follow relevant rules", "C": "Mention it only after the LIVE"}, "correct": "B", "critical": False},
    {"id": "q21", "section": "Commercial disclosure", "prompt": "Does an ESP relationship remove a creator's duty to make required commercial disclosures?", "options": {"A": "Yes", "B": "No", "C": "Only outside the UK"}, "correct": "B", "critical": False},
    {"id": "q22", "section": "Gambling", "prompt": "Should creators introduce gambling-like mechanics or illegal/regulated wagering without verifying platform and local-law permissibility?", "options": {"A": "Yes", "B": "No", "C": "Only with Gifts"}, "correct": "B", "critical": False},

    {"id": "q23", "section": "Third-party tools", "prompt": "If an AI overlay reads a hateful viewer comment aloud, can the host say the AI is solely responsible?", "options": {"A": "Yes", "B": "No; the host remains responsible for enabled LIVE tools", "C": "Only if the AI is third-party"}, "correct": "B", "critical": True},
    {"id": "q24", "section": "Authenticity", "prompt": "Should a creator impersonate another person or present another person's stream as their own?", "options": {"A": "No", "B": "Yes", "C": "Only if no money is earned"}, "correct": "A", "critical": False},
    {"id": "q25", "section": "Copyright", "prompt": "Is owning access to a song/video the same as owning every right needed to rebroadcast it?", "options": {"A": "Yes", "B": "No", "C": "Always on TikTok"}, "correct": "B", "critical": False},
    {"id": "q26", "section": "Copyright", "prompt": "What should happen when rights to media are uncertain?", "options": {"A": "Assume permission", "B": "Use rights-cleared material or obtain appropriate permission/review", "C": "Remove the creator name"}, "correct": "B", "critical": False},
    {"id": "q27", "section": "AI transparency", "prompt": "Should materially AI-generated or manipulated content be labelled when TikTok or applicable law requires it?", "options": {"A": "Yes", "B": "No", "C": "Only music"}, "correct": "A", "critical": False},
    {"id": "q28", "section": "Authenticity", "prompt": "Can deceptive synthetic media be used to falsely claim a real person said or did something?", "options": {"A": "Yes", "B": "No", "C": "Only for recruitment"}, "correct": "B", "critical": False},

    {"id": "q29", "section": "Privacy", "prompt": "Should a creator reveal another person's private address, phone number or sensitive personal data without a valid basis?", "options": {"A": "No", "B": "Yes", "C": "Only to subscribers"}, "correct": "A", "critical": True},
    {"id": "q30", "section": "Privacy", "prompt": "When broadcasting from a real-world location, should creators consider location and bystander privacy?", "options": {"A": "Yes", "B": "No", "C": "Only abroad"}, "correct": "A", "critical": False},
    {"id": "q31", "section": "Professional boundaries", "prompt": "Should Aura or an ESP creator claim to be a licensed medical, legal or financial professional when they are not?", "options": {"A": "No", "B": "Yes if confident", "C": "Yes with a Gift goal"}, "correct": "A", "critical": True},
    {"id": "q32", "section": "Professional boundaries", "prompt": "What is the correct approach to high-risk medical/legal/financial questions outside your qualifications?", "options": {"A": "Give definitive professional instructions", "B": "Use appropriate disclaimers and direct the person to qualified professional assistance", "C": "Diagnose from chat"}, "correct": "B", "critical": False},
    {"id": "q33", "section": "Children", "prompt": "Should creators minimize unnecessary collection/exposure of children's personal information?", "options": {"A": "Yes", "B": "No", "C": "Only if parents complain"}, "correct": "A", "critical": False},
    {"id": "q34", "section": "Children", "prompt": "Can engagement goals override child-safety or safeguarding concerns?", "options": {"A": "No", "B": "Yes", "C": "Only for verified creators"}, "correct": "A", "critical": True},

    {"id": "q35", "section": "ESP conduct", "prompt": "Is an ESP Creator Network LIVE a professional business activity that should follow ESP and TikTok standards?", "options": {"A": "Yes", "B": "No", "C": "Only for Agents"}, "correct": "A", "critical": False},
    {"id": "q36", "section": "ESP conduct", "prompt": "Can an Agent or Creator use bullying, hate, harassment or retaliation as an ESP management technique?", "options": {"A": "No", "B": "Yes", "C": "Only privately"}, "correct": "A", "critical": True},
    {"id": "q37", "section": "ESP roles", "prompt": "Does buying a membership or credits grant ESP Creator/Agent authority?", "options": {"A": "Yes", "B": "No", "C": "Only Pro"}, "correct": "B", "critical": False},
    {"id": "q38", "section": "ESP roles", "prompt": "Does passing this training assessment automatically make someone an ESP Creator or Agent?", "options": {"A": "Yes", "B": "No", "C": "Only after 90%"}, "correct": "B", "critical": False},
    {"id": "q39", "section": "ESP governance", "prompt": "If a rule is unclear or jurisdiction-specific, what is the safe approach?", "options": {"A": "Invent a rule", "B": "Escalate for current policy/legal review and fail closed where necessary", "C": "Ignore it"}, "correct": "B", "critical": False},
    {"id": "q40", "section": "ESP governance", "prompt": "What is more important than views, Gifts, targets or commission when a credible safety/compliance issue exists?", "options": {"A": "Safety, law, platform rules and proper escalation", "B": "Finishing the target", "C": "Winning the battle"}, "correct": "A", "critical": True},
)

REQUIRED_OWNER_CHECKS_BY_ROLE = {
    "creator": ("age_18_plus", "single_live_account", "tiktok_account_good_standing", "creator_network_exclusivity"),
    "agent": ("age_18_plus", "tiktok_account_good_standing"),
    "both": ("age_18_plus", "single_live_account", "tiktok_account_good_standing", "creator_network_exclusivity"),
}

_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.-]{2,179}$")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path(db_path: str | Path | None = None) -> Path:
    return Path(db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3")


def _valid_ref(value: str) -> bool:
    value = str(value or "").strip()
    return bool(_REF_RE.fullmatch(value)) and "://" not in value and "/" not in value and "\\" not in value


class PolicyDecisionInput(BaseModel):
    policy_key: Literal[
        "community_safety", "live_host_responsibility", "live_age_and_eligibility", "gifting_integrity",
        "commercial_disclosure", "child_and_vulnerable_person_safety", "privacy_and_personal_data",
        "ip_and_originality", "ai_and_professional_boundaries", "esp_professional_conduct",
    ]
    decision: Literal["acknowledged", "declined", "withdrawn"]
    locale: str = Field(default="en-GB", max_length=32)


class AssessmentInput(BaseModel):
    answers: dict[str, str]


class OwnerVerificationInput(BaseModel):
    check_key: Literal["age_18_plus", "single_live_account", "tiktok_account_good_standing", "creator_network_exclusivity"]
    state: Literal["verified", "rejected"]
    evidence_ref: str = Field(min_length=3, max_length=180)


class EspComplianceStore:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = _db_path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS esp_compliance_policy_evidence (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    policy_key TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    locale TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_esp_compliance_policy_user
                    ON esp_compliance_policy_evidence(user_id, recorded_at DESC);

                CREATE TABLE IF NOT EXISTS esp_compliance_assessments (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    assessment_version TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    passed INTEGER NOT NULL,
                    critical_passed INTEGER NOT NULL,
                    incorrect_json TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_esp_compliance_assessment_user
                    ON esp_compliance_assessments(user_id, completed_at DESC);

                CREATE TABLE IF NOT EXISTS esp_compliance_verifications (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    check_key TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    state TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL,
                    verified_by TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_esp_compliance_verification_user
                    ON esp_compliance_verifications(user_id, recorded_at DESC);
                """
            )

    def esp_membership(self, user_id: str) -> dict | None:
        with self._connect() as con:
            table = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='esp_memberships'").fetchone()
            if not table:
                return None
            row = con.execute("SELECT * FROM esp_memberships WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def require_active_role(self, user_id: str) -> dict:
        membership = self.esp_membership(user_id)
        if not membership or membership.get("status") != "active" or membership.get("roles") not in REQUIRED_OWNER_CHECKS_BY_ROLE:
            raise PermissionError("Active ESP Creator/Agent access is required")
        return membership

    def record_policy_decision(self, user_id: str, policy_key: str, decision: str, locale: str) -> dict:
        self.require_active_role(user_id)
        with self._connect() as con:
            latest = con.execute(
                """SELECT * FROM esp_compliance_policy_evidence
                   WHERE user_id=? AND policy_key=? AND policy_version=?
                   ORDER BY recorded_at DESC,id DESC LIMIT 1""",
                (user_id, policy_key, COMPLIANCE_VERSION),
            ).fetchone()
            if latest and latest["decision"] == decision and latest["locale"] == locale:
                return dict(latest)
            row_id = uuid4().hex
            con.execute(
                """INSERT INTO esp_compliance_policy_evidence
                   (id,user_id,policy_key,policy_version,decision,locale,recorded_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (row_id, user_id, policy_key, COMPLIANCE_VERSION, decision, locale, _iso()),
            )
            row = con.execute("SELECT * FROM esp_compliance_policy_evidence WHERE id=?", (row_id,)).fetchone()
        return dict(row)

    def grade(self, user_id: str, answers: dict[str, str]) -> dict:
        self.require_active_role(user_id)
        expected_ids = {q["id"] for q in ASSESSMENT_QUESTIONS}
        supplied_ids = set(answers)
        if supplied_ids != expected_ids:
            missing = sorted(expected_ids - supplied_ids)
            unknown = sorted(supplied_ids - expected_ids)
            raise ValueError(f"Assessment requires exactly 40 known answers; missing={missing}, unknown={unknown}")
        incorrect: list[str] = []
        critical_failed: list[str] = []
        for question in ASSESSMENT_QUESTIONS:
            choice = str(answers.get(question["id"]) or "").upper()
            if choice not in question["options"]:
                raise ValueError(f"Invalid answer choice for {question['id']}")
            if choice != question["correct"]:
                incorrect.append(question["id"])
                if question["critical"]:
                    critical_failed.append(question["id"])
        correct_count = len(ASSESSMENT_QUESTIONS) - len(incorrect)
        score = int(round(correct_count * 100 / len(ASSESSMENT_QUESTIONS)))
        critical_passed = not critical_failed
        passed = score >= PASS_PERCENT and critical_passed
        row_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_compliance_assessments
                   (id,user_id,assessment_version,score,passed,critical_passed,incorrect_json,completed_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (row_id, user_id, COMPLIANCE_VERSION, score, int(passed), int(critical_passed),
                 json.dumps(incorrect, separators=(",", ":")), _iso()),
            )
            row = con.execute("SELECT * FROM esp_compliance_assessments WHERE id=?", (row_id,)).fetchone()
        result = dict(row)
        result["passed"] = bool(result["passed"])
        result["critical_passed"] = bool(result["critical_passed"])
        result["incorrect_question_ids"] = incorrect
        result["critical_failed_question_ids"] = critical_failed
        result.pop("incorrect_json", None)
        return result

    def record_owner_verification(self, user_id: str, check_key: str, state: str, evidence_ref: str) -> dict:
        membership = self.require_active_role(user_id)
        if check_key not in REQUIRED_OWNER_CHECKS_BY_ROLE[membership["roles"]]:
            raise ValueError("Verification is not required for this ESP role")
        if not _valid_ref(evidence_ref):
            raise ValueError("Verification evidence must be an opaque reference, not a URL or filesystem path")
        row_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_compliance_verifications
                   (id,user_id,check_key,policy_version,state,evidence_ref,verified_by,recorded_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (row_id, user_id, check_key, COMPLIANCE_VERSION, state, evidence_ref, "owner_session", _iso()),
            )
            row = con.execute("SELECT * FROM esp_compliance_verifications WHERE id=?", (row_id,)).fetchone()
        return dict(row)

    def snapshot(self, user_id: str, *, include_owner_evidence: bool = False) -> dict:
        membership = self.require_active_role(user_id)
        with self._connect() as con:
            policy_rows = con.execute(
                """SELECT * FROM esp_compliance_policy_evidence
                   WHERE user_id=? AND policy_version=? ORDER BY recorded_at DESC,id DESC""",
                (user_id, COMPLIANCE_VERSION),
            ).fetchall()
            assessment = con.execute(
                """SELECT * FROM esp_compliance_assessments
                   WHERE user_id=? AND assessment_version=? ORDER BY completed_at DESC,id DESC LIMIT 1""",
                (user_id, COMPLIANCE_VERSION),
            ).fetchone()
            verification_rows = con.execute(
                """SELECT * FROM esp_compliance_verifications
                   WHERE user_id=? AND policy_version=? ORDER BY recorded_at DESC,id DESC""",
                (user_id, COMPLIANCE_VERSION),
            ).fetchall()

        latest_policy: dict[str, dict] = {}
        for row in policy_rows:
            item = dict(row)
            latest_policy.setdefault(item["policy_key"], item)
        latest_verification: dict[str, dict] = {}
        for row in verification_rows:
            item = dict(row)
            latest_verification.setdefault(item["check_key"], item)

        policy_ok = all(latest_policy.get(key, {}).get("decision") == "acknowledged" for key in POLICY_PACK)
        assessment_item = dict(assessment) if assessment else None
        assessment_ok = bool(assessment_item and assessment_item["passed"] and assessment_item["critical_passed"])
        required_checks = REQUIRED_OWNER_CHECKS_BY_ROLE[membership["roles"]]
        verification_ok = all(latest_verification.get(key, {}).get("state") == "verified" for key in required_checks)
        if not include_owner_evidence:
            latest_verification = {
                key: {"check_key": value["check_key"], "state": value["state"], "recorded_at": value["recorded_at"]}
                for key, value in latest_verification.items()
            }

        return {
            "compliance_version": COMPLIANCE_VERSION,
            "esp_role": membership["roles"],
            "policy_acknowledgements_complete": policy_ok,
            "assessment_passed": assessment_ok,
            "assessment": None if not assessment_item else {
                "score": int(assessment_item["score"]),
                "passed": bool(assessment_item["passed"]),
                "critical_passed": bool(assessment_item["critical_passed"]),
                "completed_at": assessment_item["completed_at"],
            },
            "required_owner_verifications": list(required_checks),
            "owner_verifications": latest_verification,
            "owner_verifications_complete": verification_ok,
            "ready_for_esp_compliance_review": bool(policy_ok and assessment_ok and verification_ok),
            "tiktok_live_eligibility_verified_by_this_system": False,
            "legal_compliance_certified_by_this_system": False,
            "grants_esp_role_or_permission": False,
            "alters_billing_or_membership": False,
        }


def _member_user_id(request: Request) -> str:
    member = getattr(request.state, "member", None)
    user_id = getattr(member, "user_id", None)
    if not user_id:
        raise HTTPException(401, "Authenticated member session required")
    return str(user_id)


def _store() -> EspComplianceStore:
    return EspComplianceStore()


@member_router.get("/training")
def compliance_training(request: Request):
    user_id = _member_user_id(request)
    try:
        membership = _store().require_active_role(user_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    questions = [
        {"id": q["id"], "section": q["section"], "prompt": q["prompt"], "options": q["options"], "critical": q["critical"]}
        for q in ASSESSMENT_QUESTIONS
    ]
    return {
        "compliance_version": COMPLIANCE_VERSION,
        "esp_role": membership["roles"],
        "pass_percent": PASS_PERCENT,
        "question_count": len(questions),
        "questions": questions,
        "policy_pack": POLICY_PACK,
        "notice": "Training evidence supports ESP governance only. TikTok and applicable law remain authoritative and may change.",
        "grants_esp_role_or_permission": False,
    }


@member_router.post("/policy-decisions")
def policy_decision(request: Request, payload: PolicyDecisionInput):
    user_id = _member_user_id(request)
    try:
        row = _store().record_policy_decision(user_id, payload.policy_key, payload.decision, payload.locale)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"evidence": row, "grants_esp_role_or_permission": False, "legal_certification": False}


@member_router.post("/assessment")
def submit_assessment(request: Request, payload: AssessmentInput):
    user_id = _member_user_id(request)
    try:
        result = _store().grade(user_id, payload.answers)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "assessment": result,
        "grants_esp_role_or_permission": False,
        "tiktok_live_eligibility_verified": False,
    }


@member_router.get("/readiness")
def member_readiness(request: Request):
    user_id = _member_user_id(request)
    try:
        return _store().snapshot(user_id, include_owner_evidence=False)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@owner_router.get("/{user_id}", include_in_schema=False)
def owner_readiness(user_id: str, request: Request):
    if not owner_authorized(request):
        raise HTTPException(403, "Owner authorization required")
    try:
        return _store().snapshot(user_id, include_owner_evidence=True)
    except PermissionError as exc:
        raise HTTPException(404, str(exc)) from exc


@owner_router.post("/{user_id}/verify", include_in_schema=False)
def owner_verify(user_id: str, request: Request, payload: OwnerVerificationInput):
    if not owner_authorized(request):
        raise HTTPException(403, "Owner authorization required")
    try:
        row = _store().record_owner_verification(user_id, payload.check_key, payload.state, payload.evidence_ref)
    except PermissionError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "verification": row,
        "automatic_role_change": False,
        "automatic_tiktok_action": False,
        "grants_esp_role_or_permission": False,
    }


__all__ = [
    "ASSESSMENT_QUESTIONS", "COMPLIANCE_VERSION", "PASS_PERCENT", "POLICY_PACK",
    "REQUIRED_OWNER_CHECKS_BY_ROLE", "EspComplianceStore", "member_router", "owner_router",
]
