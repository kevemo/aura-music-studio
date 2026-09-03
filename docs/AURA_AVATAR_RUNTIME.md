# Aura Embodied Host Runtime

Pulsar-Frequency House treats Aura's visual body as a presentation layer over the same audited Aura Core used by text, voice and Studio tools. The avatar never creates a second hidden execution path.

## Current software state

The repository contains the embodied-host state bus, a real browser GLB renderer and a layered performance-control runtime in `aura_music_studio/aura_avatar_runtime.py`.

Supported Aura base states:

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
4. maps Aura states to the closest matched base animation clip;
5. crossfades base-state animation changes;
6. uses the public `appendAnimation()` / `detachAnimation()` renderer API for independent rig-authored viseme, gaze and gesture layers when those clips exist;
7. schedules a bounded automatic blink through the rig-authored blink layer;
8. reports `aura:3d-ready` only after the model load event and includes whether the layered animation API and required rig performance clips are available;
9. falls back to the cosmic Aura Core state visual if the renderer/module/model fails.

The cosmic host visual remains a fallback and is **not** described as the finished photoreal Aura character.

## Production readiness contract

Aura reports distinct status values:

1. `software_runtime_connected` — the Aura state/event software exists.
2. `renderer_implemented` — GLB rendering code exists in source.
3. `renderer_configured` — an approved same-origin/HTTPS renderer module is configured.
4. `model_installed` — a real `.glb` exists under the configured Aura asset root.
5. `model_valid` — the file passes structural GLB/glTF 2 validation.
6. `model_validation.production_rig_ready` — the installed GLB satisfies the code-verifiable base-state and layered-performance rig contract.
7. `operator_validated` — the deployment operator has explicitly marked this renderer/model combination validated after browser/device testing.
8. `production_3d_ready` — true only when Aura is enabled and the model is structurally valid, the production rig contract passes, the renderer is configured and the actual deployment has been operator-validated.

A `.glb` on disk alone does not make Aura production-ready. A structurally valid GLB plus `AURA_AVATAR_3D_RENDERER_READY=true` also cannot make an incomplete performance rig appear production-ready; the runtime reports `model_renderer_validated_performance_rig_incomplete` instead.

## Production rig contract

The canonical production Aura model should be glTF 2.0 / GLB and contain:

- production-quality humanoid skeleton and a documented deterministic root;
- stable neutral rest pose;
- facial blendshapes/morph targets suitable for expressions and speech;
- browser-appropriate materials and texture sizes;
- LODs or equivalent desktop/mobile optimization evidence;
- named base animation clips that cover every Aura runtime state;
- rig-authored layered viseme animation clips;
- rig-authored gaze clips;
- blink plus expressive/body gesture clips;
- deterministic scale/orientation;
- no unlicensed textures, meshes, mocap, voice or likeness data.

### Required base-state coverage

The validator expects a match for all ten runtime roles: `idle`, `welcoming`, `listening`, `thinking`, `tool_running`, `speaking`, `celebrating`, `warning`, `recording_coach` and `studio_engineer`. Aliases are accepted so the production rig does not have to use one exact spelling.

### Layered viseme contract

The layered performance profile uses fifteen bounded OVR/Oculus-style semantic channels:

`sil`, `pp`, `ff`, `th`, `dd`, `kk`, `ch`, `ss`, `nn`, `rr`, `aa`, `e`, `i`, `o`, `u`.

Each channel maps to accepted clip-name aliases such as `viseme_aa`. The canonical asset may use the documented aliases rather than one fixed exporter naming convention. The validator requires complete channel coverage before `production_rig_ready` can become true.

This phase intentionally uses **rig-authored animation clips** through `<model-viewer>`'s public layered-animation API. It does not reach into private Three.js scene internals and does not claim a raw per-morph-target renderer API that the current presentation layer does not expose.

### Gaze and gesture contract

Required gaze layers are `center`, `left`, `right`, `up` and `down`.

The gesture catalogue currently recognises `blink`, `nod`, `shake`, `wave`, `open_hands` and `point`. Production readiness requires a matched blink plus at least two additional expressive/body gesture layers. Additional clips may exist without changing the protocol.

