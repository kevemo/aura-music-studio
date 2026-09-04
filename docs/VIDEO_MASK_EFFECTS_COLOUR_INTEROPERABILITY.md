# Video Studio — mask, item effects and colour interoperability

Wave 12 removes the historical blanket rejection that prevented a Video Studio item from combining an authored mask, supported item effects and colour controls that are already executed by the production grouped compositor.

## Production stage order

The real render state remains untouched. The established non-destructive route executes:

1. source media is read without mutation;
2. supported item effects are pre-rendered to a transient derivative;
3. authored static or keyframed mask alpha is applied/multiplied onto that derivative;
4. the alpha-capable derivative is handed to the grouped compositor;
5. authored crop is applied when present;
6. the already-supported item colour controls are applied to the visible derivative;
7. transform, item opacity/blend, whole-track effects, track opacity/keyframes and track blend continue through their existing stages;
8. the final MP4 is produced and transient derivatives are removed.

## Colour controls unlocked in this combination

Wave 12 only unlocks colour paths that the current `AdvancedVideoCompositor` genuinely executes:

- `exposure`;
- `brightness`;
- `contrast`;
- `saturation`;
- `gamma`.

The validation adapter neutralizes those fields only inside a deep-copied state passed to the historical safety validator. It never rewrites the project or the state that is rendered.

## Deliberately still fail-closed

The following colour fields are not unlocked by Wave 12 because this renderer stage does not currently execute them as item colour controls:

- `temperature`;
- `tint`;
- `highlights`;
- `shadows`.

Unknown colour fields also do not enter the interoperability path. Unsupported effects, unsupported keyframed effect paths, automatic/tracked masks, unsupported mask paths and unsupported item/track automation continue to fail closed.

## Safety and provenance

- source media remains immutable;
- derivatives remain project-local and ephemeral;
- chroma alpha and authored mask alpha remain preserved before colour processing;
- mask+crop interoperability from Wave 11 remains in the inheritance chain;
- automatic subject segmentation, planar/point/motion tracking and AI rotoscoping are not claimed by this wave;
- no renderer output constitutes legal or rights clearance.
