# Aura Game Forge Asset Pipeline

## Purpose

This stage connects Pulsar-Frequency House's existing Creative Library to Aura Game Forge without exposing member filesystem paths or executing creator-supplied code.

Creators can import finished image, video, audio and music outputs into a game as immutable private snapshots. Each snapshot records its Creative DNA origin, source element/version, SHA-256 checksum, byte size, intended game role and rights attestation.

## Security and tenancy

- Asset imports resolve only through the current member's tenant-scoped Creative Project store.
- Absolute paths and project traversal are rejected.
- Supported local media extensions are allow-listed.
- Imported files are copied into the private game directory under generated asset IDs.
- API responses expose stable media endpoints, never tenant filesystem paths.
- The default per-asset import limit is 256 MiB and can be reduced with `AURA_GAME_ASSET_MAX_BYTES`.
- Private runtime media is served only through authenticated Basic/Pro Game Forge routes.
- Published runtime media is served only through authenticated Game Forge playtesting access.

## Build and publishing integrity

Game Forge's integrity hash covers:

1. Game DNA.
2. Aura World DNA.
3. The complete imported asset manifest.

Changing an asset, removing it, or changing its rights attestation therefore makes previous builds and rating/compliance scans stale.

Public-test assessment also fails closed when:

- an imported asset has no confirmed rights/attestation;
- the private asset snapshot is missing;
- its byte size or SHA-256 no longer matches the recorded snapshot.

Publishing then performs a second immutable copy into the public game snapshot. The copied bytes are checksum-verified again. `assets.json` contains only the closed runtime projection: asset ID, kind, label, game role, relative media URL, MIME type, checksum and byte size. It deliberately excludes Creative Project names, source element IDs, filesystem paths and rights-attestation text.

If any media snapshot fails, publication is rolled back instead of leaving a partially published game.

## Native renderer consumption

Aura2D and Aura3D now consume the verified snapshot contract directly.

Aura2D can:

- use an image asset as the live Canvas world/background;
- use music/audio assets as a browser-gesture-controlled soundtrack;
- expose video assets as in-game cutscenes.

Aura3D can:

- load an image asset into a native WebGL2 texture and apply it according to role hints such as terrain, world, player or texture;
- use music/audio assets as a soundtrack;
- expose video assets as cutscenes.

Media URLs are relative (`media/<generated-asset-id>.<ext>`). The same reviewed `play.html` therefore resolves against either the private playtest frame or the immutable public snapshot without embedding private tenant locations.

The runtime Content Security Policy keeps `connect-src 'none'`. It permits only same-origin image/media resource loads plus `data:`/`blob:` fallbacks. Runtime JavaScript still cannot use provider calls, `fetch`, XHR, WebSockets, CDNs or arbitrary external engines.

## API

- `GET /api/game-forge/games/{game_id}/assets/library`
- `GET /api/game-forge/games/{game_id}/assets`
- `POST /api/game-forge/games/{game_id}/assets`
- `PATCH /api/game-forge/games/{game_id}/assets/{asset_id}/rights`
- `DELETE /api/game-forge/games/{game_id}/assets/{asset_id}`
- `GET /api/game-forge/games/{game_id}/assets/{asset_id}/media`
- `GET /api/game-forge/games/{game_id}/media/{filename}` — private runtime media
- `GET /game-gallery/{public_id}/media/{filename}` — immutable public runtime media

## Next renderer stages

The snapshot contract is now suitable for richer renderer-specific asset classes without weakening provenance. Planned extensions include sprite atlases, material maps, 3D models, skeletal animation, VFX, spatial audio/SFX buses, cinematic timelines and asset-to-entity binding controls through Aura prompts and direct editing.
