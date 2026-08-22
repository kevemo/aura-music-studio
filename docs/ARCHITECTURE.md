# Aura Music Studio v0.4 — Architecture

## Prime rule: final music is real waveform audio

Aura uses notation, MIDI, MusicXML, chord maps and score guides as **control data**. They may define timing, harmony, melody, voicing, automation and conditioning signals. They are never silently promoted to Final Master.

A final/exportable performance must originate from one or more of:

1. neural music/singing audio generation;
2. user-owned/licensed recorded audio;
3. a hybrid mix of real/neural waveform layers.

If a final-audio engine is unavailable, the job fails with diagnostics rather than falling back to General MIDI or SoundFont audio.

## System layers

### 1. Project + rights layer

- `models.py` — project manifest, render/mix/production contracts and real-audio gate.
- `rights.py` — source-rights attestations and consent-locked Voice Profiles.
- `assets.py` — upload library for audio, samples, scores, MIDI/MusicXML and text.
- `session.py` — persistent generative DAW session: tracks, audio/MIDI clips, take lanes, markers, effects and automation.

### 2. Musical intelligence

- `analysis.py` — tempo/key/audio analysis.
- `arrangement.py` — production/arrangement planning.
- `presets.py` — genre-aware production DNA.
- `styles.py` — weighted multi-reference Style DNA.
- `lyrics.py` — lyrics generation/refinement adapter.
- `transcription.py` — audio-to-MIDI/MusicXML control extraction.
- `harmony.py` — lead scan + harmony construction and vocal-render hooks.

### 3. Real-audio generation

- `renderers.py` — full-song engine failover and adapters.
- `cloud_providers.py` — optional hosted providers.
- `engine_manager.py` — local open-source engine bootstrap/detection.
- `layers.py` — independent real-audio layer generation.
- `samples.py` — loop, one-shot, riff, texture and transition Sample Lab.
- `editing.py` — non-destructive neural replace/repaint/extend/variation and take lanes.

Primary/local targets include ACE-Step 1.5, The Muser, YuE, DiffSinger and consent-gated Seed-VC/RVC. Hosted adapters may include deAPI/ACE-Step, Eleven Music and Mureka when credentials are configured.

### 4. Generative DAW + Producer

- `producer.py` — natural-language request -> structured studio actions.
- `mixer.py` — waveform-only multitrack/take-lane session render.
- `effects.py` — real-audio effects rack.
- `ui.py` — Gradio studio interface.
- `api.py` — FastAPI backend for desktop/web/mobile clients.

Examples:

- “Make the chorus bigger” -> generate or regenerate real-audio layers in the chorus and add them as takes.
- “Replace guitar 1:20–1:35” -> neural region render -> waveform replacement -> alternate take lane.
- “Give me a four-bar drum fill” -> Sample Lab -> neural waveform sample -> session clip.
- “Turn my vocal into three-part harmonies” -> audio transcription/control notes -> Harmony Architect -> approved singing renderer -> three separate real-audio harmony tracks.

### 5. Post-production

- `separation.py` — configurable RoFormer/UVR/audio-separator, Eleven stems, Demucs fallback.
- `mastering.py` — genre presets, reference mastering, LUFS/peak targets, translation checks.
- `audio.py` — WAV/MP3/FLAC/stems/BandLab package.
- `quality.py` — integrity/quality metrics and best-of-N gating.

## Engine abstraction

Aura project files do not depend on a single model vendor. The project keeps musical intent and session state, while engine adapters convert that intent into a provider/local-model request. Better future generators can therefore replace an engine without invalidating old Aura projects.

## Open-source engine directory

Third-party repositories and model weights belong under the git-ignored `engines/`, `models/` and `checkpoints/` directories. Run:

```bash
aura engines
aura engines --bootstrap
```

This keeps Aura's orchestration code small and avoids redistributing third-party weights/licenses inside the repository.

## API model

Start the API:

```bash
aura serve --host 0.0.0.0 --port 8000
```

Core API areas:

- projects / original-song creation
- producer planning
- project production/analyze
- session load/save/render
- asset upload/library
- sample analyze/generate/loop
- weighted style blend
- stem separation
- audio-to-MIDI control transcription
- mastering/reference mastering
- consent-gated voice profiles
- output browsing/download

## Security / provenance design

- Uploads are SHA-256 fingerprinted and linked to rights attestations.
- Voice Profiles require consent metadata before any voice-conversion/singing route is enabled.
- API keys remain in local environment/secrets and never in project manifests.
- Generated files preserve renderer/quality/mastering metadata for provenance.
- Symbolic guide audio is blocked from final export by both model validation and pipeline runtime checks.
