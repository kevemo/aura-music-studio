# Chat 8 Game Forge 3D Model Runtime Contract

Status: production-facing closed static-model import/runtime boundary for `development/full-site-build`.

## Current executable capability

Aura3D currently executes imported `.glb` and embedded-buffer `.gltf` assets as a closed **static triangle-mesh projection**.

The importer flattens reviewed glTF 2.0 node transforms into runtime triangle data and emits only position, normal and UV attributes consumed by the reviewed Aura3D WebGL2 path. The browser does not fetch the original model file and does not execute source-model code.

## Truthful unsupported features

The current model runtime does **not** execute:

- skeletal animation
- skinning / joint deformation
- animation clips
- embedded material programs/extensions
- external GLTF buffers/resources
- model-supplied scripts or generated code

A source asset may contain `animations` or `skins`. Those sections are detected and reported, but the current runtime continues to use only the static mesh projection. API responses expose warnings so clients must not represent the asset as an animated/rigged runtime model.

Per-model `runtime_capabilities` reports:

- `projection_mode = closed_static_mesh`
- `runtime_mesh_projection = true`
- `skeletal_animation_runtime = false`
- `skinning_runtime = false`
- `animation_clips_runtime = false`
- whether source animations/skins were detected
- `source_animation_or_skin_data_executed = false`
- `embedded_materials_executed = false`
- `external_resources_allowed = false`
- `runtime_network_required = false`
- explicit warnings when animation/skin sections are present

## Import integrity and resource limits

The closed decoder enforces configured server-side limits before/while projecting geometry, including:

- upload byte-size limit
- per-game model-count limit
- expanded runtime vertex limit
- primitive limit
- accessor-element limit
- node count
- mesh count
- accessor count
- buffer-view count
- buffer count
- node traversal depth

The default accessor-element cap is 250,000. Document-structure caps are configurable through the existing Aura Game Model environment namespace.

## Numeric safety

All decoded floating-point geometry, node transforms, transformed points, normals and UVs must be finite.

`NaN` and positive/negative infinity fail closed before runtime projection. Matrix multiplication and transformed normals are also checked so finite source values cannot overflow into non-finite browser geometry silently.

This protects WebGL buffer generation and downstream bounds/integrity calculations from malformed or adversarial numeric payloads.

## Supported geometry

The current closed runtime supports:

- glTF 2.0
- GLB embedded binary chunks
- JSON glTF with base64 embedded buffers
- TRIANGLES primitives
- POSITION
- optional NORMAL
- optional TEXCOORD_0
- indexed or non-indexed triangles
- reviewed node transforms

It deliberately rejects or does not execute unsupported features rather than forwarding them to the browser.

## Rights and integrity

Each stored model retains:

- tenant-scoped model identity
- source SHA-256
- byte size
- rights-confirmed flag
- rights attestation
- closed mesh summary

Runtime loading re-verifies stored size and SHA-256 before projection. Model changes invalidate stale Game Forge build, rating and public snapshot state. Publication remains blocked if rights confirmation/attestation or mesh integrity fails.

## Tests

Relevant suites include:

- `tests/test_game_forge_models3d.py`
- `tests/test_game_forge_mesh_hardening.py`

They verify embedded GLTF/GLB projection, no external resources, model binding/runtime integration, static skeletal capability truth, non-finite rejection, structure/accessor resource limits, and warnings when animation/skin data is present but not executed.

## Extension rule

Skeletal animation or rigging must not be enabled by changing capability flags alone. A future implementation requires a reviewed joint/skin/clip runtime, bounded decoding and interpolation, integrity semantics, browser execution support, resource budgets and automated tests. Until then, Chat 8 reports the limitation explicitly rather than simulating animated-model support.
