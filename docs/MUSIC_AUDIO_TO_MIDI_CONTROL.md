# Audio-to-MIDI Control Transcription

## Production truth

Aura's Audio-to-MIDI feature converts real uploaded audio into editable symbolic MIDI control data for the existing DAW piano roll, performance-guide workflow, harmony/scoring workflows and downstream generation guidance.

The MIDI output is **not final audio**. It is an edit/control layer. Final music must still be produced or rendered as real audio.

## Existing integration

The production repository already provides:

- project Asset Library audio ingest with SHA-256 identity and rights/provenance records;
- `POST /projects/{project_name}/transcribe` for audio Asset Library items;
- automatic transcription for `hum`, `melody` and `instrument` Performance Inputs;
- rhythm/beatbox onset-to-drum-guide MIDI;
- DAW MIDI import from Performance Inputs;
- editable MIDI piano roll, velocity, pitch bend, CC, transpose and quantise tools;
- MIDI-as-generation-guide integration that preserves note contour, rhythm, note lengths and velocity dynamics.

This wave hardens the shared transcription engine rather than creating a duplicate MIDI subsystem.

## Transcription modes

`audio_to_midi(..., mode=...)` supports:

- `auto` — use Basic Pitch when that optional runtime is available, otherwise use Aura's pYIN monophonic transcription;
- `polyphonic` — require Basic Pitch and fail closed when the runtime is unavailable or does not produce valid MIDI;
- `monophonic` — use pYIN for isolated vocals, hums, melodies and monophonic instruments.

Aura must never label a pYIN fallback as successful polyphonic transcription.

## Performance-aware monophonic MIDI

The monophonic path now:

- rejects empty and silent audio;
- preserves detected phrase timing rather than hard quantising it;
- suppresses single-frame pitch chatter with a short rolling pitch median;
- rejects very short unstable note fragments using a bounded minimum-note duration;
- derives MIDI velocity from source RMS dynamics instead of writing every note at a fixed velocity;
- retains pYIN voicing confidence in the provenance report;
- writes a normal editable `.mid`/`.midi` file compatible with the existing DAW.

## Performance Input provenance binding

Performance Inputs now consume the transcription provenance instead of only storing a MIDI path.

- `hum` and `melody` inputs explicitly request `monophonic` mode so the product does not accidentally overclaim polyphonic transcription.
- `instrument` inputs request `auto`, allowing the optional polyphonic-capable runtime when available while preserving the truthful pYIN fallback.
- the detected Performance Input BPM is passed into transcription so MIDI timing is aligned to the analysed source rather than a fixed default tempo;
- the sidecar source SHA-256 is checked against the actual project audio before the MIDI reference is accepted;
- engine, requested mode, source/output hashes and note count are persisted in `PerformanceInput.metadata`;
- Performance Input metadata repeats `midi_symbolic_guide_only: true` and `midi_final_audio: false` so downstream DAW/import surfaces cannot mistake the control layer for a release master.

Rhythm and beatbox guides also carry explicit guide-only/final-audio-false metadata.

## Provenance sidecar

Every successful transcription writes a sibling `<name>.mid.aura.json` sidecar containing:

- transcription engine and Aura engine version;
- requested transcription mode;
- source SHA-256;
- output MIDI SHA-256;
- working BPM;
- note count and pitch range;
- velocity statistics;
- whether the engine is polyphonic-capable;
- whether source-dynamic velocity tracking was applied;
- explicit `symbolic_guide_only: true` and `final_audio: false` truth flags.

The sidecar is project-local control/provenance data and does not promote MIDI to a release master.

## Safety and bounds

The engine validates transcription mode, MIDI output extension, onset threshold, BPM and minimum note duration. Explicit polyphonic requests fail closed if the required optional engine is missing. Temporary Basic Pitch output directories are unique per invocation and are removed after use.

Rights and provenance admission remains the responsibility of the existing Asset Library / Performance Input ingest layers. This wave does not weaken those boundaries and does not add a new commercial entitlement or provider credential path.

## Scope boundary

This closes the production-quality Audio-to-MIDI control-transcription layer for existing DAW and Performance Input workflows. It does not claim perfect source separation, perfect transcription of arbitrarily dense mixes, or that symbolic MIDI is equivalent to rendered audio.
