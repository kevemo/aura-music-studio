# Elevate Souls Productions Presents: The Live Sound Studio

## Music Making for Professionals · Powered by Aura

**The Live Sound Studio v0.12.0** is ESP's real-audio-first AI music creation, recording and production platform. **Aura** is the internal autonomous producer/orchestrator.

The project is being built as a generative DAW and self-hostable music service rather than a single text-to-song endpoint. It combines original-song creation, backing tracks, upload-to-full-production, recording, consent-gated vocal tools, stem separation, take lanes/comping, effects, tuning, mixing, mastering, project history, memberships, owner administration, independent public addressing and owner-controlled migration in one tenant-isolated application.

## Real-audio guarantee

**MIDI, MusicXML, notation, SoundFonts and score-guide audio are control data only. They are never accepted as the finished music.**

Aura may use symbolic information internally to preserve notes, chords, melody, harmony, rhythm, form and timing. Final audible exports must originate from:

- a neural music/singing waveform engine;
- real recorded/uploaded audio;
- or a hybrid production constructed from real/neural waveform layers.

If a real-audio renderer is unavailable, the Studio fails the render instead of silently substituting General MIDI or a SoundFont master.

## Current product architecture

### Public product + membership

- ESP-branded landing page, pricing, sign-up and sign-in
- exact ESP logo packaged with the app
- shared black/cosmic-purple, gold, magenta, silver and ruby visual identity
- public SEO/use-case pages, sitemap and robots controls
- installable PWA manifest with a public-only cache allow-list
- membership requests routed to `elevatesoulsproductions@gmail.com`
- Free / Base / Pro server-side entitlements
- ESP owner approval dashboard
- PayPal manual-payment bridge + verified 31-day billing periods
- Base daily confirmed-track accounting
- private per-member project namespaces
- account/data export and deletion tooling

### Membership progression

**Free — $0**

Basic creative access, Aura Producer, songwriting/lyric assistance, starter instruments/FX, basic previews and entry-level Studio functions.

**Base — $4.99/month**

One confirmed full track per day, with regenerations of that draft until the member confirms the result. Includes the core full-song/backing-track workflow, WAV/MP3 finished exports, browser recording, Build Around Upload, standard Aura Tune/FX/AutoMix, useful mastering and reduced splitter modes.

**Pro — $9.99/month**

Unlimited full-song production and the complete enabled Studio: editable multitrack Build Around, full splitter/stems, detailed downloads, Take Manager, phrase comping, automation, advanced instrument variants, Aura FX Designer, trusted plugin rack, advanced/custom Aura Tune, reference/album mastering, Sample Lab, Style DNA, generative DAW edits, consent-approved voice tools, spatial/video/tone engineering and priority production jobs.

## Music creation

- Prompt → original complete song project
- User lyrics → full song
- local/self-hosted lyric-generation route
- instrumental and backing-track creation
- genre/subgenre/mood/energy/BPM/key/meter/duration controls
- genre-aware production DNA
- structural song-section control
- authorized reference audio
- weighted multi-reference Style DNA
- model-independent production manifests

## Build Around Upload

Upload or record a performance and let Aura create the rest of the production around it.

Examples:

- **lead vocal → full band** while preserving the uploaded vocal as the anchor;
- **guitar → drums/bass/keys/other instruments + optional original lead/backing vocals**;
- piano, bass, drums, synth, strings and other instrument roles can also anchor the production.

Base can create the completed production. Pro can create separate editable generated stems/tracks in `aura_session.json`.

## Recording Studio

Base and Pro can record directly in the browser:

- microphone/input-device selection
- vocal/instrument role
- BPM + metronome
- count-in
- input level meter
- record/stop/discard/preview
- dry-capture workflow
- browser-format decoding and normalization to **24-bit / 48 kHz WAV**
- automatic private asset-library registration and rights attestation

## Instrument switchboard

Aura's arrangement model supports selectable instrument families/performance types rather than a single generic `guitar` or `drums` prompt, including multiple guitar, bass, drum-kit, keyboard/piano, synth, strings, brass, woodwind, percussion and vocal-ensemble variants.

Advanced performance types are tier-gated.

## Aura Tune + effects

- Natural / Classic / Hard / Robot / custom-scale tuning modes
- key/scale controls
- intensity, retune speed and humanization
- built-in offline correction fallback
- optional professional local tuning backend
- vocal, guitar, bass, drum and creative FX preset banks
- pedal/modulation-style processors
- AI FX Designer constrained to approved DSP types
- trusted owner-approved VST/LV2/plugin catalog hooks for Pro

## Generative DAW

- persistent `StudioSession`
- real waveform clips + symbolic control clips
- non-destructive region replace/repaint/extend
- generated complementary tracks
- automation curves
- fader/pan/effect racks
- **take lanes**
- newest generated take becomes the default audition
- choose an older whole take
- phrase-level comping from multiple takes
- previous takes remain intact
- project/session revision history and restore

## Mixing, splitter and mastering

- genre-aware editable AutoMix
- 2-, 4-, 6-stem and detailed separation routes
- advanced separator → audio-separator/Demucs fallbacks
- background splitter jobs
- mastering character presets
- LUFS/true-peak control
- EQ/stereo-width controls
- reference mastering when the local backend is installed
- album/EP consistency workflow
- translation/mono checks
- background mastering/tuning/restoration/spatial jobs

## Vocals + harmony

