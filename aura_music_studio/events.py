from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import Field, field_validator

from .aura_sec_dlp import sanitize_audit_details
from .shared_contracts import ContractModel, NonEmptyId


_SENSITIVE_MARKERS = (
    "password", "secret", "token", "authorization", "api_key", "apikey",
    "private_key", "credential",
)


def _assert_audit_safe(value: Any, path: str = "audit_metadata") -> Any:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(marker in normalized for marker in _SENSITIVE_MARKERS):
                raise ValueError(f"{path} contains a sensitive key")
            _assert_audit_safe(nested, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _assert_audit_safe(nested, f"{path}[{index}]")
    return value


class EventEnvelope(ContractModel):
    event_id: NonEmptyId
    type: NonEmptyId
    event_version: int = Field(default=1, ge=1)
    schema_version: int = Field(default=1, ge=1)
    actor_id: NonEmptyId | None = None
    subject_type: NonEmptyId
    subject_id: NonEmptyId
    occurred_at: datetime
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: NonEmptyId
    trace_id: NonEmptyId | None = None
    source: NonEmptyId
    provider: str | None = Field(default=None, max_length=128)
    stream_id: NonEmptyId | None = None
    project_id: NonEmptyId | None = None
    battle_id: NonEmptyId | None = None
    idempotency_key: NonEmptyId | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    audit_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at", "received_at")
    @classmethod
    def aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("event timestamps must include timezone information")
        return value

    @field_validator("audit_metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Reject secret-shaped fields and scrub secret patterns from free-form values."""

        _assert_audit_safe(value)
        sanitized = sanitize_audit_details(
            value,
            max_depth=6,
            max_items=100,
            max_string_length=2000,
        )
        if not isinstance(sanitized, dict):
            raise ValueError("audit metadata must remain an object after sanitization")
        return sanitized


class EventPublisher(Protocol):
    def publish(self, event: EventEnvelope) -> None: ...


class OutboxWriter(Protocol):
    def enqueue_event(self, event: EventEnvelope, *, connection: Any | None = None) -> None: ...


class OutboxPublisher:
    """Broker-neutral adapter: publish persisted events then acknowledge them."""

    def __init__(self, *, store: Any, publisher: EventPublisher) -> None:
        self.store = store
        self.publisher = publisher

    def publish_pending(self, *, limit: int = 100) -> int:
        published = 0
        for record in self.store.pending_outbox(limit=limit):
            event = EventEnvelope.model_validate_json(record["event_json"])
            self.publisher.publish(event)
            self.store.mark_outbox_published(event.event_id)
            published += 1
        return published
