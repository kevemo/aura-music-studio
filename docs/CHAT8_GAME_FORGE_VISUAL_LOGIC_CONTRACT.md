# Chat 8 Game Forge Visual Logic Contract

Status: additive Chat 8 contract for `development/full-site-build`.

## Purpose

Game Forge Visual Logic is a first-party browser node workbench backed by a closed compiler. It does not execute creator-authored JavaScript, Python, shader source or arbitrary expressions. A successful compile writes sanitized `BehaviorNodeDNA` rows into the canonical `GameWorldDNA`; existing Game Forge runtime projections then execute only the operations they explicitly support.

This is intentionally narrower than a general-purpose scripting language. A node is exposed only after an executable runtime path, sanitizer and automated tests are verified in the repository.

## Canonical implementation

Backend/compiler:

- `aura_music_studio.game_forge_visual_logic`

Browser workbench:

- `aura_music_studio.game_forge_visual_logic_portal`

Existing runtime and sanitizers reused:

- `aura_music_studio.game_forge_world_logic`
- `aura_music_studio.game_forge_gameplay`
- `aura_music_studio.game_forge_runtime_state`
- `aura_music_studio.game_forge_world.GameWorldDNA`
- `aura_music_studio.game_forge_world.BehaviorNodeDNA`

No second game runtime or project store is introduced.

## Schema

Visual graph schema:

`game_forge_visual_logic.v1`

Compiler:

`aura_world_logic.v2`

The v2 compiler extends the original closed World Logic compiler with gameplay operations already executed by the Aura3D runtime-state kernel. The graph persistence schema remains backward compatible.

A graph records:

- deterministic graph ID bound to Game DNA + World entity identity
- Game ID and entity ID
- optimistic concurrency revision
- typed nodes
- compile-order edges
- graph-owned compiled Behavior DNA IDs
- compiled World revision
- compiler/schema versions
- created/updated timestamps

Node authoring position is persisted for the visual workbench. Runtime behavior is never derived from arbitrary canvas geometry.

## Runtime-verified node operations

World Logic operations:

- `follow_target`
- `timer`
- `door`

Aura3D gameplay operations, verified in `game_forge_runtime_state` through `gameplay_runtime_payload`:

- `collectible`
- `damage`
- `checkpoint`
- `patrol`
- `quest_trigger`

The broader `BehaviorOp` model is not automatically exposed as visual scripting. New node types must first have a reviewed executable runtime implementation, sanitization contract and tests.

The capability response separates `world_logic_ops` from `aura3d_gameplay_ops` so clients do not have to infer runtime support.

## Edge semantics

Edges are `compile_order_only`.

They provide deterministic topological ordering for graph-owned Behavior DNA. They do not imply arbitrary control flow, hidden branching, loops or code evaluation.

Rules:

- source and target must exist in the graph
- self-links are rejected
- duplicate links are rejected
- cycles are rejected
- disconnected nodes retain stable authoring order

## Sanitization

Visual Logic reuses the sanitizer already trusted by each executable runtime path.

World Logic examples:

- `follow_target`: target, speed, stop distance
- `timer`: seconds, repeat, event text
- `door`: axis, distance, speed, trigger distance, auto-close, close delay

Aura3D gameplay examples:

- `collectible`: bounded points, respawn flag, respawn seconds
- `damage`: bounded amount, checkpoint-reset flag, cooldown seconds
- `checkpoint`: bounded label
- `patrol`: axis, distance, speed, ping-pong flag
- `quest_trigger`: bounded event text, once flag

Unknown fields such as script snippets, JavaScript text or URLs are removed and are not persisted in the graph or compiled Behavior DNA.

Numeric values are clamped to the same bounded ranges used by the executable runtime.

## Runtime preflight

Compilation fails closed if the resulting behavior cannot execute safely.

Current requirements include:

