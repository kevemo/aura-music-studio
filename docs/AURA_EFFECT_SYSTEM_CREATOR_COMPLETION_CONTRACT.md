# Aura AI Effect/System Creator & Universal Studio Library Completion Contract

**Product:** Elevate Souls Productions Content Creation Command Center  
**Powered by:** Aura AI  
**Chat owner:** Chat 6 release truth / cross-chat coordination  
**Authoritative tracking issue:** #418

## Purpose

This document makes the expanded creative definition of 100% explicit. A studio page, catalogue schema, capability registry, preset list or partial renderer is not enough by itself to count a creative domain as complete.

The accepted product outcome is a shared, project-centred creative system in which every applicable studio has a deep searchable catalogue of real executable primitives and reusable assets, and Aura can compose new editable effect/system graphs from natural-language instructions.

## Shared creator architecture

The required common workflow is:

`prompt -> intent/specification -> typed graph -> dependency resolution -> policy/entitlement validation -> sandbox validation -> renderer/compiler/runtime -> preview -> editable graph/parameters/keyframes -> project save -> reusable library item`

Aura must compose only allowlisted typed primitives and registered adapters. It must not generate arbitrary shell/process/device execution as a generic creative primitive.

Generated systems must be:

- editable rather than opaque where the studio supports non-destructive editing;
- versioned and reproducible from saved graph/specification plus renderer/provider versions;
- project-centred and rights/provenance aware;
- bounded for graph depth, node count, render time, memory/GPU use and provider cost;
- entitlement aware;
- previewable before destructive/export actions;
- fail-closed when required renderers, providers, models, native clients or permissions are unavailable;
- auditable with author/source/licence/provenance/version metadata.

## Shared catalogue record

Every reusable primitive, effect, system, preset, transition, template, material, instrument, workflow or Aura-generated graph should resolve to a common catalogue record containing at least:

- stable namespaced ID;
- domain/category/subcategory;
- label, description, tags and search terms;
- compatible input/output media or runtime types;
- typed editable parameter schema with min/max/defaults/choices;
- compatible renderers/models/providers/runtime adapters;
- implementation state and dependency state;
- tier/entitlement requirements;
- preview metadata;
- version, migration, deprecation and replacement metadata;
- licence, source, author and commercial-use status;
- rights/consent/provenance requirements;
- accessibility metadata;
- safety/content restrictions;
- CPU/GPU/memory/cost expectations where relevant;
- evidence proving real execution before an item is called executable.

User-created, Aura-created, ESP-created and marketplace catalogue items should use the same base contract with explicit scope/authority differences.

## Video Studio

The Video Studio completion denominator includes deep executable libraries for:

- transforms, crop, perspective and geometry;
- opacity, blend modes, mattes, masks and track mattes;
- grading, curves, wheels, HSL/selective colour and LUTs;
- blur, sharpen, glow, bloom, halation, light rays and lens effects;
- film grain, noise, chromatic aberration, vignette and texture;
- keying, background removal, edge refinement and spill suppression;
- tracking, rotoscoping and subject/object masks;
- motion blur, stabilization, shake, camera moves and handheld systems;
- reverse, freeze, looping, speed ramps, optical-flow/time effects;
- dissolves, wipes, slides, zooms, whips, spins, morphs, glitches, light, particle and 3D transitions;
- stylisation families such as cartoon, sketch, posterise, pixel, retro, VHS/CRT, cyber, neon, monochrome and cinematic looks;
- animated captions, titles, lower thirds and data-driven graphics;
- particles, sparks, smoke, rain, snow, stars, hearts, confetti and audio-reactive systems;
- tracked stickers, callouts and overlays;
- generative edit operations such as object/background editing, fill and extension where a compatible provider/runtime exists;
- reusable motion/VFX graphs and templates.

Existing universal Video Studio rendering and automation waves count as real foundation/evidence, but do not by themselves close this denominator.

Example accepted Aura prompt:

> Turn the subject into glowing particles, spiral them toward the camera, reform into the next clip, add a purple shockwave and make the shockwave react to the bass.

Aura should build an editable graph from available tracking/mask, particle, glow, displacement, compositing, transition and audio-analysis primitives, preview it, expose parameters/keyframes, and save it as a reusable graph when requested.

## Music Studio

The Music Studio completion denominator includes deep libraries for:

- instruments, ensembles, drums, percussion, samples and loops;
- EQ and dynamic EQ families;
- compressors, multiband dynamics, limiters, gates, expanders, de-essers and transient tools;
- reverbs, convolution, delays, stereo/spatial tools and modulation;
- tape/tube/console saturation, clipping, distortion, overdrive, fuzz and bit crushing;
- pitch, formant, harmonizer, octave, time-stretch and warp systems;
- gain/pan/phase/utility processing;
- granular, spectral, glitch, stutter, reverse, riser/downlifter and sidechain creative systems;
- vocal tuning, de-essing, breath/presence/body/air/warmth, doubling, harmony and choir chains;
- mixing, reference mixing, mastering and delivery presets;
- adaptive score and game/video music systems;
- editable AI-designed effect chains and automation.

