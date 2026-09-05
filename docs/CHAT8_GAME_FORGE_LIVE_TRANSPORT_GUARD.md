# Chat 8 Game Forge LIVE Transport Guard Contract

Status: additive compatibility contract for `development/full-site-build`.

## Purpose

Game Forge already exposed stable LIVE source-control routes before the canonical Chat 2 `game_project` programme-source bridge was added. Those legacy routes must remain compatible, but once a Game Forge source is bound to Shared Sky they must not leave Chat 2 with a stale `ready` programme-source record after Game Forge hides, revokes, detaches or otherwise changes the source.

`aura_music_studio.game_forge_live_transport_guard` closes that compatibility gap without creating a second LIVE state machine or transport engine.

## Route precedence

The guard router is mounted through `aura_music_studio.game_forge_project_binding.router` **before** `game_forge_live_integration.router`.

It intentionally claims the established methods and paths first:

- `PATCH /api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/presentation`
- `POST /api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/promote-version`
- `POST /api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/emergency-hide`
- `DELETE /api/game-forge/games/{game_id}/live/sources/{source_adapter_id}`

Each guard endpoint calls the existing canonical Game Forge mutation function first and then synchronises an already-bound canonical Chat 2 programme source. Existing clients and the embedded Go Live & Create portal therefore keep the same URLs.

## State mapping

Presentation transition:

- `brb` -> Game Forge source is non-active and the bound Chat 2 `game_project` source becomes non-ready/`failed`.
- a valid active/ready presentation such as `playtest` -> the existing programme source may return to `ready`.

Version promotion:

- preserves the same source/session identity;
- refreshes bounded transport capabilities so the canonical source record contains the promoted Game Forge version/build metadata.

Emergency Hide:

- always forces the bound programme source non-ready;
- `revoke=false` records reason `game_forge_emergency_hide`;
- `revoke=true` records reason `game_forge_source_revoked`.

Detach:

- always forces the bound programme source non-ready with reason `game_forge_source_detached`.

An unbound Game Forge source remains truthfully reported as `unbound`; the guard does not implicitly create a Shared Sky programme source.

## Ownership boundary

The guard does not own or write destination credentials, relays, transcode state, ingest sessions, destination delivery state or Chat 3 programme composition.

It delegates source-record synchronisation to `game_forge_shared_sky_transport._set_programme_source_state`, which first resolves the existing source through the canonical Chat 2 transport domain and updates only the bounded `game_project` source state/capabilities.

The direct Chat 2 programme-source table update inside that helper remains a narrow compatibility seam because the current Chat 2 transport domain does not yet expose a public generic source-state mutation method. Chat 11 should prefer replacing this seam with a canonical Chat 2 API when one exists rather than duplicating the transport engine in Game Forge.

## Privacy and safety

Transport capabilities remain bounded to safe Game Forge metadata. The synchronisation path does not serialize source code, filesystem paths, credentials, private editor documents, logs, destination tokens or whole-window capture data.

Emergency Hide and detach do not delete the Game Forge project, terminate autosave or destroy the playtest build.

## Tests

`tests/test_game_forge_live_transport_guard.py` verifies:

- guard route precedence over legacy duplicate paths;
- Emergency Hide forces a bound transport source non-ready;
- revocation uses an explicit revocation reason;
- detach forces a bound source non-ready;
- BRB and resume synchronise the same programme source without creating a new LIVE session;
- version promotion refreshes the existing programme-source state/capability projection;
- unbound sources remain truthfully `unbound`.

The wider Chat 8 suites continue to cover safe-source privacy, rights, version/build pinning, canonical transport binding, Visual Logic, provider-neutral 3D jobs and production route composition.
