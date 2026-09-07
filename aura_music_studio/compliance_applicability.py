from __future__ import annotations

import os
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from .owner_auth import owner_authorized

router = APIRouter(prefix="/compliance", tags=["Compliance Applicability"])
owner_router = APIRouter(prefix="/owner/compliance", tags=["Owner Compliance Applicability"])

REGISTRY_VERSION = "2026-08-28.1"
_OPAQUE_REF = re.compile(r"^[A-Za-z0-9._:-]{1,180}$")
_TOKEN = re.compile(r"^[a-z0-9_.-]{1,64}$")
_EU_COUNTRIES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT",
    "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
}

# Curated official-source evidence only. This is deliberately not represented as exhaustive legal coverage.
_SEED_POLICIES = (
    {
        "policy_id": "tiktok-community-guidelines",
        "jurisdiction": "GLOBAL",
        "title": "TikTok Community Guidelines and LIVE rules",
        "source_url": "https://www.tiktok.com/community-guidelines",
        "effective_from": "2025-09-13",
        "effective_to": "",
        "reviewed_at": "2026-08-28",
        "next_review_at": "2026-09-28",
        "status": "active",
        "features": ("live", "social_publication", "commercial_content"),
        "roles": ("creator", "agent", "member"),
        "min_age": 18,
        "max_age": None,
        "confidence": "high",
        "source_class": "official_platform",
    },
    {
        "policy_id": "uk-data-protection-baseline",
        "jurisdiction": "GB",
        "title": "UK GDPR and Data Protection Act guidance baseline",
        "source_url": "https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/",
        "effective_from": "2021-01-01",
        "effective_to": "",
        "reviewed_at": "2026-08-28",
        "next_review_at": "2026-10-28",
        "status": "active",
        "features": ("privacy", "account", "uploads", "analytics", "ai_generated_content"),
        "roles": ("*",),
        "min_age": None,
        "max_age": None,
        "confidence": "high",
        "source_class": "official_regulator",
    },
    {
        "policy_id": "eu-ai-act-article-50",
        "jurisdiction": "EU",
        "title": "EU AI Act Article 50 transparency obligations",
        "source_url": "https://eur-lex.europa.eu/eli/reg/2024/1689/oj",
        "effective_from": "2026-08-02",
        "effective_to": "",
        "reviewed_at": "2026-08-28",
        "next_review_at": "2026-10-28",
        "status": "active",
        "features": ("ai_generated_content", "image_generation", "video_generation", "audio_generation", "publication"),
        "roles": ("*",),
        "min_age": None,
        "max_age": None,
        "confidence": "high",
        "source_class": "official_government",
    },
    {
        "policy_id": "us-ftc-coppa",
        "jurisdiction": "US",
        "title": "FTC Children's Online Privacy Protection Rule guidance",
        "source_url": "https://www.ftc.gov/business-guidance/privacy-security/childrens-privacy",
        "effective_from": "2000-04-21",
        "effective_to": "",
        "reviewed_at": "2026-08-28",
        "next_review_at": "2026-10-28",
        "status": "active",
        "features": ("privacy_children", "account", "uploads"),
        "roles": ("*",),
        "min_age": None,
        "max_age": 12,
        "confidence": "high",
        "source_class": "official_regulator",
    },
    {
        "policy_id": "california-ccpa",
        "jurisdiction": "US-CA",
        "title": "California Consumer Privacy Act rights",
        "source_url": "https://privacy.ca.gov/california-privacy-rights/rights-under-the-california-consumer-privacy-act/",
        "effective_from": "2020-01-01",
        "effective_to": "",
        "reviewed_at": "2026-08-28",
        "next_review_at": "2026-10-28",
        "status": "active",
        "features": ("privacy", "account", "analytics"),
        "roles": ("*",),
        "min_age": None,
        "max_age": None,
        "confidence": "high",
        "source_class": "official_government",
    },
    {
        "policy_id": "w3c-wcag22",
        "jurisdiction": "GLOBAL",
        "title": "Web Content Accessibility Guidelines 2.2",
        "source_url": "https://www.w3.org/TR/WCAG22/",
        "effective_from": "2023-10-05",
        "effective_to": "",
        "reviewed_at": "2026-08-28",
        "next_review_at": "2026-11-28",
        "status": "active",
        "features": ("accessibility", "ui"),
        "roles": ("*",),
        "min_age": None,
        "max_age": None,
        "confidence": "high",
        "source_class": "standards_body",
    },
)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date(value: str | date | None, *, default: date | None = None) -> date | None:
    if value is None or value == "":
        return default
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _token(value: str, field_name: str) -> str:
    value = str(value or "").strip().lower()
    if not _TOKEN.fullmatch(value):
        raise ValueError(f"{field_name} must contain only lowercase letters, numbers, dot, dash or underscore")
    return value


