# Aura Embodied Host Runtime

Pulsar-Frequency House treats Aura's visual body as a presentation layer over the same audited Aura Core used by text, voice and Studio tools. The avatar must never create a second hidden execution path.

## Current software state

The repository contains the embodied-host state bus and browser dock in `aura_music_studio/aura_avatar_runtime.py`.

Supported states:

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

Browser consumers subscribe to the `aura:state` event or `window.AuraHost.on(...)`. The text chat, normal microphone path, hands-free Voice Conversation and tool-status UI can drive the same states.

The built-in cosmic host visual is a temporary Aura Core state visualization. It is **not** described as the finished photoreal 3D character.

## Production readiness contract

Aura reports separate status values:

1. `software_runtime_connected` — state/event software exists.
2. `model_installed` — a real `.glb` asset exists under the configured private asset root.
3. `renderer_connected` — the production WebGL/3D renderer integration has been deliberately enabled after validation.
4. `production_3d_ready` — true only when the avatar is enabled and both the model asset and renderer are ready.

A `.glb` file on disk alone must never turn the UI into “production 3D ready”.

## Rigged model requirements

The canonical production Aura model should be exported as glTF 2.0 / GLB and should contain:

- production-quality humanoid skeleton;
- stable neutral rest pose;
- facial blendshapes/morph targets suitable for expression and speech;
- viseme set or an audio-driven mouth-animation mapping;
- eye blink, gaze and head-look controls;
- hair/clothing deformation appropriate to the chosen renderer;
- sensible material/texture sizes for browser delivery;
- LODs or equivalent performance tiers for desktop/mobile;
- animation clips or mappings for at least idle, welcome, listen, think, speak, gesture, celebrate and warn;
- deterministic scale/orientation and documented root bone;
- no unlicensed textures, models, mocap, voice or likeness data.

The production renderer should map Aura state events to animation layers rather than embedding AI/tool logic inside the character renderer.

## Deployment variables

```env
# Aura embodied host
AURA_AVATAR_ENABLED=true
# Root that may contain the production GLB. The model is never allowed to resolve outside this directory.
AURA_AVATAR_ASSET_DIR=aura_music_studio/static/aura
# Optional filename/path under AURA_AVATAR_ASSET_DIR. Blank uses aura.glb.
AURA_AVATAR_MODEL_PATH=
# Set true only after the real browser 3D renderer has been integrated and validated.
AURA_AVATAR_3D_RENDERER_READY=false
```

`AURA_AVATAR_MODEL_PATH` is constrained to `AURA_AVATAR_ASSET_DIR`; path traversal/outside absolute paths are rejected.

## Routes

- `GET /aura-intelligence/api/avatar/status` — authenticated truthful runtime/asset/renderer state.
- `GET /aura-intelligence/avatar/model.glb` — authenticated GLB delivery; 404 when no valid model is installed.
- `GET /aura-intelligence/avatar-runtime.js` — state-bus/browser-host runtime.

## Voice integration

Hands-free Aura Voice uses the normal audited flow:

`microphone → membership-gated STT → canonical Aura streaming/tool turn → saved assistant message → membership-gated TTS`

and emits visual states along that path:

`listening → thinking/tool_running → speaking → listening/idle`.

The avatar may animate those states, but it cannot authorize a write, infer rights/consent, elevate ESP access, or report an unconfigured renderer/model as successful.

## Next production asset phase

The canonical Aura character artwork should be converted into a rigged production model outside the source-code text pipeline, reviewed for likeness/design consistency, retopologized/optimized, facially rigged, and exported to GLB. Because model assets are binary and large, they should be deployed through a binary-capable artifact/model store or deployment volume rather than base64-embedding a large rig into source control.
