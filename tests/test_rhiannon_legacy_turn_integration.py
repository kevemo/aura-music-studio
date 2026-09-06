from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from aura_music_studio.aura_realtime_voice_ui import REALTIME_VOICE_SCRIPT
from aura_music_studio.aura_voice_conversation import VOICE_SCRIPT
from aura_music_studio.rhiannon_turn_state import (
    RHIANNON_TURN_SCRIPT,
    RhiannonModelMetadata,
    RhiannonTurnState,
    RhiannonTurnStateMachine,
    companion_contract,
    legacy_base_reference_metadata,
    public_turn_contract,
)


def _request(member=True):
    return SimpleNamespace(state=SimpleNamespace(member=object() if member else None))


def _advance_to_response(machine: RhiannonTurnStateMachine) -> None:
    machine.transition(RhiannonTurnState.READY)
    machine.transition(RhiannonTurnState.LISTENING)
    machine.transition(RhiannonTurnState.PROCESSING)
    machine.transition(RhiannonTurnState.THINKING)
    machine.transition(RhiannonTurnState.RESPONDING)


def test_canonical_turn_lifecycle_tracks_speech_job_and_rejects_stale_job():
    machine = RhiannonTurnStateMachine()
    _advance_to_response(machine)
    speaking = machine.begin_speech("speech_message_1", expression="warm")

    assert speaking.state == RhiannonTurnState.SPEAKING
    assert speaking.active_speech_job_id == "speech_message_1"
    assert machine.accepts_speech_job("speech_message_1") is True
    assert machine.accepts_speech_job("speech_old") is False

    interrupted = machine.interrupt(reason="user_barge_in", next_state=RhiannonTurnState.LISTENING)
    assert interrupted.state == RhiannonTurnState.LISTENING
    assert interrupted.active_speech_job_id is None
    assert machine.accepts_speech_job("speech_message_1") is False

    with pytest.raises(ValueError, match="stale or unknown"):
        machine.finish_speech("speech_message_1")


def test_speech_job_supersession_invalidates_previous_job_without_parallel_state():
    machine = RhiannonTurnStateMachine()
    _advance_to_response(machine)
    machine.begin_speech("speech_a")
    replacement = machine.begin_speech("speech_b")

    assert replacement.state == RhiannonTurnState.SPEAKING
    assert replacement.active_speech_job_id == "speech_b"
    assert machine.accepts_speech_job("speech_a") is False
    assert machine.accepts_speech_job("speech_b") is True


def test_turn_state_rejects_invalid_transition_and_free_form_expression():
    machine = RhiannonTurnStateMachine()

    with pytest.raises(ValueError, match="invalid Rhiannon turn transition"):
        machine.transition(RhiannonTurnState.SPEAKING)

    machine.transition(RhiannonTurnState.READY)
    with pytest.raises(ValueError, match="unsupported Rhiannon expression"):
        machine.transition(RhiannonTurnState.LISTENING, expression="run-arbitrary-animation")


def test_error_and_recovery_are_explicit_states():
    machine = RhiannonTurnStateMachine()
    failed = machine.fail("provider_unavailable")
    assert failed.state == RhiannonTurnState.ERROR
    recovered = machine.recover(next_state=RhiannonTurnState.READY)
    assert recovered.state == RhiannonTurnState.READY


def test_legacy_glb_metadata_is_reference_only_and_cannot_be_promoted_without_rig_evidence():
    reference = legacy_base_reference_metadata()
    assert reference.source == "Rhiannon_Legacy_Aura_Base_Mesh_REFERENCE.glb"
    assert reference.source_sha256 == "0707d8ab2feb9a9dd09ebf99334d69b334a9acdda6834f62e235213b32588362"
    assert reference.vertices == 10023
    assert reference.triangles == 9997
    assert reference.has_skeleton is False
    assert reference.has_skin is False
    assert reference.has_animations is False
    assert reference.has_morph_targets is False
    assert reference.production_ready is False

    with pytest.raises(ValidationError, match="production Rhiannon model requires"):
        RhiannonModelMetadata(
            model_id="rhiannon.bad-production-claim",
            version="1",
            source="legacy.glb",
            production_status="production",
        )


def test_public_companion_contract_is_private_fail_closed_and_uses_existing_bus():
    contract = public_turn_contract()
    required_states = {
        "idle",
        "ready",
        "listening",
        "processing",
        "thinking",
        "responding",
        "speaking",
        "interrupted",
        "awaiting_permission",
        "degraded",
        "error",
        "recovery",
    }
    assert required_states.issubset(set(contract["states"]))
    assert contract["speech_job_contract"]["stale_job_rejection"] is True
    assert contract["boundaries"]["uses_existing_avatar_performance_bus"] is True
    assert contract["boundaries"]["voice_cloning_owned_here"] is False
    assert contract["legacy_model_reference"]["production_ready"] is False

    response = companion_contract(_request())
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"

    with pytest.raises(HTTPException) as exc:
        companion_contract(_request(member=False))
    assert exc.value.status_code == 401


def test_browser_voice_paths_share_turn_host_and_support_real_interruption_controls():
    assert "window.RhiannonTurnHost" in RHIANNON_TURN_SCRIPT
    assert "stale_speech_frame" in RHIANNON_TURN_SCRIPT
    assert "activeSpeechJob" in VOICE_SCRIPT
    assert "Interrupt Rhiannon" in VOICE_SCRIPT
    assert "job_id:jobId" in VOICE_SCRIPT
    assert "response.cancel" in REALTIME_VOICE_SCRIPT
    assert "user_barge_in" in REALTIME_VOICE_SCRIPT
    assert "Interrupt Rhiannon" in REALTIME_VOICE_SCRIPT
    assert "eval(" not in RHIANNON_TURN_SCRIPT
