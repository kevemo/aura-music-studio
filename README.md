# Aura Music Studio

**Aura Music Studio v0.4** is a real-audio-first, model-agnostic AI music creation and production platform. It is being designed as a generative DAW/orchestrator rather than a single text-to-song model: realistic backing tracks, original songs, lyrics-to-song, reference/cover/remix workflows, sample generation, vocal harmonies, consent-based voice profiles, stem separation, multitrack editing, mixing, mastering and DAW-ready export all share one project state.

## Real-audio guarantee

**MIDI, MusicXML, notation, SoundFonts and score-guide audio are control data only. They are never accepted as the finished music.**

Aura may use symbolic data internally to preserve exact notes, chords, melody, harmony, rhythm, form and timing. A guide may condition a neural model. `Final Master` export is permitted only when the source is:

- a neural music/singing audio engine such as ACE-Step 1.5, The Muser, YuE, Eleven Music, Mureka or another configured compatible generator;
- real uploaded/recorded audio;
- or a hybrid production built from real/neural waveform layers.

If no real-audio renderer succeeds, Aura **fails the project rather than substituting a General-MIDI/SoundFont render**.

## Platform capabilities

### Create

- Prompt -> original full song
- User lyrics -> complete song
- AI-assisted lyric creation
- Instrumental and karaoke/backing-track creation
- Genre/subgenre/mood/energy/BPM/key/meter/duration/structure controls
- Genre-aware production DNA: instrumentation, arrangement, drum/bass/vocal behavior and mastering target
- Authorized reference audio
- Weighted multi-reference **Style DNA** with independent rhythm/production/harmony/instrumentation influence
- Multilingual/vocal-engine routing where supported

### Generative DAW

- Persistent multitrack `StudioSession`
- Waveform audio clips plus symbolic control clips
- Alternate **take lanes**
- Non-destructive replace / repaint / variation / extend
- Neural complementary-layer generation
- Real-audio multitrack mix engine
- Track fader, pan and effect racks
- Automation-capable session schema
- **Aura Producer Chat**: natural-language request -> structured studio operation plan
- Project DNA and generation history

### Sample Lab

- Upload and fingerprint loops, one-shots, riffs and textures
- Analyze duration/BPM/key hint/level/loop boundaries
- Slice waveform regions
- Time-fit real audio to exact bars/BPM
- Neural loop / one-shot / fill / riff / texture / transition generation
- Generated Sample Lab outputs must be real waveform audio

### Vocals + harmony

- Harmony Architect
- Audio lead scan -> editable note/control data
- Diatonic harmony planning
- DiffSinger-compatible scored harmony rendering
- Consent-gated **Aura Voice Profiles**
- Seed-VC / RVC-style singing conversion adapters for approved voices
- Voice reference analysis and provenance metadata

### Audio tools

- Advanced source-separation routing: custom/RoFormer/UVR/audio-separator -> optional Eleven stems -> Demucs fallback
- Basic Pitch / pYIN audio-to-MIDI **control transcription**
- Genre-aware mastering
- Reference mastering via Matchering when installed
- LUFS/true-peak targets
- Phone/mono/translation analysis
- Real-audio effects rack
- WAV / MP3 / FLAC / aligned stems / BandLab pack exports

### Engines + autonomy

- ACE-Step 1.5
- The Muser
- YuE
- DiffSinger
- Seed-VC/RVC adapters
- deAPI / hosted ACE-Step
- Eleven Music adapter
- Mureka adapter
- Best-of-N generation / integrity and quality gates
- Renderer failover
- Autopilot project worker
- Local engine manager (`engines/` stays outside Git history)
- FastAPI backend for future desktop/mobile/web clients

## Start here

Core:

```bash
pip install -e .
aura doctor
aura ui
```

Optional audio production stack:

```bash
pip install -e '.[all-audio]'
```

Inspect/bootstrap local open-source engines:

```bash
aura engines
aura engines --bootstrap
```

REST API:

```bash
aura serve --host 0.0.0.0 --port 8000
```

Create an original song project:

```bash
aura create-song --title "My Song" --concept "uplifting live-band rock" --genre rock --duration 240
```

Produce a project:

```bash
aura run projects/my-song
```

## Current backing-track project

The repository includes a 107-measure, 96-BPM backing-track project under `projects/nothings-gonna-stop-us-now/`. Its score-derived guide exists only to lock form/harmony before a real neural audio engine renders the audible production.

## Documentation

- [`docs/FEATURE_MATRIX.md`](docs/FEATURE_MATRIX.md) — researched feature/engine superset
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — backend, DAW, engine and provenance architecture
- [`docs/SETUP.md`](docs/SETUP.md) — installation, engines, GPU/hosted routes and operation

## Hardware

The Aura core/API/UI can run without a GPU. High-quality local neural generation depends on the chosen engine/model and normally requires a suitable NVIDIA CUDA GPU. Hosted authenticated providers can be configured instead. Public ZeroGPU/Space endpoints are treated as **best-effort fallback**, not guaranteed production capacity.
