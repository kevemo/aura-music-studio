# Aura Game Forge Asset Pipeline

## Purpose

This stage connects Pulsar-Frequency House's existing Creative Library to Aura Game Forge without exposing member filesystem paths or executing creator-supplied code.

Creators can import finished image, video, audio and music outputs into a game as immutable private snapshots. Each snapshot records its Creative DNA origin, source element/version, SHA-256 checksum, byte size, intended game role and rights attestation.

## Security and tenancy

- Asset imports resolve only through the current member's tenant-scoped Creative Project store.
- Absolute paths and project traversal are rejected.
- Supported local media extensions are allow-listed.
- Imported files are copied into the private game directory under generated asset IDs.
- API responses expose stable media endpoints, never the imported filesystem filename/path.
- The default per-asset import limit is 256 MiB and can be reduced with `AURA_GAME_ASSET_MAX_BYTES`.
- Imported media is served only through authenticated Basic/Pro Game Forge routes.

## Build and publishing integrity

Game Forge's integrity hash now covers:

1. Game DNA.
2. Aura World DNA.
3. The complete imported asset manifest.

Changing an asset, removing it, or changing its rights attestation therefore makes previous builds and rating/compliance scans stale.

Public-test assessment also fails closed when:

- an imported asset has no confirmed rights/attestation;
- the private asset snapshot is missing;
- its byte size or SHA-256 no longer matches the recorded snapshot.

This keeps a game from being publicly tested against media that differs from the content reviewed by Aura's provisional safety/rating preflight.

## API

- `GET /api/game-forge/games/{game_id}/assets/library`
- `GET /api/game-forge/games/{game_id}/assets`
- `POST /api/game-forge/games/{game_id}/assets`
- `PATCH /api/game-forge/games/{game_id}/assets/{asset_id}/rights`
- `DELETE /api/game-forge/games/{game_id}/assets/{asset_id}`
- `GET /api/game-forge/games/{game_id}/assets/{asset_id}/media`

The first stage intentionally imports Creative Library outputs as snapshots rather than hot-linking the original file. Later native-renderer stages can consume these verified snapshots for textures, sprites, soundtracks, SFX, cutscenes and related game content without weakening provenance or tenant isolation.
