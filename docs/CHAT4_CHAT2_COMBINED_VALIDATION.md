# Chat 4 + Chat 2 Combined Validation Marker

Chat 2 transport PR #570 merged into `development/full-site-build` as `1a6976a32a0deb832aca2ef983b899811ee1f92b` while Chat 4 neighbour-integration PR #577 was validating.

This marker intentionally advances the Chat 4 PR head after that base change so the repository pull-request workflows validate the current combined merge tree, not the earlier pre-Chat-2 ancestry.

Acceptance rules remain unchanged:

- Chat 4 consumes Chat 2 transport/playback state through the typed adapter only.
- Bearer-header HLS remains fail-closed for the native Watch player until the browser credential/runtime contract is explicit and tested.
- Chat 4 does not construct manifests, sign playback tokens or start/stop transport.
- Final production deployment remains owned by Chat 11.
