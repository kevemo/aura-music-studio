from __future__ import annotations

from pathlib import Path

import pytest

from aura_music_studio.professional_editor import (
    EditorSequence,
    EditorTrack,
    ProfessionalEditorProject,
    ProfessionalEditorStore,
    VideoClip,
)
from aura_music_studio.professional_editor_transitions import (
    EditorTransition,
    apply_editor_transition,
    validate_sequence_transition_topology,
)


def _sequence() -> EditorSequence:
    return EditorSequence(
        id="sequence-1",
        name="Sequence 1",
        kind="video",
        tracks=[
            EditorTrack(
                id="track-1",
                name="Video 1",
                kind="video",
                items=[
                    VideoClip(
                        id="clip-a",
                        name="A",
                        source_asset_id="asset-a",
                        timeline_start_seconds=0.0,
                        source_in_seconds=0.0,
                        source_out_seconds=3.0,
                    ),
                    VideoClip(
                        id="clip-b",
                        name="B",
                        source_asset_id="asset-b",
                        timeline_start_seconds=2.0,
                        source_in_seconds=0.0,
                        source_out_seconds=3.0,
                    ),
                ],
            )
        ],
    )


def test_cross_dissolve_requires_adjacent_same_track_overlap() -> None:
    sequence = _sequence()
    transition = EditorTransition(
        id="transition-1",
        kind="cross_dissolve",
        track_id="track-1",
        outgoing_clip_id="clip-a",
        incoming_clip_id="clip-b",
        duration_seconds=1.0,
        easing="linear",
    )

    validated = validate_sequence_transition_topology(sequence, transition)
    assert validated.kind == "cross_dissolve"


def test_cross_dissolve_rejects_duration_that_does_not_match_authored_overlap() -> None:
    sequence = _sequence()
    transition = EditorTransition(
        id="transition-1",
        kind="cross_dissolve",
        track_id="track-1",
        outgoing_clip_id="clip-a",
        incoming_clip_id="clip-b",
        duration_seconds=0.5,
        easing="linear",
    )

    with pytest.raises(ValueError, match="overlap"):
        validate_sequence_transition_topology(sequence, transition)


def test_transition_rejects_unsupported_easing() -> None:
    with pytest.raises(ValueError):
        EditorTransition(
            id="transition-1",
            kind="fade_in",
            track_id="track-1",
            incoming_clip_id="clip-a",
            duration_seconds=0.5,
            easing="smooth",
        )


def test_fade_in_rejects_cross_clip_fields() -> None:
    sequence = _sequence()
    transition = EditorTransition(
        id="transition-1",
        kind="fade_in",
        track_id="track-1",
        incoming_clip_id="clip-a",
        outgoing_clip_id="clip-b",
        duration_seconds=0.5,
        easing="linear",
    )

    with pytest.raises(ValueError, match="outgoing"):
        validate_sequence_transition_topology(sequence, transition)


def test_apply_transition_is_sequence_owned_and_replaces_same_id() -> None:
    sequence = _sequence()
    first = EditorTransition(
        id="transition-1",
        kind="fade_in",
        track_id="track-1",
        incoming_clip_id="clip-a",
        duration_seconds=0.25,
        easing="linear",
    )
    second = first.model_copy(update={"duration_seconds": 0.75})

    apply_editor_transition(sequence, first)
    apply_editor_transition(sequence, second)

    assert len(sequence.transitions) == 1
    assert sequence.transitions[0].duration_seconds == 0.75


def test_professional_editor_store_round_trip_preserves_transitions(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    store = ProfessionalEditorStore(project_dir)
    sequence = _sequence()
    apply_editor_transition(
        sequence,
        EditorTransition(
            id="transition-1",
            kind="cross_dissolve",
            track_id="track-1",
            outgoing_clip_id="clip-a",
            incoming_clip_id="clip-b",
            duration_seconds=1.0,
            easing="linear",
        ),
    )
    document = ProfessionalEditorProject(project_name="project", sequences=[sequence])

    store.save(document)
    reloaded = store.load()

    assert reloaded.sequences[0].transitions[0].kind == "cross_dissolve"
    assert reloaded.sequences[0].transitions[0].duration_seconds == 1.0