- Harmony Architect
- contextual ACE-Step backing-vocal generation
- scored harmony control paths
- consent-gated Aura Voice Profiles
- Seed-VC/RVC-style approved voice adapters
- voice provenance/rights records

The Studio does not provide unrestricted impersonation tooling. Voice conversion is designed around the voice owner's consent or explicit authorization.

## Aura speech + Internet

- offline-first speech-to-text hooks
- offline/self-hosted text-to-speech hooks
- spoken Aura producer commands
- controlled HTTPS Web Gateway
- SSRF/private-network blocking
- self-hosted SearXNG support for free public-web search
- local-first reasoning through Ollama-compatible services

Commercial search/chat APIs are not required by the core architecture.

## Public discovery

Once ESP gives Aura a reachable hostname or public IP, the app already exposes public search-focused pages for:

- `/ai-music-studio`
- `/ai-song-generator`
- `/backing-track-maker`
- `/stem-splitter`
- `/ai-mastering`
- `/ai-vocal-studio`

`robots.txt` and `sitemap.xml` expose only the intended public marketing surfaces. Member dashboards, projects, owner tools, membership actions and private audio routes are deliberately excluded. The service worker caches only a small public allow-list and never caches member/project/payment routes.

## Self-hosted public access — no paid domain required

The v0.12 stack includes **Aura Public Address Manager**. Cloudflare is not part of this deployment path.

Supported modes:

- local-only;
- direct public IP;
- FreeDNS/afraid.org free hostname;
- DuckDNS free hostname.

Aura can monitor the current public address, maintain the selected DDNS record, inspect router UPnP where available, warn about likely CGNAT and report HTTPS/DNS readiness in the ESP owner dashboard.

Caddy is included as the optional locally run public reverse proxy/TLS terminator. With a real free hostname that resolves to the Studio host and reachable ports 80/443, it can manage browser-trusted HTTPS automatically.

The main FastAPI service binds only to `127.0.0.1:8000` on the host in the default Docker configuration; public traffic enters through the Caddy profile instead of exposing the application server directly.

See [`docs/SELF_HOSTING.md`](docs/SELF_HOSTING.md).

## Secure self-host initialization

From a Git checkout, before installing the package:

```bash
python scripts/setup_self_host.py --provider direct
```

Or, after installing the package:

```bash
aura self-host-init --provider direct
```

For DuckDNS:

```bash
aura self-host-init --provider duckdns --duckdns-subdomain esp-live-sound-studio
```

For FreeDNS:

```bash
aura self-host-init --provider freedns --hostname your-selected-free-host.example
```

The initializer generates the ESP owner-admin/provenance secrets itself. DDNS tokens are deliberately **not accepted on the command line**, so they do not land in shell history.

Then:

```bash
docker compose --profile public up -d --build
```

Convenience launchers are also included:

```bash
bash scripts/start_self_host.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_self_host.ps1
```

## Aura public-address commands

```bash
aura public-address --refresh
aura public-address --status-only
aura public-address --serve
```

Full LAN/router address information is shown only in the authenticated ESP owner dashboard. Member-facing diagnostics are redacted.

## Owner-controlled backup and migration

Accounts, billing state, production jobs and private project files can be exported into a checksummed portable archive without a cloud backup provider.

```bash
aura backup
aura backup-inspect backups/ESP_Live_Sound_Studio_....zip
```

Optional standard `age` encryption:

```bash
aura backup --age-recipient age1...
```

Restore is intentionally offline-only:

```bash
aura restore-backup backup.zip --offline-confirmed
```

The archive excludes `.env`, DDNS credentials, SMTP passwords, payment/provider secrets and model API keys. Every archived file is SHA-256 verified before restore, and the previous database/project tree is preserved by default. ESP owners can also create/list/download backups from `/owner/backups`.

Docker stores backups in a dedicated persistent volume separate from member project/output storage.

## Core development install

```bash
pip install -e .
aura doctor
aura serve
```

Optional audio production stack:

```bash
pip install -e '.[all-audio]'
```

Optional Python UPnP integration:

```bash
pip install -e '.[selfhost]'
```

The Docker image also includes the local `upnpc` utility so router-first address inspection can work without that Python optional extra. It also includes the standard `age` command for optional owner-encrypted backups.

## Self-hosted neural generation

The Studio orchestrator prefers a self-hosted **ACE-Step 1.5 REST worker**. Open/local engine adapters also exist for additional music, singing, separation and audio systems.

MIDI/notation can control neural performances but cannot become Final Master.

GPU compute is a physical resource. The software can avoid paid generation APIs, but truly unlimited public generation is limited by the GPU hardware actually available to ESP.

## Documentation

- [`docs/FEATURE_MATRIX.md`](docs/FEATURE_MATRIX.md) — feature/engine research matrix
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — backend/DAW/engine architecture
- [`docs/PRODUCT_DEPLOYMENT.md`](docs/PRODUCT_DEPLOYMENT.md) — accounts/billing/self-host deployment
- [`docs/SELF_HOSTING.md`](docs/SELF_HOSTING.md) — domain-independent £0-additional-hosting deployment path
- [`docs/SETUP.md`](docs/SETUP.md) — engine/setup notes

---

**Product:** ESP Live Sound Studio  
**Presented by:** Elevate Souls Productions  
**AI producer:** Aura  
**Tagline:** Music Making for Professionals  
**Current code milestone:** v0.12.0
