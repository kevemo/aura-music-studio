# Aura Game Engine — Native Architecture and Capability Target

Status: active engineering specification for Pulsar-Frequency House Game Creation.

## Product principle

Aura Game Forge is not a wrapper around Unreal, Unity, Godot, Phaser, PlayCanvas or Babylon. The editable source of truth is **Aura Game DNA + Aura World DNA** and the primary runtime targets are **Aura Game Engine 2D** and **Aura Game Engine 3D**.

External engines may be supported later as optional compatibility/export adapters when their licences and platform requirements permit. They must never become the only place where a Pulsar game can be edited.

The target is Unreal-class capability coverage and then differentiation through conversational, multimodal, cross-media Aura workflows. Reaching feature parity with a decades-old AAA engine is a multi-stage engineering programme, not a single renderer switch.

## Current native foundation — implemented on the Game Forge branch

- Game DNA with genre, niches, mechanics, controls, scenes, art/audio/NPC/multiplayer direction and content disclosures.
- Aura Game Engine 2D / Aura Game Engine 3D are the default targets.
- Aura World DNA scene graph with entities, transforms, parent hierarchy and asset references.
- Aura Material DNA with PBR/unlit/toon/emissive/water/terrain abstractions.
- Aura Lighting DNA with directional/point/spot/area/sky lights and shadow intent.
- Aura Physics DNA with static/dynamic/kinematic/trigger bodies and collision layers.
- Aura Animation State DNA.
- Aura Safe Behavior Graph using a closed operation vocabulary rather than arbitrary creator/LLM code execution.
- Aura Terrain DNA and procedural-world rules.
- Aura World Cells for deterministic spatial partition indexes.
- Aura Adaptive Detail / performance budgets for FPS, visible entities, lights, draw calls, triangles, textures and LOD distances.
- Basic £4.99: one active editable game workspace; Pro £9.99: unlimited active game projects; Free: approved play/test gallery.
- Private deterministic sandboxed browser playtest with no network access and no arbitrary server-side game-code execution.
- Rating/compliance preflight with explicit non-official-rating wording.
- Build/rating/public-test integrity bound to both high-level Game DNA and the complete current World DNA.
- Material world changes invalidate the previous build, rating assessment and public test approval.
- Pop-out playtest window intended for normal OS window capture in TikTok LIVE Studio/OBS-style streaming workflows; Pulsar does not claim TikTok LIVE backstage control.

## High-end capability matrix

The names below are Aura-native systems. References to other engines describe the capability class being benchmarked, not copied proprietary implementations.

| AAA capability class | Aura-native target | Stage |
| --- | --- | --- |
| Large-world partition and distance streaming | Aura World Cells + hierarchical streaming sources | Foundation implemented; runtime streaming building |
| Virtualized geometry / automatic detail | Aura Adaptive Geometry Stream | Planned native 3D renderer stage |
| Dynamic global illumination and reflections | Aura Radiance | Planned WebGPU/GPU renderer stage |
| High-quality shadow virtualization | Aura Shadow Atlas / clustered shadowing | Planned |
| Procedural content generation | Aura Procedural World Graph | Data model implemented; editor/runtime generation building |
| Advanced layered materials | Aura Material DNA + Aura Material Graph | DNA implemented; node editor/shader compiler planned |
| Terrain / open-world biome construction | Aura Terrain DNA + World Sculpt | DNA implemented; sculpt/mesh/voxel generation planned |
| Rigid body, character and destruction physics | Aura Physics | DNA implemented; native solver/adapter execution planned |
| Particles and real-time VFX | Aura VFX Graph | Behavior hook implemented; graph/editor/runtime planned |
| Character creation/rigging | Aura Character Forge | Planned |
| Animation graphs / retargeting / control rigs | Aura Motion Graph | State DNA implemented; graph/IK/retarget stage planned |
| Cinematics / sequencer | Aura Cinematic Timeline | Planned, sharing Pulsar Video Studio assets/timeline concepts |
| Spatial/procedural game audio | Aura Game Audio Graph | Planned, sharing Pulsar music/SFX/voice generation systems |
| NPC/crowd simulation | Aura Life Engine | Planned |
| Behavior trees/gameplay scripting | Aura Safe Behavior Graph | Foundation implemented; visual graph + richer ops building |
| Navigation/pathfinding | Aura Navigation Mesh/Field | Planned |
| Multiplayer replication/lobbies | Aura Multiplayer Runtime | Planned with server-authoritative isolation and moderation controls |
| Save games/profile/progression | Aura Game State | Planned |
| UI/HUD editor | Aura Game UI Composer | Planned |
| Asset import/conversion | Aura Asset Pipeline | Planned; use open standards such as glTF where practical |
| GPU profiling/performance scalability | Aura Performance Budgeter | DNA implemented; live profiler/adaptive quality planned |
| Editor collaboration/versioning | Aura Game Revisions | Planned on the existing Pulsar revision/tenant architecture |
| Platform export | Aura Export Matrix | Browser first; optional Godot/other adapters later where legal/practical |
| AI-assisted world/game creation | Aura Game Director | Foundation tools implemented; deep world/asset/logic generation building |

