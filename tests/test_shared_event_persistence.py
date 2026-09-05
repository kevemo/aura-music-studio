from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from aura_music_studio.events import EventEnvelope
from aura_music_studio.shared_persistence import (
    IdempotencyConflictError,
    IdempotencyDisposition,
    SharedPersistence,
    canonical_request_hash,
)


NOW = datetime.now(timezone.utc)


def test_event_rejects_naive_timestamps_and_sensitive_audit_metadata():
    kwargs = dict(
        event_id="evt1",
        type="gift.sent",
        subject_type="gift",
        subject_id="gift1",
        occurred_at=NOW,
        correlation_id="corr1",
        source="internal",
    )
    EventEnvelope(**kwargs)
    with pytest.raises(ValidationError):
        EventEnvelope(**(kwargs | {"occurred_at": datetime.now()}))
    with pytest.raises(ValidationError):
        EventEnvelope(**(kwargs | {"audit_metadata": {"access_token": "oops"}}))


def test_canonical_request_hash_is_order_stable_and_json_strict():
    first = canonical_request_hash({"amount": 100, "meta": {"b": 2, "a": 1}})
    second = canonical_request_hash({"meta": {"a": 1, "b": 2}, "amount": 100})
    assert first == second

    with pytest.raises(TypeError):
        canonical_request_hash({"not_json": {1, 2, 3}})
    with pytest.raises(ValueError):
        canonical_request_hash({"not_finite": float("nan")})


def test_in_memory_store_survives_across_separate_connections():
    store = SharedPersistence(":memory:")
    try:
        store.initialize()
        request_hash = canonical_request_hash({"operation": "memory-test"})
        disposition, replay = store.claim_idempotency(
            idempotency_key="memory-idem",
            request_hash=request_hash,
            correlation_id="memory-corr",
        )
        assert disposition is IdempotencyDisposition.NEW
        assert replay is None

        store.complete_idempotency(
            idempotency_key="memory-idem",
            request_hash=request_hash,
            response_status=200,
            response_body={"ok": True},
        )
        disposition, replay = store.claim_idempotency(
            idempotency_key="memory-idem",
            request_hash=request_hash,
            correlation_id="later-corr",
        )
        assert disposition is IdempotencyDisposition.REPLAY
        assert replay == {
            "status": 200,
            "body": {"ok": True},
            "correlation_id": "memory-corr",
        }
    finally:
        store.close()


def test_idempotency_replay_conflict_and_outbox(tmp_path: Path):
    store = SharedPersistence(tmp_path / "shared.db")
    store.initialize()
    request_hash = canonical_request_hash({"amount": 100})
    disposition, replay = store.claim_idempotency(
        idempotency_key="idem1", request_hash=request_hash, correlation_id="corr1"
    )
    assert disposition is IdempotencyDisposition.NEW
    assert replay is None
    store.complete_idempotency(
        idempotency_key="idem1",
        request_hash=request_hash,
        response_status=201,
        response_body={"ok": True},
    )
    disposition, replay = store.claim_idempotency(
        idempotency_key="idem1", request_hash=request_hash, correlation_id="corr2"
    )
    assert disposition is IdempotencyDisposition.REPLAY
    assert replay["body"] == {"ok": True}
    assert replay["correlation_id"] == "corr1"
    with pytest.raises(IdempotencyConflictError):
        store.claim_idempotency(
            idempotency_key="idem1",
            request_hash=canonical_request_hash({"amount": 200}),
            correlation_id="corr3",
        )

    event = EventEnvelope(
        event_id="evt1",
        type="ledger.posted",
        subject_type="ledger",
        subject_id="tx1",
        occurred_at=NOW,
        correlation_id="corr1",
        source="internal",
        idempotency_key="idem1",
    )
    store.enqueue_event(event)
    assert [item["event_id"] for item in store.pending_outbox()] == ["evt1"]
    store.mark_outbox_published("evt1")
    assert store.pending_outbox() == []
