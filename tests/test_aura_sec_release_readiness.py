from __future__ import annotations

import pytest

from aura_music_studio.aura_sec_release_readiness import (
    REQUIRED_RELEASE_GATES,
    assert_release_ready,
    evaluate_release_readiness,
)


def test_release_readiness_fails_closed_when_evidence_is_absent():
    result = evaluate_release_readiness({})
    assert result.releasable is False
    assert result.percentage == 0
    assert set(result.missing) == set(REQUIRED_RELEASE_GATES)


def test_unknown_fields_do_not_satisfy_release_gate():
    result = evaluate_release_readiness({"everything_ready": True})
    assert result.releasable is False
    assert result.completed == ()


def test_every_required_gate_must_be_explicitly_true():
    evidence = {gate: True for gate in REQUIRED_RELEASE_GATES}
    evidence[REQUIRED_RELEASE_GATES[-1]] = False
    result = evaluate_release_readiness(evidence)
    assert result.releasable is False
    assert result.percentage < 100
    assert REQUIRED_RELEASE_GATES[-1] in result.missing


def test_complete_evidence_allows_release():
    evidence = {gate: True for gate in REQUIRED_RELEASE_GATES}
    result = assert_release_ready(evidence)
    assert result.releasable is True
    assert result.percentage == 100
    assert result.missing == ()


def test_assert_release_ready_names_missing_gates():
    with pytest.raises(RuntimeError, match="native_clients_built"):
        assert_release_ready({})
