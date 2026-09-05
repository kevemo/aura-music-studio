from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from aura_music_studio.events import EventEnvelope
from aura_music_studio.shared_persistence import (
    IdempotencyConflictError,
    IdempotencyDisposition,
    IdempotencyLeaseLostError,
    SharedPersistence,
    canonical_request_hash,
)


NOW = datetime.now(timezone.utc)


def _set_claim_updated_at(
    store: SharedPersistence,
    *,
    idempotency_key: str,
    updated_at: str,
) -> None:
    with store.transaction() as connection:
        cursor = connection.execute(
            "UPDATE shared_idempotency SET updated_at=? WHERE idempotency_key=?",
            (updated_at, idempotency_key),
        )
        assert cursor.rowcount == 1


def _age_claim(
    store: SharedPersistence,
    *,
    idempotency_key: str,
    age: timedelta,
) -> None:
    _set_claim_updated_at(
        store,
        idempotency_key=idempotency_key,
        updated_at=(datetime.now(timezone.utc) - age).isoformat(),
    )


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
    with pytest.raises(TypeError):
        canonical_request_hash({"not_json": (1, 2, 3)})
    with pytest.raises(TypeError):
        canonical_request_hash({1: "non-string-key"})
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
            correlation_id="memory-corr",
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


def test_stale_claim_recovery_changes_lease_owner_and_blocks_old_worker(tmp_path: Path):
    store = SharedPersistence(tmp_path / "stale-recovery.db")
    store.initialize()
    request_hash = canonical_request_hash({"operation": "recover"})

    disposition, replay = store.claim_idempotency(
        idempotency_key="stale-idem",
        request_hash=request_hash,
        correlation_id="worker-old",
    )
    assert disposition is IdempotencyDisposition.NEW
    assert replay is None

    # A fresh in-progress claim remains fail-closed even when recovery is enabled.
    disposition, replay = store.claim_idempotency(
        idempotency_key="stale-idem",
        request_hash=request_hash,
        correlation_id="worker-too-early",
        reclaim_stale_after=timedelta(minutes=5),
    )
    assert disposition is IdempotencyDisposition.IN_PROGRESS
    assert replay is None

    _age_claim(store, idempotency_key="stale-idem", age=timedelta(minutes=10))
    disposition, replay = store.claim_idempotency(
        idempotency_key="stale-idem",
        request_hash=request_hash,
        correlation_id="worker-new",
        reclaim_stale_after=timedelta(minutes=5),
    )
    assert disposition is IdempotencyDisposition.NEW
    assert replay is None

    # The abandoned worker cannot commit after its lease has been reclaimed.
    with pytest.raises(IdempotencyLeaseLostError):
        store.complete_idempotency(
            idempotency_key="stale-idem",
            request_hash=request_hash,
            correlation_id="worker-old",
            response_status=500,
            response_body={"winner": "old"},
        )

    store.complete_idempotency(
        idempotency_key="stale-idem",
        request_hash=request_hash,
        correlation_id="worker-new",
        response_status=201,
        response_body={"winner": "new"},
    )

    # Completion is immutable. A resumed stale worker becomes a harmless no-op.
    store.complete_idempotency(
        idempotency_key="stale-idem",
        request_hash=request_hash,
        correlation_id="worker-old",
        response_status=500,
        response_body={"winner": "old-after-completion"},
    )
    disposition, replay = store.claim_idempotency(
        idempotency_key="stale-idem",
        request_hash=request_hash,
        correlation_id="replay-reader",
        reclaim_stale_after=timedelta(seconds=1),
    )
    assert disposition is IdempotencyDisposition.REPLAY
    assert replay == {
        "status": 201,
        "body": {"winner": "new"},
        "correlation_id": "worker-new",
    }


def test_stale_recovery_never_reuses_key_for_different_request(tmp_path: Path):
    store = SharedPersistence(tmp_path / "stale-conflict.db")
    store.initialize()
    first_hash = canonical_request_hash({"amount": 100})
    second_hash = canonical_request_hash({"amount": 200})
    store.claim_idempotency(
        idempotency_key="stale-conflict",
        request_hash=first_hash,
        correlation_id="worker-old",
    )
    _age_claim(store, idempotency_key="stale-conflict", age=timedelta(hours=1))

    with pytest.raises(IdempotencyConflictError):
        store.claim_idempotency(
            idempotency_key="stale-conflict",
            request_hash=second_hash,
            correlation_id="worker-new",
            reclaim_stale_after=timedelta(seconds=1),
        )


