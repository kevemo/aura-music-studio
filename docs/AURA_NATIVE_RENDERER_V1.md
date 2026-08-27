# Aura Native Renderer 3D v1

This stage is the first real browser 3D runtime owned by Pulsar-Frequency House. It consumes Aura World DNA directly and does not load Phaser, PlayCanvas, Babylon, Godot, Unity or Unreal.

## Implemented in v1

- WebGL2 compatibility renderer.
- Reviewed Pulsar runtime code only; creator/LLM JavaScript is not executed.
- Aura World DNA entity transforms and primitive rendering.
- PBR-oriented material parameters reduced to a simple native lit-material pass for this first renderer.
- Directional-light and ambient-light response.
- Depth testing and back-face culling.
- Perspective camera with orbit/zoom controls.
- WASD/arrow movement for the current player entity.
- World-cell visibility based on the Aura streaming budget.
- Distance/LOD envelope culling.
- Device-pixel-ratio cap for predictable browser GPU load.
- WebGPU capability detection while retaining WebGL2 as the v1 compatibility path.
- No runtime network access under the Game Forge playtest CSP.

## Not yet claimed

v1 is not equivalent to Unreal rendering fidelity. The following remain future native stages: WebGPU renderer, GPU-driven scene submission, mesh/texture asset loading, advanced PBR/IBL, dynamic global illumination, reflections, shadow atlas/virtual shadowing, terrain mesh generation, skeletal animation/IK, particles/VFX, post-processing, occlusion/frustum acceleration structures, native physics execution, navigation, crowds/NPC simulation, multiplayer replication and XR.

Every later stage must preserve the Game DNA + World DNA editability and the build/rating integrity boundary established by the Game Forge foundation.
