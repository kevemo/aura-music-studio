# ESP Live Sound Studio — Live Neural Renderers

The Live Sound Studio is real-audio-first. MIDI, MusicXML, SoundFonts and symbolic score guides are control/reference data only and cannot satisfy a Final Master render.

## Primary renderer: ACE-Step 1.5

ESP pins ACE-Step 1.5 to upstream commit `14c0211d5a0653b0f63e27686f4c3f151b4d8629`. The upstream Docker image provides a REST API on port 8001 with `/health`, `/v1/models`, `/release_task`, `/query_result` and `/v1/audio` endpoints. The ESP overlay exposes that port only to the private Compose network.

ACE-Step is the default engine for:

- text/lyrics to full song
- upload-conditioned cover/remix/backing-track work
- Build Around Upload / Complete
- Lego generated instrument or vocal tracks
- Extract
- repaint/inpainting

Start the core Studio with the private ACE-Step GPU renderer:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

The first start can take substantial time because model/checkpoint files must be downloaded and initialized. Model/cache volumes are persistent, so they are not intentionally downloaded again on every restart.

## Optional second renderer: YuE

YuE is pinned to upstream commit `9f1394bae1d8d218fea750c1413c2d9d731c7310` and runs in a separate CUDA/Python container. It is a lyrics-first renderer, not the default upload-conditioned engine.

The ESP YuE service:

- exposes no host/public port
- accepts one GPU generation job at a time
- uses an optional private bearer credential (`YUE_API_KEY`)
- stores heavyweight Hugging Face model cache in a persistent volume
- writes job output into an isolated renderer volume
- normalizes the selected final mix to stereo 48 kHz / 24-bit WAV before the Studio receives it
- does not mount the ESP member database, member project volume, PayPal configuration or SMTP configuration

Start Studio + YuE only:

```bash
docker compose -f docker-compose.yml -f docker-compose.yue.yml up -d --build
```

Start both real-music engines:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.gpu.yml \
  -f docker-compose.yue.yml \
  up -d --build
```

YuE is resource-heavy. Its own project documentation recommends low segment counts for GPUs with 24 GB VRAM or less and much larger (~80 GB-class) GPU capacity for many-section full-song generation. ESP therefore defaults `AURA_YUE_MAX_SEGMENTS=2`; ACE-Step remains the normal full-song engine on consumer GPUs.

## Renderer truth / health

Authenticated members can inspect a redacted renderer status through:

```text
GET /system/renderers
```

It reports whether a real Final Master renderer is actually ready, which engine is primary, and whether the optional engines are reachable/configured. It deliberately does not reveal private service URLs or API credentials.

When a live GPU overlay is used, `AURA_REQUIRE_LIVE_RENDERER=true` is forced into the Studio and queue worker. A Final Master pipeline then fails immediately if no real renderer is reachable/configured.

Even after a renderer reports success, Aura independently probes the returned file. Symbolic files, missing files, zero/invalid audio, undecodable payloads and too-short waveform output are rejected before quality scoring or mastering.

## Hardware requirements

The software path does not require Suno/Udio subscriptions or a paid music-generation API. Actual neural inference still consumes physical GPU resources, electricity, storage and initial model-download bandwidth.

For local/self-hosted NVIDIA execution install:

1. NVIDIA driver compatible with the selected CUDA stack.
2. Docker Engine / Docker Desktop.
3. NVIDIA Container Toolkit with Docker GPU access.
4. Enough disk space for ACE-Step/YuE model caches.

Confirm Docker can see the GPU before starting the renderer stack:

```bash
docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi
```

An ESP Compute Node can host the heavy renderer instead of the public web machine. That keeps the website/account server lightweight while generation is performed on another ESP-controlled GPU computer.

## Secrets

Use `deploy/renderers.env.example` as the settings reference. Put real values only into the deployment `.env`/secret store. Never commit `ACESTEP_API_KEY`, `YUE_API_KEY`, Hugging Face credentials or any other private token.

## Licence policy

ACE-Step code and YuE code/model terms must be tracked separately from generated-output rights and from any third-party input/reference rights. The Studio model catalogue remains the authority for deployment approval; adding a repository does not automatically mark every checkpoint commercially unrestricted.