- follow targets must resolve to an existing World entity
- `follow_target` requires kinematic or dynamic Physics DNA
- `door` requires kinematic Physics DNA
- `patrol` requires kinematic or dynamic Physics DNA
- `collectible`, `damage`, `checkpoint` and `quest_trigger` require Physics DNA for collision behavior
- core player/camera entities cannot be authored through this graph
- entity behavior-count safety limits remain enforced

The compiler does not silently replace physics configuration or convert core entities.

## Ownership and coexistence

Visual Logic owns only Behavior DNA IDs listed in the graph's `compiled_behavior_ids`.

Recompilation removes/replaces only the prior graph-owned behaviors. Manually authored, Aura-authored or other feature-owned behaviors on the same entity are preserved.

Deletion removes only graph-owned Behavior DNA and leaves unrelated behaviors intact.

## Integrity and build invalidation

A successful compile writes the canonical World DNA, increments its revision and invalidates:

- stale public snapshot/public ID
- stale rating assessment
- stale latest build
- non-draft status

The user must rebuild before playtest/public testing. Because executable graph output lives in World DNA, existing Game Forge integrity hashing and publication checks bind the compiled result without inventing a parallel integrity scheme.

A failed validation does not mutate the World or persist a graph.

## Optimistic concurrency

PUT requests may supply `expected_revision`.

If another save has advanced the graph, stale writes fail with HTTP 409 rather than overwriting newer node work.

## Persistence

Graph state is stored beneath the existing tenant-scoped Game Forge directory:

`visual_logic/<sha256(entity_id)>.json`

The filename is derived from a digest instead of client path text. Stored graph identity is revalidated on load.

No filesystem path is accepted from the caller.

## API

- `GET /api/game-forge/games/{game_id}/visual-logic`
- `GET /api/game-forge/games/{game_id}/visual-logic/{entity_id}`
- `PUT /api/game-forge/games/{game_id}/visual-logic/{entity_id}`
- `DELETE /api/game-forge/games/{game_id}/visual-logic/{entity_id}`

Capability responses explicitly report:

- all verified runtime operations
- World Logic vs Aura3D gameplay operation groups
- compile target `WorldEntityDNA.behaviors`
- compile-order-only edge semantics
- no arbitrary script source
- no `eval`
- no graph-originated runtime network access
- no unknown-node execution
- optimistic concurrency
- build invalidation after compile

## Browser workbench

Route:

`GET /game-creation/visual-logic/{game_id}/{entity_id}`

The workbench exposes all eight currently verified operations and provides:

- draggable node layout
- typed World Logic and Aura3D gameplay palettes
- operation-specific bounded parameter controls
- enable/disable state
- visual compile-order links
- link removal
- node removal
- revision-aware compile/save
- graph deletion
- direct Build & Play handoff
- navigation back to Advanced World Logic
- explicit Physics DNA guidance for collision and movable-node requirements

It contains no arbitrary code editor and uses no external node-graph dependency.

Project context advertises:

- `visual_logic_capabilities_url`
- `visual_logic_editor_url_template`

## Tests

- `tests/test_game_forge_visual_logic.py`
- `tests/test_game_forge_visual_logic_gameplay_ops.py`
- `tests/test_game_forge_visual_logic_portal.py`

Coverage includes:

- compilation into real World Logic Behavior DNA
- compilation of all five gameplay operations into the production `gameplay_runtime_payload`
- exact runtime parameter sanitization and bounds
- unknown script/JavaScript/URL field removal
- topological ordering
- cycle/dangling-edge/path-like node ID rejection
- stale revision conflicts
- physics/runtime preflight failure without mutation
- stale build/rating/public-state invalidation
- preservation of non-graph behaviors
- graph-owned deletion
- truthful separated capability flags
- release application route composition
- complete eight-operation workbench palette
- no `eval`/`new Function`/textarea code controls

## Extension rule

Chat 8 may expand this graph only by adding typed operations with a corresponding executable Aura runtime and tests. The correct growth path is richer closed node semantics—events, conditions, state-machine transitions, gameplay actions and safe data bindings—not arbitrary browser/server code execution.
