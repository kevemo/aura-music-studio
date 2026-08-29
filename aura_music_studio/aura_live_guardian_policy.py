from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

LanguageTolerance = Literal["strict", "balanced", "relaxed"]
SpamSensitivity = Literal["low", "medium", "high"]

_ALLOWED_CATEGORIES = {
    "harassment", "hate", "sexual", "threat", "doxxing", "scam", "spam",
    "impersonation", "self_harm_concern", "grooming_concern",
}
_MANDATORY_HIGH_RISK = {"threat", "doxxing", "grooming_concern"}


@dataclass(frozen=True)
class AuraLiveGuardianPolicy:
    user_id: str
    blocked_phrases: tuple[str, ...]
    language_tolerance: LanguageTolerance
    spam_sensitivity: SpamSensitivity
    enabled_categories: frozenset[str]
    updated_at: datetime
    updated_by: str


def _identity(value: str, field: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 160 or "\n" in clean or "\r" in clean:
        raise ValueError(f"{field} is invalid")
    return clean


def _phrases(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        value = " ".join(str(raw).strip().split())
        if not value:
            continue
        if len(value) > 120:
            raise ValueError("blocked phrase is too long")
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) > 100:
            raise ValueError("too many blocked phrases")
    return tuple(result)


def _categories(values: set[str] | frozenset[str] | list[str] | tuple[str, ...] | None) -> frozenset[str]:
    # None means use the safe default set on first creation. An explicitly empty collection means
    # the creator disabled every optional category, so only mandatory high-risk protections remain.
    source = _ALLOWED_CATEGORIES if values is None else values
    selected = {str(value).strip() for value in source}
    unknown = selected - _ALLOWED_CATEGORIES
    if unknown:
        raise ValueError("unknown moderation category")
    selected.update(_MANDATORY_HIGH_RISK)
    return frozenset(selected)


class AuraLiveGuardianPolicyStore:
    def __init__(self, database: str | Path):
        self.database = str(database)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS aura_live_guardian_policies (
                    user_id TEXT PRIMARY KEY,
                    blocked_phrases_json TEXT NOT NULL,
                    language_tolerance TEXT NOT NULL,
                    spam_sensitivity TEXT NOT NULL,
                    enabled_categories_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                )
            """)

    def save(self, *, user_id: str, blocked_phrases: list[str] | tuple[str, ...] | None,
             language_tolerance: LanguageTolerance = "balanced", spam_sensitivity: SpamSensitivity = "medium",
             enabled_categories: set[str] | frozenset[str] | list[str] | tuple[str, ...] | None = None,
             actor: str, now: datetime | None = None) -> AuraLiveGuardianPolicy:
        user_id = _identity(user_id, "user_id")
        actor = _identity(actor, "actor")
        if language_tolerance not in {"strict", "balanced", "relaxed"}:
            raise ValueError("language tolerance is invalid")
        if spam_sensitivity not in {"low", "medium", "high"}:
            raise ValueError("spam sensitivity is invalid")
        phrases = _phrases(blocked_phrases)
        categories = _categories(enabled_categories)
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO aura_live_guardian_policies (
                    user_id, blocked_phrases_json, language_tolerance, spam_sensitivity,
                    enabled_categories_json, updated_at, updated_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    blocked_phrases_json=excluded.blocked_phrases_json,
                    language_tolerance=excluded.language_tolerance,
                    spam_sensitivity=excluded.spam_sensitivity,
                    enabled_categories_json=excluded.enabled_categories_json,
                    updated_at=excluded.updated_at,
                    updated_by=excluded.updated_by
            """, (user_id, json.dumps(phrases, ensure_ascii=False), language_tolerance, spam_sensitivity,
                    json.dumps(sorted(categories)), timestamp.isoformat(), actor))
        return AuraLiveGuardianPolicy(user_id, phrases, language_tolerance, spam_sensitivity, categories, timestamp, actor)

    def get(self, user_id: str) -> AuraLiveGuardianPolicy | None:
        user_id = _identity(user_id, "user_id")
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM aura_live_guardian_policies WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            return None
        return AuraLiveGuardianPolicy(
            user_id=user_id,
            blocked_phrases=tuple(json.loads(str(row["blocked_phrases_json"]))),
            language_tolerance=str(row["language_tolerance"]),  # type: ignore[arg-type]
            spam_sensitivity=str(row["spam_sensitivity"]),  # type: ignore[arg-type]
            enabled_categories=frozenset(json.loads(str(row["enabled_categories_json"]))),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            updated_by=str(row["updated_by"]),
        )

    def phrase_matches(self, user_id: str, message: str) -> tuple[str, ...]:
        policy = self.get(user_id)
        if policy is None:
            return ()
        haystack = " ".join(str(message or "").casefold().split())
        return tuple(phrase for phrase in policy.blocked_phrases if phrase.casefold() in haystack)


__all__ = ["AuraLiveGuardianPolicy", "AuraLiveGuardianPolicyStore"]