The validator also reports root-bone and explicit LOD naming/extension signals. Lack of explicit LOD metadata remains a warning rather than a false assertion that optimization is impossible, because equivalent deployment optimization can be delivered outside one GLB extension.

## Browser performance API

The browser exposes `window.AuraHost.performance` using protocol `AuraHost.performance/v1`:

- `setViseme(channel, options)` — apply or replace the current viseme layer;
- `setGaze(direction, options)` — apply or replace the current gaze layer;
- `gesture(name, options)` — play a one-shot expressive/body layer;
- `speechFrame(detail)` — accept a provider/application performance frame;
- `clear()` — remove active performance layers;
- `status()` — report currently available animations, active layers and whether layered performance is supported by the loaded renderer.

Equivalent DOM inputs are accepted:

- `aura:viseme-input`
- `aura:gaze-input`
- `aura:gesture-input`
- `aura:speech-frame`

Performance actions emit `aura:performance` for observability. These events alter presentation only; they do not authorize Aura tools, project writes, account actions or platform access.

## Speech and lip-sync truthfulness

The existing generic Aura speech endpoint produces/caches an audio WAV through `AuraSpeechService`. The currently supported generic TTS adapters return audio, not authoritative per-phoneme or per-viseme timing metadata.

Therefore the fallback hands-free voice flow only reports truthful speech lifecycle frames to the avatar: speaking starts when TTS playback starts and the viseme layer returns to `sil` when playback completes, fails, is stopped or the page unloads. It does **not** infer or fabricate phoneme timestamps from the response text.

A future or deployment-specific TTS provider that supplies real timing/viseme metadata can feed those authoritative frames through `aura:speech-frame` / `AuraHost.performance.speechFrame(...)` without replacing the renderer contract.

## Deployment variables

```env
AURA_AVATAR_ENABLED=true
AURA_AVATAR_ASSET_DIR=aura_music_studio/static/aura
# Blank uses <asset dir>/aura.glb
AURA_AVATAR_MODEL_PATH=

# Default when omitted: pinned official model-viewer 4.3.1 module.
# Set a same-origin URL for a fully self-hosted/vendored deployment.
AURA_AVATAR_RENDERER_MODULE_URL=https://ajax.googleapis.com/ajax/libs/model-viewer/4.3.1/model-viewer.min.js

# Keep false until the actual production-grade rig + renderer combination is tested and approved
# on the deployment/browser/device matrix. This flag cannot override an incomplete rig contract.
AURA_AVATAR_3D_RENDERER_READY=false
```

`AURA_AVATAR_MODEL_PATH` is constrained to `AURA_AVATAR_ASSET_DIR`; traversal/outside paths are rejected. Renderer module URLs must be either same-origin absolute paths beginning with `/` or public HTTPS URLs.

## Routes

- `GET /aura-intelligence/api/avatar/status` — authenticated runtime/model/renderer/performance/validation state.
- `GET /aura-intelligence/avatar/model.glb` — authenticated GLB delivery; 404 until a structurally valid model is installed.
- `GET /aura-intelligence/avatar-runtime.js` — Aura state bus, GLB browser renderer and layered performance runtime.

## Voice integration

Hands-free Aura Voice keeps one audited path:

`microphone → membership-gated STT → canonical Aura streaming/tool turn → saved assistant message → membership-gated TTS`

with visual states:

`listening → thinking/tool_running → speaking → listening/idle`.

The character renderer can animate those states and provider-supplied performance frames but cannot authorize project writes, infer rights/consent, elevate ESP access or claim an unavailable model/service succeeded.

## Remaining production asset and validation phase

The repository can implement and validate the software contract, but it cannot manufacture proof that a final likeness-grade binary rig has been modelled, legally cleared, optimized and tested across real deployment hardware.

The canonical Aura artwork still needs to be delivered as the actual production rig with modelling/retopology, PBR materials, hair/clothing setup, humanoid rigging, facial targets, the complete layered performance clip set, animation polish, LOD/device optimization and final GLB export. That binary asset should be delivered through a binary-capable artifact/model store or deployment volume rather than encoded into source text.

After the final asset is installed, production approval still requires real browser/device validation for load health, animation correctness, memory/GPU performance, mobile/desktop quality tiers and the intended likeness. Until that evidence exists, `production_3d_ready` must remain false.