## Differentiation beyond conventional engines

Aura's advantage is intended to be the **single conversational project intelligence layer** across the whole Pulsar site:

1. A member describes a game by text, voice, sketches, reference images, music, video or existing project assets.
2. Aura creates/updates Game DNA and World DNA rather than generating an opaque one-off blob.
3. Aura can call the site's own Image, Video, Music, Voice and future 3D systems to build assets with common provenance and revision history.
4. Every generated world entity, character, material, quest, audio cue and scene remains addressable for later edits.
5. Commands such as “make that mountain twice as high”, “change this NPC's personality”, “turn this into four-player co-op”, “replace the soundtrack” or “make this suitable for age 7+” become bounded edits to the existing project.
6. Aura can run playtest, performance, accessibility, content/rating and security preflights and explain the blockers.
7. Public/free testing is permitted only for an immutable approved build whose content hash matches the current scan.

## Safety and legal boundary

Pulsar can provide an internal **Pulsar Safety & Rating Assessment** and regional rating estimates. It must not display an ESRB, PEGI, IARC, USK, ACB/Australian Classification or other authority badge as official until a genuine external classification result is supplied and verified.

Changes to violence, language, sexual content, gambling/loot mechanics, monetisation, chat, UGC, networking, personal-data use, advertising, world/gameplay content or other material rating inputs invalidate prior approval.

Public child-accessible social features require dedicated privacy, age-assurance, moderation, report/block and parental-safety design. Real-money gambling is not eligible for the current Pulsar public playtest path.

Generated game code/assets must not execute inside the FastAPI application process. Native runtime code is owned/reviewed Pulsar code; generated creator content enters that runtime as validated data. Any future generated scripting must run in a separately constrained sandbox with explicit capabilities.

## Research benchmark references

Current benchmark families were checked against official Epic documentation, including World Partition, Lumen, Procedural Content Generation, MetaHuman and Unreal Engine 5.8 worldbuilding/release documentation:

- https://dev.epicgames.com/documentation/en-us/unreal-engine/world-partition-in-unreal-engine
- https://dev.epicgames.com/documentation/en-us/unreal-engine/lumen-global-illumination-and-reflections-in-unreal-engine
- https://dev.epicgames.com/documentation/en-us/unreal-engine/using-pcg-generation-modes-in-unreal-engine
- https://dev.epicgames.com/documentation/en-us/unreal-engine/unreal-engine-5-8-release-notes
- https://dev.epicgames.com/documentation/en-us/metahuman/metahuman-documentation

Open-source technology is researched for standards, techniques and optional commercially compatible low-level components. Every dependency/model still requires a licence and redistribution review before inclusion in a paid production deployment.
