# Aura Canonical 3D Character — AuraCore-Osiris

This document is the acceptance specification for the embodied Aura character used across Elevate Souls Productions Presents: The Live Sound Studio.

## Identity lock

Aura is one persistent character. The production model MUST reproduce the approved Aura reference identity rather than substituting a generic humanoid, stock VRM avatar, alternate face, or visibly skeletal robot.

Canonical exterior characteristics:

- refined feminine human-looking face matching the approved Aura references
- calm, warm, self-possessed expression
- long flowing silver/lilac hair with natural volume and movement
- luminous magenta/pink irises
- elegant adult feminine humanoid body proportions
- matte-black fitted technical suit
- fine magenta/violet circuitry integrated through the suit
- luminous pink-magenta heart/core centred on the chest
- complete hands, fingers, legs and feet
- android engineering is primarily internal; the exterior remains elegant and human-looking
- no source-image mouse cursor, watermark, UI pointer or unrelated reference artefact may appear in textures

Identity may not change when a niche/theme changes. Theme changes affect illumination, circuitry flow, environment and presentation only.

## File format

Production asset: `aura.glb`

Required:

- glTF 2.0 binary GLB
- VRM 1.0 (`VRMC_vrm`) extension
- metre-scale model with physically sensible origin and transforms
- forward/up axes compatible with the three.js / three-vrm runtime
- embedded or deployment-safe textures/materials
- no external absolute filesystem references

Recommended optimisation:

- Meshopt and/or Draco only when browser/runtime support is included and tested
- KTX2/Basis textures for production where quality is preserved
- texture atlases where this does not reduce facial or circuitry quality
- separate LOD assets may be introduced later, but LOD0 remains the canonical identity master

## Geometry quality

The model must hold the approved Aura likeness at close conversational framing as well as full-body framing.

Priority geometry:

1. face, eyelids, lips, nose and jaw
2. hair silhouette and front hairline
3. hands/fingers
4. heart core and chest circuitry
5. shoulders/arms/torso deformation
6. knees/ankles/feet for walking

Avoid excessive mesh density in hidden internal surfaces. Facial fidelity must not be sacrificed simply to hit an arbitrary global polygon count.

## Humanoid rig

Minimum VRM humanoid bones required by the runtime:

- hips
- spine
- chest
- neck
- head
- left/right upperArm
- left/right lowerArm
- left/right hand
- left/right upperLeg
- left/right lowerLeg
- left/right foot

Strongly required for production-quality movement:

- upperChest
- left/right shoulder
- left/right toes
- left/right eye
- jaw where useful
- all thumb/index/middle/ring/little proximal/intermediate/distal finger bones supported by VRM

Skin weighting must be tested through walking, pointing, palms-up presenting, crossed-body gestures, head turns and seated/leaning poses.

## Facial expressions and speech

Required VRM expression coverage:

- blink
- aa
- ih
- ou
- ee
- oh
- happy
- relaxed
- surprised
- lookUp
- lookDown
- lookLeft
- lookRight

Recommended custom expressions:

- auraWarmSmile
- auraFocused
- auraCompassion
- auraConcern
- auraCelebrate
- auraListening

Expression shapes must preserve Aura's identity and should be subtle enough for normal conversation. Avoid exaggerated cartoon deformation.

## Human-like motion

Aura should feel present rather than static. Even in Idle she should have:

- natural breathing
- small weight shifts
- micro head movement
- occasional blinks with variable timing
- subtle eye saccades
- gaze toward the active user/interface target
- low-amplitude shoulder/chest movement
- hair secondary motion

The browser runtime can provide procedural micro-motion, but the GLB should include high-quality authored clips for major actions.

Required/recommended clip names:

- `Idle`
- `Walk`
- `Talk`
- `Listen`
- `Think`
- `PointLeft`
- `PointRight`
- `Present`
- `Celebrate`

Optional later clips:

