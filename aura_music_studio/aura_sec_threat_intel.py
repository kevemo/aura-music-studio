from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class IntelSource(str, Enum):
    NVD = "nvd"
    CISA_KEV = "cisa_kev"
    FIRST_EPSS = "first_epss"
    VENDOR_ADVISORY = "vendor_advisory"
    URL_REPUTATION = "url_reputation"
    INTERNAL_DETECTION = "internal_detection"


DEFAULT_MAX_AGE: dict[IntelSource, timedelta] = {
    IntelSource.NVD: timedelta(hours=4),
    IntelSource.CISA_KEV: timedelta(hours=6),
    IntelSource.FIRST_EPSS: timedelta(hours=36),
    IntelSource.VENDOR_ADVISORY: timedelta(hours=24),
    IntelSource.URL_REPUTATION: timedelta(hours=1),
    IntelSource.INTERNAL_DETECTION: timedelta(minutes=15),
}


class ThreatIntelEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: IntelSource
    source_record_id: str = Field(min_length=1, max_length=300)
    retrieved_at: datetime
    published_at: datetime | None = None
    model_or_schema_version: str = Field(default="unknown", min_length=1, max_length=100)
    confidence: float = Field(ge=0.0, le=1.0)
    licence_class: str = Field(min_length=1, max_length=160)
    redistribution_allowed: bool = False
    raw_payload_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    evidence_url: str | None = Field(default=None, max_length=1000)

    @field_validator("retrieved_at", "published_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("threat intelligence timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def chronology(self):
        if self.published_at and self.published_at > self.retrieved_at + timedelta(minutes=5):
            raise ValueError("published_at cannot materially post-date retrieval")
        return self


def payload_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def evaluate_intel_freshness(
    evidence: ThreatIntelEvidence,
    *,
    now: datetime | None = None,
    max_age: timedelta | None = None,
) -> dict:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    permitted_age = max_age or DEFAULT_MAX_AGE[evidence.source]
    age = current - evidence.retrieved_at
    future_skew = evidence.retrieved_at - current

    if future_skew > timedelta(minutes=5):
        return {
            "usable": False,
            "state": "invalid_future_timestamp",
            "age_seconds": 0,
            "max_age_seconds": int(permitted_age.total_seconds()),
        }

    age_seconds = max(0, int(age.total_seconds()))
    if age > permitted_age:
        return {
            "usable": False,
            "state": "stale",
            "age_seconds": age_seconds,
            "max_age_seconds": int(permitted_age.total_seconds()),
        }

    return {
        "usable": True,
        "state": "fresh",
        "age_seconds": age_seconds,
        "max_age_seconds": int(permitted_age.total_seconds()),
    }


def evidence_summary(evidence: ThreatIntelEvidence, *, now: datetime | None = None) -> dict:
    freshness = evaluate_intel_freshness(evidence, now=now)
    return {
        "source": evidence.source.value,
        "source_record_id": evidence.source_record_id,
        "model_or_schema_version": evidence.model_or_schema_version,
        "confidence": evidence.confidence,
        "retrieved_at": evidence.retrieved_at.isoformat(),
        "freshness": freshness,
        "licence_class": evidence.licence_class,
        "redistribution_allowed": evidence.redistribution_allowed,
        "evidence_url": evidence.evidence_url,
        "raw_payload_sha256": evidence.raw_payload_sha256.lower(),
    }


__all__ = [
    "DEFAULT_MAX_AGE",
    "IntelSource",
    "ThreatIntelEvidence",
    "evaluate_intel_freshness",
    "evidence_summary",
    "payload_digest",
]
