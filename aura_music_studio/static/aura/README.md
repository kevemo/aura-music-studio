# Aura production avatar assets

This directory is the default deployment root for Aura's browser/avatar assets.

The canonical rigged model is expected at `aura.glb` unless `AURA_AVATAR_MODEL_PATH` selects another path under this directory.

Do not commit a placeholder file named `aura.glb`: the runtime deliberately uses file existence as part of its truthful readiness check. The production GLB must be an actual reviewed, licensed, optimized and rigged Aura model.

Large binary character/model/texture files should normally be delivered by the deployment artifact/model pipeline rather than encoded into UTF-8 source files.

See `docs/AURA_AVATAR_RUNTIME.md` for the complete rig/state contract.
