# Video Studio — grouped track opacity keyframes

Wave 9 added render-time execution for Video Studio track-opacity automation. Wave 10 completes the authoring loop by exposing track keyframes through the professional editor mutation API while preserving the existing grouped compositor stage order.

## Supported render contract

The production Video Studio renderer accepts either `opacity` or `track.opacity` in `EditorTrack.keyframes`. Both names address the same track-opacity property and must not be authored at the same time.

Keyframes use the editor's established interpolation values:

- `hold`
- `linear`
- `smooth`
- `bezier`

Opacity values must be finite numbers from 0 through 1. Times must be finite and non-negative.

The opacity automation executes against sequence time after the track's items have been composed and after whole-track effects, but before the track blend mode is applied to the sequence canvas. This preserves the professional grouped-track ordering already established by the Video Studio compositor.

The FFmpeg runtime evaluates the authored opacity expression per frame against the completed RGBA track group. Original source media is not modified.

## Public authoring surface

Wave 10 exposes:

`POST /creative/projects/{project_name}/editor/tracks/{track_id}/keyframes`

The endpoint uses the same `KeyframesRequest` contract as item automation and requires the existing Pro automation entitlement. It:

- validates every supplied keyframe through `EditorKeyframe`;
- deduplicates identical times deterministically, keeping the final authored point;
- sorts the stored automation by timeline time;
- rejects writes to locked tracks;
- records before/after snapshots in the normal editor operation graph;
- participates in Undo and Redo;
- attributes the operation to the authenticated editor actor;
- returns the updated track and complete editor state.

The editor capability response now advertises `item` and `track` as keyframe targets and identifies `opacity` / `track.opacity` as the currently render-safe Video Studio track paths.

Unsupported track keyframe paths may still be authored into the non-destructive project model, matching item-keyframe behavior, but production export fails closed rather than silently dropping unsupported state.

## Existing Video Studio capabilities preserved

Wave 10 extends rather than replaces the existing production lineage:

1. grouped track composition and item/track blend modes;
2. supported item and whole-track effects;
3. supported universal whole-track effect automation, including the established grade/mix paths;
4. chroma-key alpha preservation;
5. authored static and keyframed roto masks;
6. grouped track opacity automation;
7. public track-keyframe authoring with undo/history;
8. final sequence composition and export.

Track opacity keyframes can therefore coexist with the already-supported chroma, authored-mask, and safe universal track-effect automation paths.

## Fail-closed boundary

The following remain unsupported and fail closed rather than disappearing from export:

- track keyframe paths other than `opacity` / `track.opacity`;
- universal whole-track effect keyframe paths outside the already-supported safe parameter/mix contract;
- automatic mask tracking;
- unsupported item/effect/mask state inherited from the existing renderer contract.

## Non-destructive truth boundary

Track-keyframe authoring changes only `pro_editor.json` metadata and editor history. Source media is never rewritten. Wave 10 provides a real mutation/API path for creator-authored automation; it does not claim automatic tracking, automatic animation generation, or support for renderer paths that remain fail-closed.