def _jurisdiction(value: str) -> str:
    raw = str(value or "").strip().upper()
    if not re.fullmatch(r"(?:GLOBAL|EU|[A-Z]{2}(?:-[A-Z0-9]{2,8})?)", raw):
        raise ValueError("jurisdiction must be GLOBAL, EU, a two-letter country, or country-region code")
    return raw


def _opaque(value: str) -> str:
    value = str(value or "").strip()
    if not _OPAQUE_REF.fullmatch(value):
        raise ValueError("evidence_reference must be an opaque identifier, not a URL/path/free-form payload")
    return value


def _require_owner(request: Request) -> None:
    if not owner_authorized(request):
        raise HTTPException(403, "Owner authorization required")


class PolicyReviewInput(BaseModel):
    policy_id: str = Field(min_length=1, max_length=64)
    jurisdiction: str = Field(min_length=2, max_length=16)
    title: str = Field(min_length=3, max_length=220)
    source_url: str = Field(min_length=8, max_length=500)
    effective_from: date
    effective_to: date | None = None
    reviewed_at: date
    next_review_at: date
    status: Literal["active", "upcoming", "retired"] = "active"
    features: list[str] = Field(min_length=1, max_length=32)
    roles: list[str] = Field(default_factory=lambda: ["*"], min_length=1, max_length=16)
    min_age: int | None = Field(default=None, ge=0, le=130)
    max_age: int | None = Field(default=None, ge=0, le=130)
    confidence: Literal["high", "medium", "low"] = "high"
    source_class: Literal["official_government", "official_regulator", "official_platform", "standards_body"]
    evidence_reference: str = Field(min_length=1, max_length=180)
    note: str = Field(default="", max_length=1500)

    @field_validator("policy_id")
    @classmethod
    def validate_policy_id(cls, value: str) -> str:
        return _token(value, "policy_id")

    @field_validator("jurisdiction")
    @classmethod
    def validate_jurisdiction(cls, value: str) -> str:
        return _jurisdiction(value)

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("https://"):
            raise ValueError("source_url must use HTTPS and point to the official source")
        return value

    @field_validator("features", "roles")
    @classmethod
    def validate_tokens(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            if value == "*":
                cleaned.append(value)
            else:
                cleaned.append(_token(value, "feature/role"))
        return sorted(set(cleaned))

    @field_validator("evidence_reference")
    @classmethod
    def validate_evidence(cls, value: str) -> str:
        return _opaque(value)


class PolicyRegistryStore:
    """Append-only reviewed policy evidence; never a legal-certification database."""

    def __init__(self, db_path: str | Path | None = None):
        configured = db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3"
        self.db_path = Path(configured)
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
                CREATE TABLE IF NOT EXISTS compliance_policy_reviews (
                    id TEXT PRIMARY KEY,
                    policy_id TEXT NOT NULL,
                    jurisdiction TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    effective_from TEXT NOT NULL,
                    effective_to TEXT NOT NULL DEFAULT '',
                    reviewed_at TEXT NOT NULL,
                    next_review_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    features TEXT NOT NULL,
                    roles TEXT NOT NULL,
                    min_age INTEGER,
                    max_age INTEGER,
                    confidence TEXT NOT NULL,
                    source_class TEXT NOT NULL,
                    evidence_reference TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_compliance_policy_latest
                    ON compliance_policy_reviews(policy_id, jurisdiction, created_at DESC);
                """
            )

    def append_review(self, body: PolicyReviewInput) -> dict:
        if body.effective_to and body.effective_to < body.effective_from:
            raise ValueError("effective_to cannot be before effective_from")
        if body.next_review_at < body.reviewed_at:
            raise ValueError("next_review_at cannot be before reviewed_at")
        if body.min_age is not None and body.max_age is not None and body.max_age < body.min_age:
            raise ValueError("max_age cannot be below min_age")
        row_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO compliance_policy_reviews
                (id,policy_id,jurisdiction,title,source_url,effective_from,effective_to,reviewed_at,next_review_at,
                 status,features,roles,min_age,max_age,confidence,source_class,evidence_reference,note,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row_id, body.policy_id, body.jurisdiction, body.title, body.source_url,
                    body.effective_from.isoformat(), body.effective_to.isoformat() if body.effective_to else "",
                    body.reviewed_at.isoformat(), body.next_review_at.isoformat(), body.status,
                    ",".join(body.features), ",".join(body.roles), body.min_age, body.max_age,
                    body.confidence, body.source_class, body.evidence_reference, body.note.strip(), _iso(),
                ),
            )
        return self.get_review(row_id)

    def get_review(self, row_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM compliance_policy_reviews WHERE id=?", (row_id,)).fetchone()
        if not row:
            raise KeyError(row_id)
        return self._decode(dict(row))

    def latest_reviews(self) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT r.* FROM compliance_policy_reviews r
                   JOIN (SELECT policy_id,jurisdiction,MAX(created_at) created_at
                         FROM compliance_policy_reviews GROUP BY policy_id,jurisdiction) latest
                   ON r.policy_id=latest.policy_id AND r.jurisdiction=latest.jurisdiction
                      AND r.created_at=latest.created_at"""
            ).fetchall()
        return [self._decode(dict(row)) for row in rows]

    @staticmethod
    def _decode(row: dict) -> dict:
        row["features"] = tuple(item for item in row["features"].split(",") if item)
        row["roles"] = tuple(item for item in row["roles"].split(",") if item)
        return row


store = PolicyRegistryStore()


def _registry_rows(registry_store: PolicyRegistryStore) -> list[dict]:
    rows = [dict(item) for item in _SEED_POLICIES]
    latest = {(item["policy_id"], item["jurisdiction"]): item for item in registry_store.latest_reviews()}
    merged: dict[tuple[str, str], dict] = {(item["policy_id"], item["jurisdiction"]): item for item in rows}
    merged.update(latest)
    return list(merged.values())


def _jurisdictions(country: str, region: str | None) -> set[str]:
    country = country.upper()
    values = {"GLOBAL", country}
    if country in _EU_COUNTRIES:
        values.add("EU")
    if region:
        values.add(f"{country}-{region.upper()}")
    return values


def evaluate_applicability(
    *,
    country: str,
    feature: str,
    user_role: str = "member",
    age: int | None = None,
    region: str | None = None,
    as_of: date | None = None,
    registry_store: PolicyRegistryStore | None = None,
) -> dict:
    country = country.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise ValueError("country must be an ISO-style two-letter country code")
    if region and not re.fullmatch(r"[A-Za-z0-9]{2,8}", region.strip()):
        raise ValueError("region must be a short state/province/territory code")
    feature = _token(feature, "feature")
    user_role = _token(user_role, "user_role")
    if age is not None and not 0 <= age <= 130:
        raise ValueError("age must be between 0 and 130")
    target_date = as_of or datetime.now(timezone.utc).date()
    jurisdictions = _jurisdictions(country, region)
    rows = _registry_rows(registry_store or store)

    applicable = []
    upcoming = []
    stale = []
    age_findings = []
    local_evidence = False
    for row in rows:
        if row["jurisdiction"] not in jurisdictions:
            continue
        if feature not in row["features"]:
            continue
        if "*" not in row["roles"] and user_role not in row["roles"]:
            continue
        if row["jurisdiction"] != "GLOBAL":
            local_evidence = True
        effective_from = _date(row["effective_from"])
        effective_to = _date(row.get("effective_to"))
        next_review = _date(row["next_review_at"])
        status = row["status"]
        public = {
            key: row.get(key) for key in (
                "policy_id", "jurisdiction", "title", "source_url", "effective_from", "effective_to",
                "reviewed_at", "next_review_at", "status", "features", "roles", "min_age", "max_age",
                "confidence", "source_class",
            )
        }
        public["source_provenance"] = "official_source_review_record"
        if status == "retired" or (effective_to and target_date > effective_to):
            continue
        if status == "upcoming" or (effective_from and target_date < effective_from):
            upcoming.append(public)
            continue
        applicable.append(public)
        if next_review and target_date > next_review:
            stale.append(row["policy_id"])
        if age is not None:
            if row.get("min_age") is not None and age < int(row["min_age"]):
                age_findings.append({"policy_id": row["policy_id"], "finding": "age_below_policy_minimum"})
            if row.get("max_age") is not None and age <= int(row["max_age"]):
                age_findings.append({"policy_id": row["policy_id"], "finding": "child_age_scope_triggered"})

    reasons = []
    if not applicable:
        reasons.append("no_active_policy_evidence_for_query")
    if stale:
        reasons.append("policy_review_evidence_stale")
    if not local_evidence:
        reasons.append("no_jurisdiction_specific_evidence_for_feature")
    if age_findings:
        reasons.append("age_specific_requirements_require_review")
    requires_review = bool(reasons)
    return {
        "registry_version": REGISTRY_VERSION,
        "country": country,
        "region": region.upper() if region else None,
        "feature": feature,
        "user_role": user_role,
        "age": age,
        "as_of": target_date.isoformat(),
        "decision": "requires_qualified_legal_review" if requires_review else "policy_evidence_available",
        "reasons": reasons,
        "applicable_policies": applicable,
        "upcoming_policies": upcoming,
        "stale_policy_ids": sorted(set(stale)),
        "age_findings": age_findings,
        "coverage_complete": False,
        "legal_advice": False,
        "legal_certification": False,
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
        "notice": "This is jurisdiction-aware policy evidence and decision support, not legal advice or certification. Unknown, stale, incomplete or high-risk coverage must be escalated for qualified legal review.",
    }


@router.get("/applicability")
def compliance_applicability(
    country: str = Query(min_length=2, max_length=2),
    feature: str = Query(min_length=1, max_length=64),
    user_role: str = Query(default="member", min_length=1, max_length=64),
    age: int | None = Query(default=None, ge=0, le=130),
    region: str | None = Query(default=None, min_length=2, max_length=8),
    as_of: date | None = Query(default=None),
):
    try:
        return evaluate_applicability(
            country=country, feature=feature, user_role=user_role, age=age, region=region, as_of=as_of
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@owner_router.post("/policy-registry/reviews", include_in_schema=False)
def owner_record_policy_review(body: PolicyReviewInput, request: Request):
    _require_owner(request)
    try:
        result = store.append_review(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "review": result,
        "append_only": True,
        "legal_certification": False,
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
    }


__all__ = [
    "router", "owner_router", "PolicyRegistryStore", "PolicyReviewInput", "evaluate_applicability", "REGISTRY_VERSION"
]
