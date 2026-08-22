# Aura Music Studio — Feature Superset (August 2026)

Aura Music Studio is designed as a model-agnostic AI music production system rather than a single generator. It combines the strongest workflows found in current commercial and open music systems, while retaining local/self-hosted options and explicit rights/consent controls.

## Research baseline

### Suno / Suno Studio 2.0
Sources:
- https://help.suno.com/en/articles/13670529
- https://suno.com/pricing
- https://help.suno.com/en/articles/11362433

Observed capabilities to match or exceed:
- Text/lyrics to complete song
- Audio upload / record
- Cover, remix, extend, replace/add section
- Add vocals or instrumentals
- Custom Voices and custom model tuning
- Multitrack Studio timeline
- Chat-driven generation/editing
- Alternate/take lanes
- Track automation for volume, pan, effect parameters
- Compressor, convolution, delay, distortion, EQ, gate, reverb
- MIDI tracks, piano roll, velocities, pitch bend, modulation, quantize
- Musical typing, chord mode, arpeggiator, external MIDI
- Audio-to-MIDI and MIDI-to-generated-audio
- Wavetable synth
- Advanced stem separation
- Full project / range / multitrack export

### ACE-Step 1.5
Source: https://github.com/ace-step/ACE-Step-1.5

Capabilities used as Aura's primary open neural music engine:
- Text-to-music and lyrics-to-song
- 10 seconds to 10 minutes generation
- 1000+ styles/instrument descriptions and 50+ lyric languages
- Reference audio
- Cover
- Repaint
- Track extraction
- LEGO/add-track generation
- Complete missing tracks
- Vocal-to-BGM accompaniment generation
- Multi-track generation
- Audio understanding: BPM, key/scale, meter, caption
- Automatic lyric timestamp generation
- LoRA personalization
- Automatic quality scoring
- Batch generation / best-of-N workflows

### Udio
Sources:
- https://help.udio.com/en/articles/11649525-sessions-udio-s-timeline-editing-view
- https://help.udio.com/en/articles/11003046-styles
- https://help.udio.com/en/articles/10754328-create-music-with-your-own-audio

Capabilities to incorporate:
- Waveform-centric Sessions editor
- Extend before/after, intro/outro
- Replace/inpaint selected segment
- Edit lyrics corresponding to selection
- Take/snapshot workflow and undo
- Audio upload
- Remix and Style from uploads
- One/two-reference Style blending with influence weighting

### Loudly
Sources:
- https://www.loudly.com/music/ai-music-generator
- https://www.loudly.com/ai-song-mastering

Capabilities to incorporate:
- Producer controls: genre, subgenre, BPM, key, duration, energy, instruments, structure
- Text-to-music plus formula-driven generation
- Full tracks, stems and samples/loops/one-shots
- Remix variations
- FX
- Genre-aware mastering
- Reference-track mastering
- Audio profile / song metadata analysis

### Eleven Music
Source: https://elevenlabs.io/docs/overview/capabilities/music

Capabilities to incorporate:
- Full songs or instrumentals from natural language
- Multilingual vocals/lyrics
- Fine structural control
- Audio reference
- Post-generation section and lyric editing
- Model/finetune routing

### Mureka
Source: https://platform.mureka.ai/docs/

Capabilities to incorporate through optional API adapter:
- Prompt-to-song
- Lyrics-to-song
- Instrumental generation
- Lyrics generation
- Song extension
- Remix
- Music transcription
- Stem separation
- Complementary track generation: vocals, accompaniment or instrument
- Image/video soundtrack generation
- Fine-tuned music models

### The Muser
Source: https://github.com/noah-chelednik/the-muser

Capabilities to incorporate:
- LLM producer/orchestrator
- Tool-based composition loop
- NotaGen symbolic notation
- ACE-Step neural audio
- DiffSinger singing voice synthesis
- RVC / Seed-VC voice conversion
- Demucs separation
- Audio-to-MIDI
- EQ, reverb, compression, mixing
- Best-of-N generation
- Multi-metric quality scoring and curation
- Gradio/web workflow

### YuE
Source: https://github.com/multimodal-art-projection/YuE

Capabilities to incorporate through optional local adapter:
- Full multi-minute lyrics-to-song generation
- Vocals plus accompaniment
- Genre/language/vocal-technique prompts
- Single-track and dual-track reference audio ICL
- Continuation
- LoRA fine-tuning

### Voice systems
Sources:
- https://github.com/Plachtaa/seed-vc
- https://github.com/IAHispano/Applio
- https://github.com/openvpi/DiffSinger

Aura policy and capabilities:
- User-owned / explicitly consented voice profiles only
- Reference recording upload/record
- Singing voice conversion
- Optional fine-tuning for higher similarity
- Pitch, formant/timbre, emotion/style and similarity controls where supported
- DiffSinger score-driven singing and harmony generation
- Consent manifest stored beside every Voice Profile
- Voice profile cannot be selected for rendering until consent is recorded

### Separation and transcription
Sources:
- https://github.com/nomadkaraoke/python-audio-separator
- https://github.com/facebookresearch/demucs
- https://github.com/spotify/basic-pitch

Aura strategy:
- Auto-select source-separation backend
- Prefer BS/MelBand RoFormer for high-quality vocal/instrumental tasks when installed
- Demucs 6-source fallback: vocals, drums, bass, guitar, piano, other
- Basic Pitch audio-to-MIDI for isolated/polyphonic instrument tracks
- Stem-first transcription where possible

### Mastering / DSP
Sources:
- https://github.com/sergree/matchering
- https://github.com/spotify/pedalboard

Aura strategy:
- LUFS/true-peak mastering targets
- Genre presets
- Reference-track matching
- Per-stem EQ/compression/reverb/delay/pan/width
- Safe effect-chain designer from natural language
- Optional VST3/AU hosting only through an isolated optional DSP process

## Aura-exclusive goals

1. **Backing Track Architect** — score/reference-driven reconstruction with lead-vocal space, harmony vocals and original countermelody.
2. **Model Router** — pick the best engine for the requested job rather than forcing every task through one model.
3. **Producer Autopilot** — analyze → plan → generate multiple takes → quality gate → retry → stems → mix → master → export.
4. **Rights & Consent Ledger** — source-rights confirmation and voice-consent metadata are first-class project data.
5. **Project DNA** — structured BPM/key/meter/chords/sections/instruments/energy/loudness/stem metadata used by every agent action.
6. **DAW + Generative Timeline** — audio/MIDI tracks, take lanes, automation, effects, clips and AI actions share one project state.
7. **Voice Harmony Architect** — generate harmony parts from key/chords/lead melody, then render them through approved voice profiles.
8. **Reference Mastering + Translation Tests** — master against a reference and analyze phone/mono/low-bitrate translation.
9. **Model-independent project format** — projects remain editable even if an external model or API is replaced.
10. **Full export** — WAV/MP3/FLAC, MIDI, MusicXML where available, aligned stems, lyrics/LRC, metadata and BandLab/DAW ZIP.
