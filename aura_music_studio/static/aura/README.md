# Aura production avatar assets

This directory is the default deployment root for Aura's browser/avatar assets.

The canonical rigged model is expected at `aura.glb` unless `AURA_AVATAR_MODEL_PATH` selects another path under this directory.

Do not commit a placeholder file named `aura.glb`: the runtime deliberately uses file existence and structural/rig validation as part of its truthful readiness check. The production GLB must be an actual reviewed, licensed, optimized and rigged Aura model.

The deployable production asset must satisfy the state and layered-performance contract documented in `docs/AURA_AVATAR_RUNTIME.md`, including the required base-state clips, complete viseme channel set, gaze layers, blink and gesture coverage. A structurally valid GLB plus an operator-ready flag is not sufficient if the performance rig is incomplete.

Large binary character/model/texture files should normally be delivered by the deployment artifact/model pipeline rather than encoded into UTF-8 source files. The final likeness-grade binary and real browser/device validation remain deployment evidence, not source-code claims.

See `docs/AURA_AVATAR_RUNTIME.md` for the complete rig, performance and readiness contract.
