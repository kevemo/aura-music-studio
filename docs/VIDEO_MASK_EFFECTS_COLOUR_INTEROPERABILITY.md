# Video Studio — mask, item effects and colour interoperability

Wave 12 removed the historical blanket rejection that prevented a Video Studio item from combining an authored mask, supported item effects and colour controls already executed by the production grouped compositor. Wave 13 extends that same truthful item-colour stage with bounded temperature and tint execution.

## Production stage order

The real render state remains untouched. The established non-destructive route executes:

1. source media is read without mutation;
2. supported item effects are pre-rendered to a transient derivative;
3. authored static or keyframed mask alpha is applied/multiplied onto that derivative;
4. the alpha-capable derivative is handed to the grouped compositor;
5. authored crop is applied when present;
6. supported item colour controls are applied to the visible derivative;
7. transform, item opacity/blend, whole-track effects, track opacity/keyframes and track blend continue through their existing stages;
8. the final MP4 is produced and transient derivatives are removed.

## Colour controls executable in this combination

The production `AdvancedVideoCompositor` now genuinely executes:

- `exposure`;
- `brightness`;
- `contrast`;
- `saturation`;
- `gamma`;
- `temperature`;
- `tint`.

Temperature and tint are bounded to the normalized creative range `[-1, 1]`. They use FFmpeg `colorbalance` with preserve-lightness enabled, matching the established universal `video.grade.basic` creative semantics. Temperature maps to opposing red/blue midtone and highlight balance; tint maps to bounded green balance.

The mask/effects/colour validation adapter neutralizes these fields only inside a deep-copied state passed to the historical safety validator. It never rewrites the project or the state that is rendered.

## Deliberately still fail-closed

The following item-colour fields remain unsupported by this production item-colour stage:

- `highlights`;
- `shadows`.

Non-zero highlights/shadows now explicitly fail closed in the advanced compositor rather than being silently ignored. Unknown colour fields do not enter the mask/effects interoperability path. Unsupported effects, unsupported keyframed effect paths, automatic/tracked masks, unsupported mask paths and unsupported item/track automation continue to fail closed.

## Safety and provenance

- source media remains immutable;
- derivatives remain project-local and ephemeral;
- chroma alpha and authored mask alpha remain preserved before colour processing;
- mask+crop interoperability from Wave 11 remains in the inheritance chain;
- temperature/tint execute after the effects/mask derivative and optional crop, so they do not alter chroma-key detection or disappear beneath a later grayscale effect;
- automatic subject segmentation, planar/point/motion tracking and AI rotoscoping are not claimed;
- no renderer output constitutes legal or rights clearance.