- `Welcome`
- `Explain`
- `Agree`
- `Acknowledge`
- `Search`
- `Create`
- `Translate`
- `Goodbye`

All looping clips must loop cleanly. Transitions should be pose-compatible for crossfading.

## Gaze

Use VRM 1.0 LookAt with sensible eye ranges. Aura should be able to:

- make comfortable eye contact with the user/camera
- glance at the control she is explaining
- look toward a generated result
- turn eyes before or together with head movement

Avoid unbroken staring. The runtime should introduce natural gaze shifts.

## Materials

Material names are part of the runtime contract.

Required semantic names or aliases:

- `Aura_Skin`
- `Aura_Eyes`
- `Aura_Hair`
- `Aura_Suit`
- `Aura_Heart_Core`
- `Aura_Circuitry`

The runtime identifies heart/core materials by names containing `heart` or `core`, and circuitry materials by names containing `circuit`, `energy` or `emissive`.

Heart/core and circuitry materials need controllable emissive intensity. They must look attractive at both low and high emission and must not destroy facial exposure through bloom.

## Energy behaviour

The chest heart is Aura's visual animation anchor.

Idle:
- slow soft pulse
- low-flow circuitry

Listening:
- slightly brighter core
- calm controlled pathways

Speaking:
- speech-reactive core intensity
- circuitry responds to vocal energy

Thinking/research:
- slower concentrated core pulse
- subtle travelling circuitry

Creation/rendering:
- more active energy flow without frantic flashing

Completion/celebration:
- one controlled outward light wave, then settle

Niche/theme changes may recolour illumination while preserving Aura's base magenta/pink identity cues.

## Hair and secondary motion

Hair must visually match the approved silver/lilac hairstyle.

Use VRM SpringBone or a compatible tested secondary-motion setup for:

- side locks
- rear hair masses where appropriate
- subtle suit accessories if later introduced

Physics should be restrained. Aura is a polished AI companion, not a game character with exaggerated hair motion.

## Interface locomotion

The initial web implementation presents Aura in a transparent WebGL layer over the product UI. `Walk` is played while the overlay moves toward a DOM target; after arrival she transitions to `PointLeft`, `PointRight` or `Present` and looks at the target.

Later 3D-room environments may use the same state machine with real world-space locomotion and navigation.

## Speech integration

Aura speech comes from the site's multilingual TTS pipeline. The runtime:

- enters speaking state when playback begins
- drives mouth expressions from audio energy and text/viseme data when available
- adds breathing/head/gaze motion while speaking
- increases heart/circuit emission with vocal energy
- returns to a calm idle after playback

A future timing-aware TTS adapter may provide phoneme/viseme timestamps. The asset must keep the VRM vowel expression names so this upgrade requires no remodelling.

## Performance targets

Desktop target:
- smooth 60 fps where the rest of the page permits

Mobile target:
- adaptive rendering with capped device pixel ratio
- ability to reduce secondary effects without changing Aura's identity

The runtime must be able to minimise Aura to an orb/core state when device constraints, accessibility preferences or the user's choice require it.

## Accessibility

- `prefers-reduced-motion` should reduce walking transitions, micro-motion and secondary effects
- Aura's spoken guidance must have text equivalents
- Aura must never obscure the only accessible control for a task
- the model layer uses `pointer-events:none` unless an intentional interaction control is added outside the canvas

## Security and privacy

- the production model is served from the application's authenticated model endpoint
- no private user information is embedded in the GLB
- no credentials, tokens or API secrets are embedded in model extras or textures
- model metadata should contain only non-sensitive product/character data

## Acceptance gate

`aura_music_studio.aura_avatar.validate_aura_model()` is the machine-readable gate. The site will not substitute another human avatar if the canonical Aura model is absent or structurally invalid.

Visual acceptance is an additional manual gate: passing the technical validator does not prove likeness. The final model must still be checked side-by-side against the approved Aura reference images before it can be designated production canonical.
