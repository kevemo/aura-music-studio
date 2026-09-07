# Aura Groove & Timing Editor / Humanisation

This document records the production-truth contract for the Chat 2 Groove & Timing Editor and bounded Humanisation engine.

## What is implemented

The Music Studio can extract a reusable groove template from a rights-cleared Performance Input and apply that feel to another real project audio asset through the existing durable engineering-job queue.

The implemented path provides:

- beat/onset reuse from the Performance Input analysis pipeline;
- fallback beat/onset analysis when required by the engine;
- a four-beat groove cycle represented at sixteenth-note resolution;
- per-slot timing offsets, observation counts and confidence;
- eighth-note swing-ratio estimation;
- timing-variation diagnostics;
- project-scoped persisted groove templates under `work/groove_templates/`;
- source SHA-256 provenance and rights-record linkage;
- application to drums, bass, guitar, piano, percussion or a generic audio role;
- adjustable groove strength;
- deterministic, seeded timing humanisation;
- bounded maximum timing movement;
- pitch-preserving local real-audio segment stretching;
- crossfaded segment assembly;
- 24-bit WAV output;
- source-file immutability verification after render;
- silence/invalid-output rejection;
- reusable Asset Library derivative registration;
- existing `MULTITRACK_DAW` entitlement and `PRIORITY_QUEUE` behavior rather than a new commercial boundary.

## API surface

Performance Input analysis:

- `POST /projects/{project_name}/performance-inputs/{input_id}/groove-template`
- `GET /projects/{project_name}/performance-inputs/{input_id}/groove-template`

Rendering uses the existing engineering endpoint:

- `POST /production/projects/{project_name}/engineering-jobs`

with `operation: "groove_follow"` and a `target_performance_input_id`.

The engineering request also accepts:

- `instrument_role`: `drums`, `bass`, `guitar`, `piano`, `percussion`, or `other`;
- `groove_strength`: 0.0–1.0;
- `humanize_timing_ms`: 0–40 ms;
- `humanize_seed`: deterministic non-negative seed;
- `max_groove_shift_ms`: 5–150 ms;
- `groove_max_stretch_ratio`: 1.05–1.8;
- `source_bpm` and `crossfade_ms` through the existing timing-render controls.

## Safety and fidelity boundaries

Groove extraction and application are non-destructive. The original guide and the source asset are never rewritten.

A Groove Follow job fails closed when:

- the target Performance Input is missing;
- its rights/provenance record is incomplete;
- there are too few reliable timing events;
- a timing profile would reverse musical time;
- a segment would require stretching outside the configured professional bound;
- the source or output is invalid/silent;
- source SHA-256 changes during the operation;
- an unsupported instrument role or out-of-contract parameter is supplied.

The renderer is intentionally bounded rather than forcing any groove onto incompatible material. Extreme pocket/swing patterns may require a different source arrangement or a user-approved higher stretch bound inside the allowed ceiling.

## Humanisation scope

This wave implements real-audio **timing humanisation**: controlled seeded micro-timing imperfections added after the reference groove offset and before safe real-audio rendering.

It does **not** misrepresent MIDI as finished audio. Existing symbolic MIDI/transcription remains a guide/edit layer only.

Velocity, note-length and articulation humanisation belong to editable MIDI/instrument-performance authoring and can be expanded in the dedicated MIDI/Instrument Studio wave without weakening this real-audio contract.

## Master-spec coverage

This closes the core real-audio portion of the Music master specification's:

- Groove & Timing Editor: detect groove from a reference performance and apply it to drums, bass, guitar, piano and percussion;
- Humanisation: timing variation / performance imperfection;
- Drum Studio groove and swing foundation.

It deliberately does not claim every future drum sequencer, MIDI humanisation or generative rhythm feature is complete.

## Provenance returned with finished audio

A successful `groove_follow` result returns:

- the source asset ID;
- target Performance Input ID;
- persisted groove-template reference and public template data;
- selected instrument role;
- a project-relative real-audio output reference;
- a reusable Asset Library derivative;
- a conform report recording source hashes, timing anchors, render-segment count, applied shift ceiling, stretch range, output peak/RMS and deterministic humanisation controls;
- `audio_origin: local_pitch_preserving_groove_conform`;
- `source_preserved: true`;
- `final_audio: true`.

No host filesystem path is intentionally returned through this feature surface.
