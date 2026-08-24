# Pulsar-Frequency House — Release-Grade Music + Editable Song DNA

## Product requirement

A generated song is expected to aim at a finished, professional, mastered result **and** remain deeply editable afterwards. The stereo master is a render of a structured project, not the only surviving form of the song.

The system must therefore solve two different problems:

1. **Release quality** — real decoded audio, believable performance, clean production, measured mastering and quality control.
2. **Editability** — stable lyric/section/instrument targets, stems/MIDI/session data where available, revision history and targeted regeneration.

Technical QC can reject measurable faults. It cannot honestly guarantee that a listener will judge a performance indistinguishable from a top human studio production. Pulsar-Frequency House keeps a separate perceptual acceptance checklist for that reason.

## Verified 2026 market baseline

Current official product documentation confirms that the market has moved toward generative DAWs rather than one-shot song files:

- Suno Studio 2.0: MIDI import/record/edit, piano roll, MIDI as a generation prompt, audio↔MIDI, effects, automation, wavetable synth, chat generation/plugin design, advanced stem separation and multitrack export.
  - https://help.suno.com/en/articles/13670529
  - https://suno.com/blog/studio-2
- BandLab: modern browser/mobile recording and AI vocal-cleaning workflows such as noise removal, dereverb and AutoEQ.
  - https://help.bandlab.com/hc/en-us/articles/37657276691097-Remove-Noise-with-Voice-Cleaner
- ElevenLabs: instant/pro voice cloning plus performance-preserving multilingual dubbing workflows.
  - https://elevenlabs.io/docs/eleven-creative/voices/voice-cloning
  - https://elevenlabs.io/blog/dubbing-api

Pulsar-Frequency House is designed around an original unified architecture rather than copying any proprietary UI or implementation.

## New-song release contract

`creation.py` now creates new songs with:

- real-audio-only final output;
- no symbolic/MIDI guide accepted as the finished track;
- stronger human-performance prompting;
- explicit avoidance of common AI audio artifacts;
- four candidate quality attempts (`quality_retries=3` plus the initial take);
- a default renderer quality threshold of `0.72` for newly created projects;
- 24-bit/48 kHz mastering/export path already provided by the audio finalizer;
- an `song_dna.json` file at project creation.

## Editable Song DNA

`aura_music_studio/song_dna.py` provides stable project entities:

- `SongSection`
- `LyricLine`
- `InstrumentLayer`
- `SongEditDirective`
- `SongDNA`

The DNA stores:

- title / genre / mood / language;
- BPM, key, meter and target duration;
- section structure;
- lyric lines with stable IDs and revisions;
- instrument identities and eventual track/stem/MIDI references;
- vocal mode and consent-approved voice profile reference;
- master profile;
- quality contract;
- targeted edit directives;
- latest master/stem/session synchronization metadata.

### Targeted edit operations currently modelled

- replace lyric line;
- replace instrument;
- add/remove instrument;
- regenerate/extend/shorten section;
- change key or tempo;
- change voice;
- remix;
- remaster.

The first API layer deliberately returns `render_state=planned` for edits whose renderer-specific local regeneration has not yet run. It does **not** claim a changed audio file exists simply because the metadata was edited.

## Song DNA API

`aura_music_studio/song_dna_api.py` exposes:

- `GET /projects/{project_name}/song-dna`
- `POST /projects/{project_name}/song-dna/initialize`
- `PATCH /projects/{project_name}/song-dna/lyrics/{line_id}`
- `POST /projects/{project_name}/song-dna/instruments/{layer_id}/replace-plan`
- `POST /projects/{project_name}/song-dna/sections/{section_id}/regenerate-plan`
- `POST /projects/{project_name}/song-dna/sync-session`

The next renderer orchestration phase should execute these directives against stems/regions and then rebuild transitions/mix/master only where needed.

## Release-quality technical gate

`aura_music_studio/release_quality.py` checks the final exported master for measurable conditions:

- final WAV exists;
- sample rate meets release minimum and reports 48 kHz preference;
- 24-bit preference;
- valid channel count (stereo preference, intentional mono allowed);
- peak ceiling;
- integrated loudness proximity to the project target;
- existing renderer QC score/integrity;
- editable Song DNA exists;
- stem delivery warning when unavailable;
- translation warnings (stereo width, sub-bass, presence, high-frequency balance).

The pipeline writes:

`output/release_quality_report.json`

and refuses a technically invalid master when `AURA_RELEASE_GATE_STRICT=true` (default).

## Perceptual release checklist

A technical pass is not the same thing as a perceptual pass. Before describing a track as release-ready, the QA workflow should evaluate:

- stable, intelligible and emotionally believable lead vocals;
- instruments that sound performed rather than generic General MIDI;
- intentional groove with no accidental timing drift;
- believable articulations, attacks, decays and transitions;
- no obvious AI warble, metallic ringing, phasey doubling, transient smearing or lyric corruption;
- stable vocal/instrument identity across sections;
- intentional arrangement density and frequency space;
- translation across headphones, phone/laptop speakers and full-range playback.

Future work should add a blind-listening benchmark harness and project-specific human approval state so `release_ready` can become true only after the configured perceptual QA path.

## Next build phase

1. Implement renderer execution for `SongEditDirective` using local/regional generation.
2. Map lyric lines to time/phoneme regions for surgical lyric replacement.
3. Attach generated/exported stems to `InstrumentLayer` reliably rather than filename heuristics alone.
4. Add audio↔MIDI transcription and MIDI-as-generation-input workflow.
5. Build DAW UI around Song DNA: section strip, lyric lane, instrument inspector, lock/preserve controls and Aura edit bar.
6. Add A/B generation candidates with waveform audition before committing a replacement.
7. Add listening-test/perceptual QA workflow and reference-track comparison.
8. Expand vocal identity controls with consent-approved voice conversion/cloning adapters.
