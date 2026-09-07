# Aura Effect/System Creator — project workflow

This document describes the executable project workflow implemented by Chat 9 Wave 4.

## Member flow

1. Compose a bounded effect graph from an Aura prompt, or submit an explicit editable graph.
2. Inspect entitlement state for every unique catalogue effect used by the graph.
3. Save reusable graph definitions inside the current tenant project under `work/effect_systems/`.
4. Preview a graph against a real DAW track without mutating project or source media.
5. Treat the compiled graph fingerprint returned by preview as the apply token.
6. Apply only when the submitted graph still compiles to that exact token and all current entitlements pass again.
7. Create a project revision before inserting compiled effects into the DAW track.
8. Restore the recorded pre-apply revision to undo the effect-system application.

## Integrity and safety boundaries

- System and node identifiers are bounded and path-safe.
- Saved graphs include a deterministic SHA-256 compiled fingerprint.
- Same-version graph changes are rejected; changed saved definitions require a version increment.
- Optional prompt provenance is stored only as a validated SHA-256 fingerprint, not raw prompt text.
- Preview does not grant apply authority.
- Apply rechecks server-side permanent effect entitlements and fails before revision/project mutation when access is missing.
- Apply rejects a graph that changed after preview.
- Source media remains immutable; the workflow changes DAW project metadata/effects only.
- The workflow does not execute arbitrary shell commands or user-supplied code.
- Tenant-aware project resolution uses the existing authenticated member storage boundary.

## Production routes

All routes are under `/command-center/api/universal-library/effect-systems` and are registered before the legacy Universal Library catch-all:

- `POST /compose`
- `GET /projects/{project_name}`
- `POST /projects/{project_name}/save`
- `GET /projects/{project_name}/{system_id}`
- `POST /projects/{project_name}/tracks/{track_id}/preview`
- `POST /projects/{project_name}/tracks/{track_id}/apply`
- `POST /projects/{project_name}/restore/{revision_id}`

## Truth boundary

This wave provides the backend and authenticated API workflow for reusable effect systems. It does not claim the visual browser node editor, keyframe automation UI, user-created effect-system marketplace/CCC sale flow, or external production verification are complete.
