# Legacy Reference Index — AuraCoreAI Deployment

Status: reference extraction in progress. This document is an inventory/recovery record, not a production-completion claim.

## Source archive

- Archive: `AuraCoreAI_Deployment (2).zip`
- SHA-256: `ee0d2986ac0f7243def15a1781646126bad790434e454f384e2ad3807b21d38a`
- ZIP entries: 12,687 including directories
- File entries: 7,418
- Uncompressed file bytes: 58,515,172
- Readable/source-like files identified by extension: 535
- Legacy material remains reference-only until provenance, licensing, security and architecture review is complete.

## High-value exact findings

### Aura avatar

A real Aura GLB is present in the archive at:

- `AuraCoreAI_Deployment/frontend/public/avatar/Aura-3d.glb` — 1,707,336 bytes

A generated/built duplicate is also present at:

- `AuraCoreAI_Deployment/frontend/dist/avatar/Aura-3d.glb` — 1,707,336 bytes

The `public` path is the preferred source-reference location; the `dist` copy should be treated as generated output unless provenance review proves otherwise.

### Nested module archive

- `AuraCoreAI_Deployment/backend/modules.zip` — 115,718 bytes
- 101 files inside the nested ZIP.

The nested module archive includes code organised around communication/speech, memory/context, scene/environment handling, visual runtime concepts and other historical Aura subsystems. Example exact paths include:

- `modules/communication/aura.speech.synthesizer.js`
- `modules/expansion/aura.scene.builder.js`
- `modules/expansion/environment.driver.js`
- `modules/frontend/visual.engine.js`
- `modules/memory/context.memory.js`
- `modules/memory/memory.recall.js`
- `modules/memory/memory.snapshot.js`
- `modules/memory/auraCodex.archive.sync.js`

These are architecture/code-reference candidates only. They are not automatically production-safe or compatible with the current Command Center.

### Package manifests

The archive contains at least these primary package manifests:

- `AuraCoreAI_Deployment/backend/package.json`
- `AuraCoreAI_Deployment/frontend/package.json`

Dependency and external-reference extraction should use manifests as authoritative hints, then verify every dependency/licence against the modern repository before reuse.

## Capability-path inventory

Filename/path classification over the archive currently identifies the following broad reference buckets. Counts are path-hint matches, not executable-feature counts:

| Reference bucket | Matching file paths |
| --- | ---: |
| Game Forge / game-engine / ARKSTAR / Fractalis related | 4,825 |
| Memory / context / Codex related | 183 |
| Voice / speech / TTS / phoneme related | 64 |
| Avatar / 3D related | 44 |
| AuraSec / security / guardian related | 38 |
| Multiplayer / replication / lobby / matchmaking / server related | 19 |
| Aura OS / scheduler related | 13 |
| Live-world-change / WorldDelta / SceneDelta / world-director / rollback path hints | 1 |
| Security-sensitive filename/path hints | 14 |

The high Game Forge count is dominated by historical game-engine concept/scaffold material. It must not be interpreted as 4,825 working engine features.

## Game-engine recovery rule

The archive contains a large `game engines/game-engines` historical corpus with hundreds of concept directories. Chat 9 must distinguish among:

1. owner-authored reusable implementation candidates;
2. architecture/design references;
3. generated/scaffold-only material;
4. third-party/licensed material;
5. obsolete material;
6. security-sensitive material;
7. unclear provenance.

A directory name or generated scaffold does not count as an executable Game Forge feature. Recovery must look for actual runtime logic, state transitions, networking authority, persistence, tests and integration points.

## Live creation / Aura Presence recovery

The archive has historical references related to scene/environment control, server/networking, rollback concepts and world-change concepts. The recovery target remains a modern typed pipeline:

`command -> permission/safety validation -> typed WorldDelta/SceneDelta -> authoritative state mutation -> visible world change -> persistence -> replication where applicable -> undo/rollback -> test`

Historical code may inform this design, but the modern implementation must conform to current tenancy, auth, security, APIs, project architecture and tests.

## Security handling

At least 14 paths match sensitive-name patterns such as environment/credential/service-key/private-key/secret/token conventions. The extraction process must:

- index sensitive files by metadata/path only;
- never commit recovered secret values to the modern repository;
- never print secret contents into reports, CI logs or catalogue metadata;
- rotate/revoke any historical credential if it is discovered to still be active;
- classify such material `SECURITY-SENSITIVE` until reviewed.

## Current extraction classification

Until provenance review is complete, recovered elements default to `UNCLEAR_PROVENANCE`. The allowed final classifications are:

- `OWNER-AUTHORED / REUSABLE CANDIDATE`
- `ARCHITECTURE REFERENCE`
- `ASSET REFERENCE`
- `THIRD-PARTY / LICENSED`
- `OBSOLETE`
- `SECURITY-SENSITIVE`
- `UNCLEAR PROVENANCE`

## Next extraction passes

1. Build the machine-readable per-file index with SHA-256, format/language, subsystem hints, URL/dependency hints and sensitive-file flags.
2. Recursively inventory `backend/modules.zip` and any later Drive archives.
3. Extract package/dependency/reference URLs and licence/NOTICE evidence.
4. Deduplicate generated `dist`, dependency trees and Git-object noise from owner-source analysis.
5. Score implementation density for the historical game-engine corpus so scaffolds cannot inflate completion.
6. Identify concrete owner-authored candidates for memory/context, speech, scene/environment, multiplayer and Aura Presence recovery.
7. Reconcile each candidate against the current integration architecture before any code is carried forward.

No legacy source is wholesale-imported by this index.
