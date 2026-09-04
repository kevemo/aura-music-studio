# Video Studio — mask and crop interoperability

Wave 11 removes the historical blanket rejection that prevented an authored Video Studio mask and crop from being rendered on the same item.

## Execution order

The production renderer keeps the existing non-destructive stage order:

1. source media is read without mutation;
2. supported item effects/chroma are rendered to a transient derivative when required;
3. authored static or keyframed masks are converted to alpha and multiplied with any existing alpha matte;
4. the masked derivative remains alpha-capable;
5. the grouped compositor applies the item's authored crop to that derivative;
6. transform, colour, item opacity, item blend, whole-track effects, track opacity/keyframes and track blend continue through their established stages;
7. the final MP4 is rendered and transient derivatives are removed.

Mask points remain normalized to the original source canvas. Wave 11 does not rewrite mask coordinates to crop space; the crop happens later, so authored mask geometry keeps the same meaning it had before crop was enabled.

## Validation design

The project and render state are not altered to make the combination pass validation. Wave 11 deep-copies only the state supplied to the historical validator and resets crop to its neutral values only for enabled items that also contain enabled masks. The real render state retains the crop.

All other validation continues to execute. In particular:

- unsupported mask shapes remain fail-closed;
- unsupported keyframed mask paths remain fail-closed;
- non-empty automatic/tracking state remains fail-closed;
- the existing mask + effects + non-default colour-adjustment safety boundary remains fail-closed;
- unsupported item/track automation remains fail-closed;
- source media remains immutable.

## Supported mask forms

The interoperability applies to the already-supported Video Studio masks:

- rectangle;
- ellipse;
- polygon;
- path;
- add/subtract/intersect composition;
- inversion;
- opacity;
- feather;
- expansion;
- authored keyframes for points, opacity, feather and expansion.

It also preserves alpha created by the professional chroma keyer before the authored mask and crop stages.

## Truth boundary

Wave 11 implements deterministic authored mask + crop composition. It does not claim automatic subject segmentation, planar tracking, point tracking, motion tracking, or AI rotoscoping. Those remain separate capabilities and must not be inferred from manual/keyframed mask support.
