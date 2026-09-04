# Drum Studio — Sequencer Foundation

## Production truth

Aura Drum Studio provides deterministic editable drum-pattern control data for the existing Pulsar DAW MIDI workflow. This layer is **symbolic MIDI control/edit data**, not final rendered audio. Final music must still be rendered as real audio through the connected music engine.

## Implemented

The production Drum Studio engine provides:

- 8-, 16- and 32-step-per-bar grids;
- 1–64 bar patterns;
- General MIDI percussion mapping on MIDI channel 10 (zero-based channel 9);
- kick, snare, clap, closed/open/pedal hats, toms, crash, ride, tambourine, cowbell and shaker lanes;
- custom MIDI percussion-note lanes;
- per-hit velocity;
- per-hit probability;
- per-hit note length;
- per-hit micro-timing shift;
- global swing from straight 0.50 through 0.75;
- bounded seeded timing humanisation up to 40 ms;
- bounded seeded velocity humanisation up to ±24 velocity units;
- deterministic probability/humanisation decisions from a stable SHA-256-derived seed;
- conversion into the existing `MidiDocument` / `MidiNote` model used by the Pulsar piano roll;
- an editable four-on-the-floor starter groove.

## Production DAW API

The sequencer is mounted in the canonical production application beside the existing DAW/MIDI routes. It reuses the current signed-in-member and `AUDIO_TO_MIDI_CONTROL` Pro entitlement gate rather than inventing a new commercial entitlement.

Available routes:

- `GET /projects/{project_name}/daw/drums` — capability truth, lane map and session BPM;
- `POST /projects/{project_name}/daw/drums/patterns` — validate a bounded pattern, write real MIDI control data, create a normal editable Pulsar MIDI track/clip and persist the pattern manifest inside the project;
- `POST /projects/{project_name}/daw/drums/starter/four-on-the-floor` — create the deterministic editable starter groove and insert it through the same clip path.

Created clips use the existing piano roll, MIDI writer, revision snapshots and DAW session storage. Browser responses use the existing public clip representation and do not expose the server filesystem source path.

Each pattern manifest is kept below `work/drum_patterns/` and records the pattern, deterministic render report, project-relative MIDI reference and clip/track IDs. Clip metadata and generation history retain the Drum Studio engine version and explicit symbolic/final-audio truth.

## Determinism

The same pattern, BPM and seed produce the same probability decisions, timing offsets and velocity changes. This is important for project reopening, revision comparison and repeatable exports.

## Swing

Swing delays odd grid steps while leaving even steps fixed. The pattern stores swing as a ratio between `0.50` (straight) and `0.75` (heavy swing). The generated MIDI notes retain their real beat positions rather than pretending the pattern is straight-quantised.

## Humanisation

Timing and velocity humanisation are optional and bounded. Humanisation is deterministic for the same seed. Per-hit micro timing can be combined with global humanisation, with resulting note positions clamped so they never become negative.

## Safety and bounds

The engine rejects:

- unsupported grid sizes;
- patterns longer than 64 bars;
- hits outside the declared pattern grid;
- duplicate hits on the same lane/step;
- unknown drum names unless an explicit MIDI note is supplied;
- BPM outside 20–400;
- invalid MIDI velocities, probabilities, humanisation bounds or micro shifts.

Project and membership boundaries are inherited from the existing DAW/MIDI API. The saved pattern manifest is explicitly confined to the current project.

## Existing DAW integration

The engine emits the repository's existing `MidiDocument` model and the production API inserts that document through the existing MIDI clip helpers. The result therefore flows through piano-roll editing, MIDI writing, quantise/velocity editing, generation-guide and revision systems without introducing a parallel MIDI format.

This bounded Drum Studio wave does **not** claim a rendered drum sampler, acoustic drum synthesis, exhaustive sample browser, automatic drum replacement, or final-master output. Those require real audio renderer/library work in later Music Studio waves.
