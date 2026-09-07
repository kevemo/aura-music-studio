# Drum Studio — Project Workspace and DAW Insertion

## Production truth

This wave connects the existing Drum Studio sequencer engine to the existing Pulsar DAW project model without creating a second MIDI system, second membership gate or second project store.

## Implemented

- validated DrumPattern JSON persistence under the member project;
- opaque pattern identifiers with traversal/path-escape rejection;
- listing and reopening saved patterns;
- four-on-the-floor starter-pattern persistence;
- deterministic conversion through the existing Drum Studio engine;
- real `.mid` file creation through the existing Pulsar MIDI writer;
- direct insertion into the existing DAW MIDI track/clip model;
- Drum Studio provenance stored on the DAW clip and generation history;
- browser-safe response payloads that do not expose storage paths;
- fail-closed handling when probability rules yield no playable hits.

## Audio truth

A Drum Studio pattern and the generated MIDI file are **symbolic control/edit data**. They are not a final drum recording, sampler render, mix or master. Final music remains real rendered audio through the connected music/audio rendering path.

## Reuse boundary

The workspace backend deliberately reuses:

- `DrumPattern` and `pattern_to_midi_document` from `drum_studio.py`;
- the existing DAW session model;
- the existing Pulsar MIDI clip creation and MIDI writing helpers.

This shared backend is intended to support the next member-facing Drum Studio API/UI and Aura-chat drum-creation tool without duplicating persistence or clip-insertion logic.

## Out of scope for this bounded wave

This wave does not add pricing, payments, new membership entitlements, provider credentials, native-security authority, a rendered drum sampler, or a second DAW implementation.
