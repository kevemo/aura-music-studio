from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from aura_music_studio import creation_coin_metering
from aura_music_studio.render_attempts import ActiveRenderAttemptError, RenderAttemptStore


def _store(tmp_path):
    return RenderAttemptStore(tmp_path / "render-attempts.sqlite3")


def test_reserve_blocks_second_active_attempt(tmp_path):
    store = _store(tmp_path)
    first = store.reserve("member-1", "project-one", "directive-1")

    with pytest.raises(ActiveRenderAttemptError) as exc_info:
        store.reserve("member-1", "project-one", "directive-1")

    assert exc_info.value.attempt.attempt_id == first.attempt_id
    assert store.active("member-1", "project-one", "directive-1") == first


def test_independent_render_targets_can_reserve(tmp_path):
    store = _store(tmp_path)

    first = store.reserve("member-1", "project-one", "directive-1")
    second = store.reserve("member-1", "project-one", "directive-2")
    third = store.reserve("member-2", "project-one", "directive-1")

    assert len({first.attempt_id, second.attempt_id, third.attempt_id}) == 3


def test_terminal_attempt_allows_fresh_rerender(tmp_path):
    store = _store(tmp_path)
    first = store.reserve("member-1", "project-one", "directive-1")
    queued = store.mark_queued(first.attempt_id, "provider-prompt-1")
    completed = store.mark_completed(queued.attempt_id)

    assert completed.state == "completed"
    assert completed.provider_prompt_id == "provider-prompt-1"
    assert store.active("member-1", "project-one", "directive-1") is None

    second = store.reserve("member-1", "project-one", "directive-1")
    assert second.attempt_id != first.attempt_id
    assert second.charge_reference != first.charge_reference
    assert second.refund_reference != first.refund_reference


def test_store_restart_preserves_active_admission(tmp_path):
    db_path = tmp_path / "render-attempts.sqlite3"
    first_store = RenderAttemptStore(db_path)
    first = first_store.reserve("member-1", "project-one", "directive-1")

    restarted_store = RenderAttemptStore(db_path)
    active = restarted_store.active("member-1", "project-one", "directive-1")

    assert active is not None
    assert active.attempt_id == first.attempt_id
    with pytest.raises(ActiveRenderAttemptError):
        restarted_store.reserve("member-1", "project-one", "directive-1")


def test_parallel_stores_only_admit_one_render(tmp_path):
    db_path = tmp_path / "render-attempts.sqlite3"
    first_store = RenderAttemptStore(db_path)
    second_store = RenderAttemptStore(db_path)
    barrier = Barrier(2)

    def reserve(store):
        barrier.wait()
        try:
            return store.reserve("member-1", "project-one", "directive-1")
        except ActiveRenderAttemptError:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(reserve, (first_store, second_store)))

    admitted = [outcome for outcome in outcomes if outcome != "blocked"]
    blocked = [outcome for outcome in outcomes if outcome == "blocked"]
    assert len(admitted) == 1
    assert len(blocked) == 1


def test_invalid_render_attempt_transition_is_rejected(tmp_path):
    store = _store(tmp_path)
    attempt = store.reserve("member-1", "project-one", "directive-1")

    with pytest.raises(ValueError, match="Invalid render attempt transition"):
        store.mark_completed(attempt.attempt_id)

    assert store.get(attempt.attempt_id).state == "reserved"


def test_charged_attempt_persists_stable_provider_and_ledger_evidence(tmp_path):
    store = _store(tmp_path)
    attempt = store.reserve("member-1", "project-one", "directive-1")
    charged = store.mark_charged(attempt.attempt_id, 25, "ledger-9")
    queued = store.mark_queued(charged.attempt_id, "provider-prompt-7")

    assert queued.state == "queued"
    assert queued.charge_amount == 25
    assert queued.charge_ledger_id == "ledger-9"
    assert queued.provider_prompt_id == "provider-prompt-7"
    assert queued.charge_reference.endswith(":charge")
    assert queued.refund_reference.endswith(":refund")


def test_creation_coin_charge_accepts_persistent_attempt_references(monkeypatch):
    observed = {}

    class Wallet:
        def spend(self, user_id, amount, *, reason, reference, actor):
            observed.update(
                user_id=user_id,
                amount=amount,
                reason=reason,
                reference=reference,
                actor=actor,
            )
            return {"id": "ledger-1", "amount": -amount, "reference": reference}

    monkeypatch.setenv(
        "LSS_CREATION_COIN_COSTS_JSON",
        '{"free_video_render": 7}',
    )
    monkeypatch.setattr(creation_coin_metering, "CreditWalletStore", lambda: Wallet())

    charge = creation_coin_metering.charge_free_video_render(
        "member-1",
        project_id="project-one",
        directive_id="directive-1",
        charge_reference="creative-render:attempt-1:charge",
        refund_reference="creative-render:attempt-1:refund",
    )

    assert charge.cost == 7
    assert observed["reference"] == "creative-render:attempt-1:charge"
    assert charge.refund_reference == "creative-render:attempt-1:refund"


def test_creation_coin_stable_references_must_be_supplied_as_pair(monkeypatch):
    monkeypatch.setenv(
        "LSS_CREATION_COIN_COSTS_JSON",
        '{"free_video_render": 7}',
    )

    with pytest.raises(ValueError, match="must be supplied together"):
        creation_coin_metering.charge_free_video_render(
            "member-1",
            project_id="project-one",
            directive_id="directive-1",
            charge_reference="creative-render:attempt-1:charge",
        )
