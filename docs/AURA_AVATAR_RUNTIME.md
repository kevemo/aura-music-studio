# Aura Embodied Host Runtime

Pulsar-Frequency House treats Aura's visual body as a presentation layer over the same audited Aura Core used by text, voice and Studio tools. The avatar never creates a second hidden execution path.

## Current software state

The repository now contains both the embodied-host state bus **and a real browser GLB renderer** in `aura_music_studio/aura_avatar_runtime.py`.

Supported Aura states:

- `idle`
- `welcoming`
- `listening`
- `thinking`
- `tool_running`
- `speaking`
- `celebrating`
- `warning`
- `recording_coach`
- `studio_engineer`

Browser consumers subscribe to `aura:state` or `window.AuraHost.on(...)`. Text chat, microphone input, hands-free Voice Conversation and tool-status UI drive this same event contract.

The GLB renderer uses Google's `<model-viewer>` web component. The default renderer module is pinned to 4.3.1 rather than floating to `latest`. Operators may replace the module URL with a same-origin vendored build or another approved public HTTPS copy.

When a rig is installed the browser:

1. loads the authenticated Aura GLB;
2. exposes camera controls;
3. reads `availableAnimations` from the rig;
4. maps Aura states to the closest matching animation clip (`idle`, `welcome`, `listen`, `think`, `speak`, etc.);
5. reports a client `aura:3d-ready` event only after the model load event;
6. falls back to the cosmic Aura Core state visual if the renderer/module/model fails.

The cosmic host visual remains a fallback and is **not** described as the finished photoreal Aura character.

## Production readiness contract

Aura reports distinct status values:

1. `software_runtime_connected` — the Aura state/event software exists.
2. `renderer_implemented` — GLB rendering code exists in source.
3. `renderer_configured` — an approved same-origin/HTTPS renderer module is configured.
4. `model_installed` — a real `.glb` exists under the configured Aura asset root.
5. `operator_validated` — the deployment operator has explicitly marked this renderer/model combination validated after browser/device testing.
6. `production_3d_ready` — true only when Aura is enabled and all required renderer/model/validation conditions are satisfied.

A `.glb` on disk alone does not make Aura production-ready, and a configuration flag alone cannot make a missing renderer/model look live.

## Rigged model requirements

The canonical production Aura model should be glTF 2.0 / GLB and contain:

- production-quality humanoid skeleton;
- stable neutral rest pose;
- facial blendshapes/morph targets suitable for expressions and speech;
- viseme set or audio-driven mouth-animation mapping;
- eye blink, gaze and head-look controls;
- hair/clothing deformation suitable for the renderer;
- browser-appropriate material and texture sizes;
- LODs or equivalent desktop/mobile performance tiers;
- named animation clips for idle, welcome, listen, think, speak, gesture, celebrate, warn, recording coach and studio engineer modes;
- deterministic scale/orientation and documented root bone;
- no unlicensed textures, meshes, mocap, voice or likeness data.

The current `<model-viewer>` phase handles GLB presentation and animation-state playback. The next advanced renderer phase adds direct facial/viseme morph driving, gaze blending, layered gestures and higher-fidelity realtime performance control.

## Deployment variables

```env
AURA_AVATAR_ENABLED=true
AURA_AVATAR_ASSET_DIR=aura_music_studio/static/aura
# Blank uses <asset dir>/aura.glb
AURA_AVATAR_MODEL_PATH=

# Default when omitted: pinned official model-viewer 4.3.1 module.
# Set a same-origin URL for a fully self-hosted/vendored deployment.
AURA_AVATAR_RENDERER_MODULE_URL=https://ajax.googleapis.com/ajax/libs/model-viewer/4.3.1/model-viewer.min.js

# Keep false until the actual rig + renderer combination is tested and approved on the deployment.
AURA_AVATAR_3D_RENDERER_READY=false
```

`AURA_AVATAR_MODEL_PATH` is constrained to `AURA_AVATAR_ASSET_DIR`; traversal/outside paths are rejected. Renderer module URLs must be either same-origin absolute paths beginning with `/` or public HTTPS URLs.

## Routes

- `GET /aura-intelligence/api/avatar/status` — authenticated runtime/model/renderer/validation state.
- `GET /aura-intelligence/avatar/model.glb` — authenticated GLB delivery; 404 until a valid model is installed.
- `GET /aura-intelligence/avatar-runtime.js` — Aura state bus plus GLB browser renderer.

## Voice integration

Hands-free Aura Voice keeps one audited path:

`microphone → membership-gated STT → canonical Aura streaming/tool turn → saved assistant message → membership-gated TTS`

with visual states:

`listening → thinking/tool_running → speaking → listening/idle`.

The character renderer can animate those states but cannot authorize project writes, infer rights/consent, elevate ESP access or claim an unavailable model/service succeeded.

## Remaining production asset phase

The canonical Aura artwork still needs conversion into the actual production rig: modelling/retopology, PBR materials, hair/clothing setup, humanoid rigging, facial blendshapes/visemes, animation clips, LOD optimization and final GLB export. That binary asset should be deployed through a binary-capable artifact/model store or deployment volume rather than embedded as source text.
