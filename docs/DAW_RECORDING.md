# ESP Live Sound Studio — DAW Recording

Version 0.15 adds dry browser recording directly inside Aura's visual DAW.

## Recording contract

- Browser/device captures are normalized server-side to stereo 48 kHz, 24-bit PCM WAV.
- Recording remains dry. Monitor effects are browser monitoring only and are not baked into the stored source.
- Captures are ingested into the member's private project asset library with a rights attestation and recording-role tags.
- The browser never receives the private source filesystem path.

## Membership behavior

### Base

Base members can record at the DAW playhead into their single editable timeline lane. If a project already contains Pro multitrack, automation or alternate-take state, the advanced session is preserved but cannot be edited through Base after downgrade.

### Pro

Pro members can target independent tracks, record alternate take lanes and use punch recording. Repeated loop passes can be placed into successive take lanes for auditioning and phrase comping.

## Punch recording

Punch mode requires a target track, a punch start and a punch end. Aura stores the full normalized dry capture as a private asset, while the DAW clip duration is constrained to the selected punch region. This keeps the source non-destructive and allows later trim/comp decisions.

## Safety boundaries

- 250 MB maximum browser capture per request.
- Recording role is restricted to the Studio's known audio roles.
- Rights confirmation is required before a recording enters the asset library.
- Base cannot use old Pro session structure as an entitlement bypass.
- Recording revisions snapshot DAW metadata before the new take is attached.
- Final session rendering still accepts real waveform audio only; MIDI/symbolic guides cannot become a final master.
