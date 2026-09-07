from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from .aura_live_moderator import ModerationAction, ModerationDecision

ReviewKind = Literal["action_confirmation", "safety_escalation"]
ReviewStatus = Literal["pending", "confirmed", "dismissed", "acknowledged"]

_ACTIONABLE = {
    ModerationAction.WARN,
    ModerationAction.RECOMMEND_MUTE,
    ModerationAction.RECOMMEND_BLOCK,
}


@dataclass(frozen=True)
class AuraLiveGuardianReviewItem:
    review_id: str
    user_id: str
    audit_event_id: str
    review_kind: ReviewKind
    signal_category: str
    signal_severity: int
    confidence_bucket: str
    recommended_action: str
    provider_write_permitted_at_decision: bool
    status: ReviewStatus
    created_at: datetime
    expires_at: datetime | None
    resolved_at: datetime | None
    resolved_by: str | None

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        moment = (now or datetime.now(UTC)).astimezone(UTC)
        return moment >= self.expires_at.astimezone(UTC)


class AuraLiveGuardianReviewStore:
    """Data-minimized, tenant-scoped human review queue for Aura LIVE Guardian.

    Review confirmation records human intent only. It never performs provider writes and never
    stores raw LIVE messages, blocked phrase text, classifier evidence strings or provider secrets.
    """

    def __init__(self, database: str | Path):
        self.database = str(database)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS aura_live_guardian_reviews (
                    review_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    audit_event_id TEXT NOT NULL UNIQUE,
                    review_kind TEXT NOT NULL,
                    signal_category TEXT NOT NULL,
                    signal_severity INTEGER NOT NULL,
                    confidence_bucket TEXT NOT NULL,
                    recommended_action TEXT NOT NULL,
                    provider_write_permitted_at_decision INTEGER NOT NULL CHECK (provider_write_permitted_at_decision IN (0, 1)),
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    resolved_at TEXT,
                    resolved_by TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_aura_live_guardian_reviews_user_status_created
                    ON aura_live_guardian_reviews(user_id, status, created_at DESC);
                """
            )

    @staticmethod
    def _identity(value: str, field: str) -> str:
        clean = str(value or "").strip()
        if not clean or len(clean) > 160 or "\n" in clean or "\r" in clean:
            raise ValueError(f"{field} is invalid")
        return clean

    @staticmethod
    def _timestamp(value: datetime | None = None) -> datetime:
        moment = value or datetime.now(UTC)
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return moment.astimezone(UTC)

    def enqueue(
        self,
        *,
        user_id: str,
        audit_event_id: str,
        signal_category: str,
        signal_severity: int,
        confidence_bucket: str,
        decision: ModerationDecision,
        now: datetime | None = None,
        action_ttl_seconds: int = 600,
    ) -> AuraLiveGuardianReviewItem | None:
        user_id = self._identity(user_id, "user_id")
        audit_event_id = self._identity(audit_event_id, "audit_event_id")
        signal_category = self._identity(signal_category, "signal_category")
        confidence_bucket = self._identity(confidence_bucket, "confidence_bucket")
        if signal_severity < 0 or signal_severity > 4:
            raise ValueError("signal severity is invalid")

        if decision.action is ModerationAction.ESCALATE:
            review_kind: ReviewKind = "safety_escalation"
            expires_at = None
        elif decision.requires_human_confirmation and decision.action in _ACTIONABLE:
            review_kind = "action_confirmation"
            ttl = int(action_ttl_seconds)
            if ttl < 30 or ttl > 1800:
                raise ValueError("action review TTL must be between 30 and 1800 seconds")
            expires_at = self._timestamp(now) + timedelta(seconds=ttl)
        else:
            return None

        created_at = self._timestamp(now)
        review_id = str(uuid.uuid4())
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM aura_live_guardian_reviews WHERE audit_event_id = ?",
                (audit_event_id,),
            ).fetchone()
            if existing is not None:
                item = self._from_row(existing)
                if item.user_id != user_id:
                    raise ValueError("audit event review tenant mismatch")
                return item

            connection.execute(
                """
                INSERT INTO aura_live_guardian_reviews (
                    review_id, user_id, audit_event_id, review_kind, signal_category,
                    signal_severity, confidence_bucket, recommended_action,
                    provider_write_permitted_at_decision, status, created_at, expires_at,
                    resolved_at, resolved_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, NULL, NULL)
                """,
                (
                    review_id,
                    user_id,
                    audit_event_id,
                    review_kind,
                    signal_category,
                    signal_severity,
                    confidence_bucket,
                    decision.action.value,
                    int(decision.provider_write_permitted),
                    created_at.isoformat(),
                    expires_at.isoformat() if expires_at else None,
                ),
            )
        return self.get(user_id=user_id, review_id=review_id)

    def get(self, *, user_id: str, review_id: str) -> AuraLiveGuardianReviewItem:
        user_id = self._identity(user_id, "user_id")
        review_id = self._identity(review_id, "review_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM aura_live_guardian_reviews WHERE review_id = ? AND user_id = ?",
                (review_id, user_id),
            ).fetchone()
        if row is None:
            raise KeyError("Guardian review item not found")
        return self._from_row(row)

    def pending(self, user_id: str, *, limit: int = 100) -> list[AuraLiveGuardianReviewItem]:
        user_id = self._identity(user_id, "user_id")
        limit = max(1, min(int(limit), 200))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM aura_live_guardian_reviews
                WHERE user_id = ? AND status = 'pending'
                ORDER BY CASE WHEN review_kind='safety_escalation' THEN 0 ELSE 1 END,
                         signal_severity DESC, created_at ASC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def recent(self, user_id: str, *, limit: int = 50) -> list[AuraLiveGuardianReviewItem]:
        user_id = self._identity(user_id, "user_id")
        limit = max(1, min(int(limit), 200))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM aura_live_guardian_reviews WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def confirm_action(self, *, user_id: str, review_id: str, actor: str, now: datetime | None = None) -> AuraLiveGuardianReviewItem:
        return self._resolve(user_id=user_id, review_id=review_id, actor=actor, target="confirmed", expected_kind="action_confirmation", now=now, reject_expired=True)

    def dismiss_action(self, *, user_id: str, review_id: str, actor: str, now: datetime | None = None) -> AuraLiveGuardianReviewItem:
        return self._resolve(user_id=user_id, review_id=review_id, actor=actor, target="dismissed", expected_kind="action_confirmation", now=now, reject_expired=False)

    def acknowledge_escalation(self, *, user_id: str, review_id: str, actor: str, now: datetime | None = None) -> AuraLiveGuardianReviewItem:
        return self._resolve(user_id=user_id, review_id=review_id, actor=actor, target="acknowledged", expected_kind="safety_escalation", now=now, reject_expired=False)

    def _resolve(
        self,
        *,
        user_id: str,
        review_id: str,
        actor: str,
        target: ReviewStatus,
        expected_kind: ReviewKind,
        now: datetime | None,
        reject_expired: bool,
    ) -> AuraLiveGuardianReviewItem:
        user_id = self._identity(user_id, "user_id")
        review_id = self._identity(review_id, "review_id")
        actor = self._identity(actor, "actor")
        timestamp = self._timestamp(now)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM aura_live_guardian_reviews WHERE review_id = ? AND user_id = ?",
                (review_id, user_id),
            ).fetchone()
            if row is None:
                raise KeyError("Guardian review item not found")
            item = self._from_row(row)
            if item.status != "pending":
                raise ValueError("Guardian review item is already resolved")
            if item.review_kind != expected_kind:
                raise ValueError("Guardian review action is invalid for this item")
            if reject_expired and item.is_expired(timestamp):
                raise ValueError("Guardian action confirmation has expired")
            connection.execute(
                """
                UPDATE aura_live_guardian_reviews
                SET status = ?, resolved_at = ?, resolved_by = ?
                WHERE review_id = ? AND user_id = ? AND status = 'pending'
                """,
                (target, timestamp.isoformat(), actor, review_id, user_id),
            )
        return self.get(user_id=user_id, review_id=review_id)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> AuraLiveGuardianReviewItem:
        expires_at = datetime.fromisoformat(str(row["expires_at"])) if row["expires_at"] else None
        resolved_at = datetime.fromisoformat(str(row["resolved_at"])) if row["resolved_at"] else None
        return AuraLiveGuardianReviewItem(
            review_id=str(row["review_id"]),
            user_id=str(row["user_id"]),
            audit_event_id=str(row["audit_event_id"]),
            review_kind=str(row["review_kind"]),  # type: ignore[arg-type]
            signal_category=str(row["signal_category"]),
            signal_severity=int(row["signal_severity"]),
            confidence_bucket=str(row["confidence_bucket"]),
            recommended_action=str(row["recommended_action"]),
            provider_write_permitted_at_decision=bool(row["provider_write_permitted_at_decision"]),
            status=str(row["status"]),  # type: ignore[arg-type]
            created_at=datetime.fromisoformat(str(row["created_at"])),
            expires_at=expires_at,
            resolved_at=resolved_at,
            resolved_by=str(row["resolved_by"]) if row["resolved_by"] else None,
        )


__all__ = ["AuraLiveGuardianReviewItem", "AuraLiveGuardianReviewStore"]
