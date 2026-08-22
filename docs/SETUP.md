# Aura Music Studio — Setup

Aura is an orchestration/production system. The core installs on CPU, while high-quality local neural generation needs an appropriate NVIDIA CUDA GPU for the selected model. Hosted real-audio providers can be used instead.

## 1. Core install

```bash
git clone https://github.com/kevemo/aura-music-studio.git
cd aura-music-studio
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
```

Linux/macOS:

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

Install FFmpeg separately and make sure `ffmpeg` and `ffprobe` are on PATH.

## 2. Optional production tools

```bash
pip install -e '.[all-audio]'
```

This enables the packaged integration layer for:

- Demucs separation
- audio-separator / RoFormer-style separation
- Basic Pitch transcription
- Matchering reference mastering

These tools do not replace a full-song neural generator.

## 3. Configure secrets and engine commands

```bash
cp .env.example .env
```

Add only the credentials/commands you choose to use. Never commit `.env`.

### Hosted examples

- `DEAPI_API_KEY` — hosted ACE-Step route
- `ELEVENLABS_API_KEY` — optional music/stem route
- `MUREKA_API_KEY` — optional music/vocal route

### Local model command adapters

Aura does not hard-code one vendor CLI because these projects change quickly. Instead it supplies standardized environment variables to adapter commands:

- `AURA_LOCAL_RENDER_CMD` — ACE-Step/local full-song renderer
- `AURA_MUSER_CMD` — The Muser
- `AURA_YUE_CMD` — YuE
- `AURA_REGION_RENDER_CMD` — repaint/replace/extend
- `AURA_LAYER_RENDER_CMD` — complementary instrument/vocal layer
- `AURA_SAMPLE_RENDER_CMD` — loops, one-shots, riffs and textures
- `AURA_DIFFSINGER_CMD` — score-driven singing/harmony audio
- `AURA_SEEDVC_CMD` / `AURA_RVC_CMD` — consent-approved singing voice conversion

Every final-audio command must write a real waveform file to `AURA_OUTPUT`.

## 4. Bootstrap open engines

Inspect the engine registry:

```bash
aura engines
```

Clone supported repositories into the git-ignored `engines/` directory:

```bash
aura engines --bootstrap
```

Optionally install packaged utilities as well:

```bash
aura engines --bootstrap --install-packages
```

Model checkpoints remain under engine-specific/model directories and are not committed to Aura's repository.

## 5. System check

```bash
aura doctor
```

Doctor reports:

- CUDA/NVIDIA visibility
- FFmpeg and notation tools
- configured local neural renderers
- authenticated hosted renderers
- public ACE-Step fallback Spaces
- stem/transcription/mastering tools
- voice/harmony renderers
- real-audio policy state

Public ZeroGPU hosts are explicitly marked best-effort. A public Space being configured does not mean a GPU slot is guaranteed.

## 6. Launch the studio

Web studio:

```bash
aura ui --host 0.0.0.0 --port 7860
```

REST API:

```bash
aura serve --host 0.0.0.0 --port 8000
```

OpenAPI documentation is available from FastAPI at `/docs` while the API is running.

## 7. Create an original song

```bash
aura create-song \
  --title "My Song" \
  --concept "an uplifting rock song about starting again" \
  --genre rock \
  --duration 240
```

Or use the Create Song tab to enter/paste lyrics, select genre, instruments, BPM/key, vocals and authorized references.

## 8. Produce a project

```bash
aura run projects/my-song
```

Aura performs:

1. project/reference analysis;
2. arrangement planning;
3. guide/control preparation where needed;
4. real neural/recorded audio rendering;
5. best-of-N quality gate;
6. stem separation;
7. mastering and translation analysis;
8. WAV/MP3/FLAC/stem/BandLab export.

If only MIDI/SoundFont/score-guide audio is available, step 4 fails by design.

## 9. Backing tracks from scores/references

Add authorized audio/score/MIDI/MusicXML through the Upload Library or project input directory. Symbolic information can lock exact form/chords/melody while ACE-Step/The Muser/another real-audio engine performs the audible band.

The workflow is:

```text
score / MIDI / MusicXML / authorized reference
              ↓
     musical timing + harmony guide
              ↓
       REAL neural audio renderer
              ↓
  stems → mix → master → DAW/BandLab pack
```

## 10. Voice profiles

Use Voice Studio only for your own voice or a person who has explicitly authorized the use. Aura stores consent metadata and blocks unapproved profiles from voice-conversion routes.

## 11. Sample Lab

Configure `AURA_SAMPLE_RENDER_CMD` (or `AURA_LAYER_RENDER_CMD`) for neural sample generation. Aura can also analyze/slice/time-fit uploaded waveform samples and loops. Generated samples are waveform audio; MIDI cannot satisfy the Sample Lab final-output contract.

## 12. Development and CI

```bash
pip install -e '.[dev]'
pytest -q
python -m compileall -q aura_music_studio
```

GitHub CI also runs a hard guard verifying symbolic guide audio cannot be enabled as Final Master.
