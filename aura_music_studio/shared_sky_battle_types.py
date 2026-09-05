from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal
from uuid import uuid4

MAX_PARTICIPANTS = 8
ACTIVE_PARTICIPANT_STATES = {"lobby", "connected", "ready", "live", "reconnecting"}
BATTLE_MODES = {"1v1", "2v2", "3v3", "4v4", "multi_team", "free_for_all", "host_challengers", "collaborative"}
PARTICIPANT_ROLES = {"host", "cohost", "guest", "participant", "moderator", "producer", "technical_director", "observer"}
STAGE_STATES = {"backstage", "stage"}
AUTHORITY_ROLES = {"host", "producer", "moderator", "technical_director"}


class BattleDomainError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class CommittedGiftEvent:
    event_id: str
    transaction_id: str
    recipient_user_id: str
    gift_definition_id: str
    occurred_at: str
    risk_state: str = "allow"
    correlation_id: str = ""


@dataclass(frozen=True)
class ReversedGiftEvent:
    event_id: str
    reverses_event_id: str
    occurred_at: str
    reason: str = "source_reversal"
    correlation_id: str = ""


@dataclass(frozen=True)
class EngagementScoreEvent:
    event_id: str
    event_type: Literal["like_batch", "reaction_batch"]
    recipient_user_id: str
    occurred_at: str
    count: int = 1
    risk_state: str = "allow"
    correlation_id: str = ""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def parse_time(value: str | None) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _bounded(text: str | None, limit: int) -> str:
    return (text or "").strip()[:limit]
