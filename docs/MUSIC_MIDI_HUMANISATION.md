# MIDI Performance Humanisation

## Production truth

Aura's MIDI humanisation layer adds bounded performance variation to editable MIDI control data in the existing DAW workflow. It complements the real-audio Groove & Timing humanisation engine; it does not replace or render final audio.

The production endpoint is:

`POST /projects/{project_name}/daw/midi/{clip_id}/humanize`

It uses the existing Pro MIDI entitlement (`AUDIO_TO_MIDI_CONTROL`), project-scoped MIDI path confinement, DAW revision history and session persistence.

## Humanised dimensions

The v1 engine can vary:

- note start timing, in milliseconds;
- note-on velocity;
- note duration as a bounded percentage.

Pitch, MIDI channel, CC controller events and pitch-bend events are preserved. Aura does not claim articulation synthesis from this symbolic transform.

## Reproducibility

Humanisation is deterministic for a given input document, parameter set and integer seed. This allows a creator to recall the same performance variation during revisions rather than receiving uncontrolled randomness each time.

The first zero-beat downbeat can be preserved exactly. All note starts remain non-negative, velocities remain 1–127, and note lengths retain a minimum one-tick duration at the DAW's 480 PPQ control resolution.

## Safety bounds

The request contract bounds:

- timing variation: 0–80 ms;
- velocity variation: 0–32 MIDI velocity units;
- duration variation: 0–35%;
- seed: 0–2,147,483,647;
- working BPM: 20–400.

The operation is revision-safe and writes back through the existing project-confined MIDI save path.

## Symbolic/final-audio boundary

Every API response explicitly reports:

- `symbolic_guide_only: true`;
- `audio_rendered: false`;
- `final_audio: false`.

MIDI humanisation changes control data only. A release master still requires a connected real music renderer or a real recorded performance.

## Scope boundary

This closes the timing/velocity/note-length portion of the Music Studio Humanisation requirement for MIDI workflows. It does not claim instrument-specific articulation modelling, physical-performance simulation, source separation, or final-audio rendering.
