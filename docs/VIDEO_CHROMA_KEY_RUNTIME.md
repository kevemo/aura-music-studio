# Video Studio Professional Chroma Key Runtime

Chat 3 Wave 7 adds a real alpha-preserving chroma/colour key path to the professional Video Studio renderer.

## Universal contract

Effect ID:

`video.key.chroma`

Supported scopes:

- video clip item;
- complete visual track.

Supported static parameters:

- `screen`: `green`, `blue` or `custom`;
- `color`: `green`, `blue`, `#RRGGBB`, `0xRRGGBB` or an 8-digit hex colour (alpha component is ignored for the key target);
- `similarity`: bounded to `0.00001..1.0`;
- `blend`: bounded to `0..1` for matte-edge softness;
- `despill`: bounded to `0..1` for green/blue-screen spill reduction;
- `despill_expand`: bounded to `0..1`.

Green and blue screen modes may run FFmpeg's dedicated `despill` filter after the key. Custom key colours do not pretend to have a general-purpose despill model; they key the selected RGB colour without applying green/blue-specific spill correction.

## Alpha preservation

Clip-local universal effects normally use a temporary H.264/yuv420p derivative before grouped composition. That path cannot preserve transparency and therefore is not used for keyed clips.

When a clip effect chain contains an active `video.key.chroma`, the compositor instead:

1. keeps the filter graph in RGBA;
2. applies RGB `colorkey` and optional green/blue `despill`;
3. keeps the resulting matte in alpha;
4. writes a project-local lossless QuickTime RLE (`qtrle`) MOV using ARGB;
5. passes that transparent derivative into the established grouped-track compositor;
6. composites it over the sequence/lower tracks;
7. produces the normal final MP4;
8. deletes the temporary keyed MOV after the final render.

Original source media is never modified.

Whole-track keying does not need a derivative: the track is already an RGBA group. The keyer runs after item composition and before track opacity/blend, so keyed pixels reveal lower tracks correctly.

## Automation boundary

The existing universal wet/dry system supports keyframed effect `mix`, including for the chroma key contract.

The keyer's own `color`, `similarity`, `blend`, `despill` and `despill_expand` parameters remain static in this wave. Attempts to keyframe those parameter paths fail closed instead of flattening, guessing or frame-interpolating a matte.

## Current FFmpeg semantics

The implementation uses FFmpeg's RGB `colorkey` filter, whose current runtime options include key colour, similarity and blend, and FFmpeg's `despill` filter for green/blue-screen contamination. The renderer keeps all parameter values within FFmpeg's documented bounds and retains RGBA across the keying stage.

## Truthfulness and release contract

This is an executable local VFX feature, not a generative placeholder. Pixel-level regression tests render a red foreground box against a green screen and verify that keyed background pixels reveal the blue sequence background while the red foreground survives.

The effect does not perform automatic subject segmentation, AI rotoscoping, motion tracking or legal/rights clearance. Those remain separate capabilities and must not be inferred from chroma-key support.
