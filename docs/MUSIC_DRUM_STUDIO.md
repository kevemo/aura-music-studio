# Drum Studio — Sequencer Foundation

## Production truth

Aura Drum Studio provides deterministic editable drum-pattern control data for the existing Pulsar DAW MIDI workflow. This layer is **symbolic MIDI control/edit data**, not final rendered audio. Final music must still be rendered as real audio through the connected music engine.

## Implemented in this wave

The first production Drum Studio engine adds:

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

## Existing DAW integration

The engine emits the repository's existing `MidiDocument` model, so the output can flow into the existing MIDI writer, piano roll, quantise/velocity editing, generation-guide and revision systems without introducing a parallel MIDI format.

A later UI/API surface can expose pattern-pad editing and one-click clip insertion. This wave establishes the deterministic production engine first and deliberately does not claim a rendered drum sampler, acoustic drum synthesis or final-master output.