The existing audio effects/pedal registry and AI chain designer are foundations. 100% requires broad real execution and user-visible discovery/editing, not only catalogue declarations.

Example Aura prompt:

> Create a guitar effect that starts warm and clean, grows wider while notes sustain, and blooms into a huge cosmic stereo tail on long notes.

The output should be an editable DSP/automation graph using registered processors, with safe bounds and reproducible versioned parameters.

## Image / Design Studio

The Image/Design denominator includes deep libraries for filters, compositing, blend modes, masks, retouching, typography/layout, vector shapes/paths, brushes, gradients, patterns, textures, palettes, frames, mockups, materials, lighting/style packs and generative edits.

Aura-created image effects should remain editable through layers/effect graphs where possible instead of being represented only as flattened outputs.

## Game Forge / 3D

The Game Forge/3D denominator includes reusable systems and libraries for:

- entity/component systems and visual-script nodes;
- input, camera, physics, animation and interaction;
- materials, shaders, lighting, particles and VFX;
- UI, menus, HUD, dialogue, quests, inventory and progression;
- save/load, achievements, leaderboards and analytics;
- multiplayer/matchmaking/chat/live-ops adapters where supported;
- procedural generation, terrain, environments and skyboxes;
- characters, rigs, retargeted animation and reusable animation sets;
- spatial audio, sound design and adaptive music;
- export/runtime templates and validated target adapters.

Example Aura prompt:

> Create a teleport system where the player dissolves into stars, travels through a wormhole and appears at the target with a shockwave.

Aura should construct an editable bounded component/event/VFX/audio graph from registered primitives, rather than emitting an opaque claim that a system exists.

## Voice Studio

The Voice denominator includes reusable voice-processing and performance libraries for TTS styles, approved identity profiles, voice-to-voice conversion, emotion/prosody, cleanup, restoration, dubbing/timing, lip-sync/viseme timelines, character/performance presets and real-time processing where supported by safe native/provider adapters.

## Aura LIVE / Streaming

Aura LIVE must expose reusable scenes, overlays, alert systems, lower thirds, captions, widgets, goals, polls, wheels, trivia/mini-games, visualisers, particles, event reactions, TTS/avatar states, moderation/Guardian actions and safe event-driven compositions.

Prompt-driven generation must remain within documented platform permissions and registered local/browser/runtime primitives.

## Social / Creator / other applicable surfaces

Social, Creator, Agent and other applicable studio/control surfaces should use domain-appropriate template/action/workflow libraries and shared Aura composition contracts where useful, without bypassing platform permission, outreach, compliance or approval boundaries.

## Definition of an executable catalogue item

An item may be counted as executable only when all required conditions are met:

1. its schema is valid;
2. required renderer/provider/runtime dependencies are available or correctly fail closed;
3. its parameters are validated;
4. it produces a real output or runtime behaviour in tests;
5. source/project confinement and immutability guarantees are respected where required;
6. provenance/rights metadata is recorded;
7. representative user workflow is surfaced in the applicable studio;
8. regression coverage exists for success and failure boundaries.

`planned_original`, `contract_ready`, `renderer_required` and `external_provider_required` are not equivalent to executable completion.

## Effect/System Creator acceptance criteria

Issue #418 may close only when:

1. a shared typed Aura graph-composer exists;
2. Video, Music, Image and Game/3D provide real prompt -> graph -> preview -> edit -> save workflows;
3. Voice/LIVE/Social use the shared contract where applicable instead of incompatible ad-hoc copies;
4. generated graphs are versioned, auditable and reproducible;
5. catalogue search/filter/discovery is user-visible on relevant studio pages;
6. allowed user/Aura-created items can be saved and versioned in appropriate scopes;
7. unsupported dependencies fail closed and are represented truthfully;
8. complex multi-node end-to-end tests exist for each major creative domain;
9. performance/resource/provider-cost limits are enforced;
10. rights, consent and provenance survive graph composition and rendering;
11. release-truth documentation and the Chat 6 master completion register include this scope in the denominator.

## Release truth

This contract expands the definition of repository completeness. It does not weaken the separate P0/P1 security, commercial, native-client or external production-evidence gates.

A large number of preset names is not 100%. A graph schema is not 100%. A provider route is not 100%. Completion requires real user-visible, editable, executable and tested workflows across the accepted catalogue families, with external-only requirements remaining explicitly fail-closed until production evidence exists.
