# Aura Living 3D Energy System

This is a production requirement for the canonical AuraCore-Osiris character used by Elevate Souls Productions Presents: The Live Sound Studio.

Aura is not a static avatar with a glowing texture. Her eyes, chest heart/core and suit circuitry are three independently controllable visual energy channels connected to page context, conversation state, generated audio energy and the embodied AI state machine.

## Identity stays fixed

Theme changes never load a different model, face or body. Aura remains the same canonical person and `aura.glb` identity. Only illumination, pulse behaviour, subtle facial state and surrounding presentation respond to context.

## Required GLB energy materials

The production GLB must expose independently discoverable emissive materials for:

- `Aura_Eyes`
- `Aura_Heart_Core`
- `Aura_Circuitry`

Aliases may be supported by the validator, but these canonical names are preferred.

All three must have a real emissive channel. A coloured diffuse/albedo texture without emissive capability is not sufficient. `scripts/build_aura_avatar.py` fails the final installation if any one of the three channels is absent or non-emissive.

The heart and circuitry may use `KHR_materials_emissive_strength` where the export pipeline supports it. Runtime intensity remains under Three.js control.

## Page and niche palette

The active site page or explicit ESP creator niche selects the base palette. The runtime can receive this through `data-niche`, `data-workspace`, `data-section`, a controlled `aura:theme` event, or known route context.

Examples:

- Music / Singing — magenta heart, violet circuitry, luminous pink eyes
- Gaming — cyan heart, electric violet circuitry, cyan-white eyes
- Business / Owner — restrained warm gold heart, pale violet/white circuitry, warm gold-white eyes
- Spiritual / Wellbeing — violet-pink heart, softer pink circuitry, luminous lavender eyes
- Art / Visual Studio — violet heart, cyan circuitry, lavender eyes

The niche system may add additional palettes, but it must never alter Aura's geometry or canonical face/body identity.

## Communication energy modes

Page/niche theme provides the base colours. Communication mode changes the pulse rate, amplitude, luminance and a small hue displacement within that palette.

### Idle

- slow breathing-like heart pulse
- low circuitry flow
- steady soft eye glow
- lowest compositing energy

### Listening

- slightly brighter eyes and heart
- calm medium-slow pulse
- subtle attentive facial expression
- no frantic flashing

### Speaking

- heart intensity reacts to measured speech/audio energy
- circuitry brightens in response to the same speech envelope
- eyes gently brighten and shift within the active page palette
- vocal energy changes intensity continuously rather than toggling on/off

### Thinking / Researching

- slower concentrated pulse
- subtle hue displacement within the page palette
- focused custom facial expression where available

### Creating / Rendering

- stronger controlled pulse
- brighter circuitry pathways
- energetic but not strobing presentation

### Translating

- distinct controlled hue displacement
- moderate pulse representing active two-way transformation

### Guiding / Presenting

- moderate pulse while walking/pointing to interface controls
- eyes and gaze remain directed toward the relevant control or user

### Celebrate / Completion

- highest short-duration energy state
- brighter eyes/heart/circuitry and outward visual presence
- automatically settles back to the appropriate idle/listening state

## Smooth transitions

Colour transitions are interpolated each frame. Aura must not snap between page/theme colours. The system preserves a current colour and a target colour for each channel and lerps toward the target.

## Accessibility and mobile performance

`prefers-reduced-motion` reduces pulse amplitude and colour transition speed but does not remove the semantic communication state entirely.

The outer CSS glow is throttled instead of being rewritten every animation frame, reducing mobile compositing cost. KTX2/Basis textures and Meshopt-compressed geometry/morph/animation data are supported by the production runtime through explicit Three.js decoders.

## Runtime events

Supported energy/context events include:

- `aura:theme`
- `aura:page-context`
- `aura:energy-mode`
- `aura:speaking`
- `aura:listening`
- `aura:thinking`
- `aura:researching`
- `aura:creating`
- `aura:translating`
- `aura:guide`
- `aura:celebrate`

SPA route changes (`pushState`, `replaceState`, `popstate`, `hashchange`) and supported page data attributes trigger page-theme re-evaluation.

## Production acceptance

A candidate Aura GLB is not production-ready merely because it renders. It must also pass:

1. base glTF 2.0 / VRM 1.0 runtime validation;
2. full production humanoid rig including articulated fingers, eyes, toes and shoulders;
3. detailed facial morph contract;
4. VRM LookAt;
5. VRM SpringBone secondary hair motion;
6. Aura-specific emotion expressions;
7. required authored body animation clips;
8. semantic material naming;
9. independently emissive eyes, heart and circuitry;
10. self-contained mobile packaging with KTX2/Basis textures and mesh compression;
11. mobile file/triangle budgets;
12. manual visual likeness approval against the canonical Aura reference images.

Only after every gate passes may the build pipeline atomically install the candidate as `aura_music_studio/static/aura/aura.glb`.
