# Music Studio — Audio-to-MIDI Performance Intelligence

## Product truth

Audio-to-MIDI in the Music Studio is an editable symbolic control layer for the existing Pulsar MIDI Piano Roll and generation-guide workflow. MIDI is not final rendered music. Final music must still be produced by the connected real-audio renderer or by preserved user-owned source audio.

## Existing foundation

The repository already includes:

- a production MIDI document model with notes, velocity, channel, CC and pitch bend;
- real `.mid` read/write support through `mido`;
- piano-roll editing, transpose, velocity editing and non-destructive quantisation;
- Performance Input MIDI import into the DAW;
- use-as-generation-guide behaviour that preserves note contour, rhythm, lengths, velocity and controller expression;
- Pro entitlement through `AUDIO_TO_MIDI_CONTROL`.

This wave does not rebuild those systems.

## Performance transcription contract

Rights-cleared Performance Inputs of kind `hum`, `melody` and `instrument` can create project-confined MIDI transcription controls.

### Hum and melody

These use the bounded monophonic pYIN path. The implementation claims only one dominant pitched line. It infers:

- pitch;
- note starts;
- note durations;
- velocity from source RMS dynamics;
- voicing confidence.

It does not claim polyphonic separation.

### Instrument

Instrument Performance Inputs use `auto` mode. When the optional Basic Pitch runtime is installed, the existing transcription engine may use its polyphonic-capable path. If that runtime is unavailable or fails, auto mode falls back to the monophonic pYIN implementation. An explicitly requested polyphonic transcription fails closed when Basic Pitch is unavailable.

## Provenance

Each transcription sidecar records:

- transcription schema and engine version;
- engine used;
- requested mode;
- source SHA-256;
- MIDI output SHA-256;
- BPM;
- note count and pitch range;
- velocity statistics;
- whether the engine is polyphonic-capable;
- symbolic/final-audio truth.

Performance Input analysis now consumes that sidecar and persists the relevant provenance in `PerformanceInput.metadata` so the MIDI guide remains traceable to the real project audio that produced it.

A source-hash mismatch causes the Performance Input transcription integration to fail rather than silently attach MIDI to the wrong source.

## DAW workflow

The generated project MIDI is already consumable through:

`POST /projects/{project_name}/daw/midi/import-performance/{input_id}`

The imported clip is editable in the Pulsar MIDI Piano Roll and can then be used as a generation guide. The DAW keeps MIDI project-relative and does not expose raw filesystem source paths in browser payloads.

## Safety and commercial boundaries

- Audio must remain inside the member project boundary.
- Performance Input upload requires user ownership/licence attestation and receives an Asset Library rights record.
- Editable MIDI remains behind the existing `AUDIO_TO_MIDI_CONTROL` entitlement; this wave introduces no new tier or pricing.
- MIDI/transcription is always `symbolic_guide_only` and never a Final Master.
- This wave does not claim stem separation, articulation recognition, automatic orchestration, or universal polyphonic transcription.
