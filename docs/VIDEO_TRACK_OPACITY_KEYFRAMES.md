# Video Studio — grouped track opacity keyframes

Wave 9 adds render-time execution for authored Video Studio track-opacity automation while preserving the existing grouped compositor stage order.

## Supported

The production Video Studio renderer accepts either `opacity` or `track.opacity` in `EditorTrack.keyframes`. Both names address the same track-opacity property and must not be authored at the same time.

Keyframes use the editor's established interpolation values:

- `hold`
- `linear`
- `smooth`
- `bezier`

Opacity values must be finite numbers from 0 through 1. Times must be finite and non-negative.

The opacity automation executes against sequence time after the track's items have been composed and after whole-track effects, but before the track blend mode is applied to the sequence canvas. This preserves the professional grouped-track ordering already established by the Video Studio compositor.

The FFmpeg runtime evaluates the authored opacity expression per frame against the completed RGBA track group. Original source media is not modified.

## Existing Video Studio capabilities preserved

Wave 9 extends rather than replaces the existing production lineage:

1. grouped track composition and item/track blend modes;
2. supported item and whole-track effects;
3. chroma-key alpha preservation;
4. authored static and keyframed roto masks;
5. grouped track opacity automation;
6. final sequence composition and export.

Track opacity keyframes can therefore coexist with the already-supported chroma and authored-mask paths.

## Fail-closed boundary

The following remain unsupported and fail closed rather than disappearing from export:

- track keyframe paths other than `opacity` / `track.opacity`;
- keyframed whole-track effect parameters or mixes;
- automatic mask tracking;
- unsupported item/effect/mask state inherited from the existing renderer contract.

## Authoring surface status

`EditorTrack` already persists a generic keyframe map and Wave 9 executes supported track opacity data from that project state. The current public editor mutation surface still exposes `set_item_keyframes` but does not yet expose a dedicated `set_track_keyframes` operation. That authoring endpoint is intentionally recorded as the next completion item rather than being claimed complete here.

This means Wave 9 closes the production render/runtime gap first while keeping the remaining editor-authoring gap explicit.