def test_completed_claim_is_replayed_not_reclaimed_even_if_timestamp_is_old(tmp_path: Path):
    store = SharedPersistence(tmp_path / "completed-replay.db")
    store.initialize()
    request_hash = canonical_request_hash({"operation": "completed"})
    store.claim_idempotency(
        idempotency_key="completed-idem",
        request_hash=request_hash,
        correlation_id="worker-complete",
    )
    store.complete_idempotency(
        idempotency_key="completed-idem",
        request_hash=request_hash,
        correlation_id="worker-complete",
        response_status=202,
        response_body={"accepted": True},
    )
    _age_claim(store, idempotency_key="completed-idem", age=timedelta(days=1))

    disposition, replay = store.claim_idempotency(
        idempotency_key="completed-idem",
        request_hash=request_hash,
        correlation_id="worker-reclaim-attempt",
        reclaim_stale_after=timedelta(seconds=1),
    )
    assert disposition is IdempotencyDisposition.REPLAY
    assert replay == {
        "status": 202,
        "body": {"accepted": True},
        "correlation_id": "worker-complete",
    }


def test_stale_recovery_policy_is_positive_timedelta_and_timestamp_is_aware(tmp_path: Path):
    store = SharedPersistence(tmp_path / "recovery-policy.db")
    store.initialize()
    request_hash = canonical_request_hash({"operation": "policy"})
    store.claim_idempotency(
        idempotency_key="policy-idem",
        request_hash=request_hash,
        correlation_id="policy-worker",
    )

    for invalid in (timedelta(0), timedelta(seconds=-1)):
        with pytest.raises(ValueError):
            store.claim_idempotency(
                idempotency_key="policy-idem",
                request_hash=request_hash,
                correlation_id="policy-reclaim",
                reclaim_stale_after=invalid,
            )
    with pytest.raises(TypeError):
        store.claim_idempotency(
            idempotency_key="policy-idem",
            request_hash=request_hash,
            correlation_id="policy-reclaim",
            reclaim_stale_after=60,  # type: ignore[arg-type]
        )

    _set_claim_updated_at(
        store,
        idempotency_key="policy-idem",
        updated_at=datetime.now().isoformat(),
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        store.claim_idempotency(
            idempotency_key="policy-idem",
            request_hash=request_hash,
            correlation_id="policy-reclaim",
            reclaim_stale_after=timedelta(seconds=1),
        )


@pytest.mark.parametrize(
    ("body", "error_type"),
    [
        ({"not_json": {1, 2, 3}}, TypeError),
        ({"not_json": (1, 2, 3)}, TypeError),
        ({1: "non-string-key"}, TypeError),
        ({"not_finite": float("nan")}, ValueError),
        ({"object": object()}, TypeError),
    ],
)
def test_completed_replay_body_requires_strict_json(
    tmp_path: Path,
    body: object,
    error_type: type[Exception],
):
    store = SharedPersistence(tmp_path / "strict-response.db")
    store.initialize()
    request_hash = canonical_request_hash({"operation": "strict-response"})
    store.claim_idempotency(
        idempotency_key="strict-response-idem",
        request_hash=request_hash,
        correlation_id="strict-response-worker",
    )

    with pytest.raises(error_type):
        store.complete_idempotency(
            idempotency_key="strict-response-idem",
            request_hash=request_hash,
            correlation_id="strict-response-worker",
            response_status=200,
            response_body=body,
        )

    # Serialization failure must not mutate the lease into a completed replay.
    disposition, replay = store.claim_idempotency(
        idempotency_key="strict-response-idem",
        request_hash=request_hash,
        correlation_id="strict-response-reader",
    )
    assert disposition is IdempotencyDisposition.IN_PROGRESS
    assert replay is None


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
        correlation_id="corr1",
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
