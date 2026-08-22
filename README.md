# Aura Music Studio

Aura Music Studio is a multi-model AI music creation and production system for realistic backing tracks, original songs, lyrics-to-song, reference/cover/remix workflows, vocal harmonies, consent-based voice profiles, stem separation, transcription, mixing, mastering and DAW-ready export.

## Real-audio guarantee

**MIDI, MusicXML, notation and score-guide audio are control data only. They are never accepted as the finished music.**

Aura may use MIDI/notation internally to preserve exact notes, chords, melody, harmony, rhythm, form and timing. A temporary guide can also condition a neural model. But `Final Master` export is permitted only when the source is:

- a neural audio music engine such as ACE-Step 1.5, The Muser, YuE, Eleven Music, Mureka or a configured compatible generator;
- real uploaded/recorded audio;
- or a hybrid production built from real/neural audio layers.

If no real-audio renderer succeeds, Aura **fails the project rather than substituting a General-MIDI/SoundFont render**.

## Core platform

- Original prompt-to-song generation
- User lyrics -> complete song
- Instrumental/backing-track generation
- Reference audio, cover, remix, repaint, extend and section workflows
- Samples, stems and track uploads
- Genre / subgenre / mood / BPM / key / meter / duration / energy / structure / instrument controls
- Score, MIDI and MusicXML guidance
- Audio-to-MIDI transcription for editing/control
- Original guitar countermelody generation
- Harmony Architect for backing vocal parts
- Consent-gated Aura Voice Profiles for approved singing voice conversion / synthesis
- Multi-model routing and renderer failover
- Best-of-N / automatic quality control and retry
- RoFormer/UVR-style and Demucs stem separation paths
- Per-track processing, automation and take-lane project model
- Adaptive mastering, reference mastering and translation checks
- WAV / MP3 / aligned stems / MIDI / notation / BandLab-ready export
- Autopilot project worker and local web UI

See `docs/FEATURE_MATRIX.md` for the researched feature superset and engine map.

## Current backing-track project

The repository includes a 107-measure, 96-BPM backing-track project under `projects/nothings-gonna-stop-us-now/`. Its symbolic score guide exists only to lock structure and harmony before a real neural audio engine renders the production.

## High-quality local rendering

For local ACE-Step / The Muser / YuE workflows, use an NVIDIA CUDA GPU appropriate to the selected model. Hosted APIs and hosted ACE-Step endpoints can also be configured through environment variables.

Run:

```bash
pip install -e .
aura doctor
aura ui
aura run projects/nothings-gonna-stop-us-now
```
