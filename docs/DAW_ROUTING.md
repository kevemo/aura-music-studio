# ESP Live Sound Studio — Pro DAW Routing

Version 0.16 adds professional parallel routing, non-destructive crossfades and reversible track freeze/bounce to Aura's visual DAW.

## Auxiliary buses and sends

A Pro session can create `bus` tracks. Ordinary audio tracks carry explicit post-fader `Send` records containing a destination bus, level in dB and enabled state.

The real-audio mixer renders the source track first, including its inserts and fader/pan automation. A level-adjusted waveform copy is then sent in parallel to the selected bus. The bus combines its incoming sends, runs its own effect rack and automation, then returns that waveform to the master mix.

Initial safe bus presets are:

- `clean` — transparent parallel bus.
- `reverb` — Aura Reverb using the Studio's real waveform reverb processor.
- `delay` — Aura Delay using the Studio's real waveform delay processor.

Deleting a bus also deletes every send that targeted it, preventing dangling routing references.

## Crossfades

Crossfades are metadata edits only. The two source files remain untouched. Aura overlaps the right clip with the tail of the left clip and sets matching fade-out/fade-in durations. Crossfades require two real-audio clips on the same track and take lane.

## Track bounce

Bounce renders one ordinary audio track through its current clip edits, insert effects and automation into a 24-bit WAV in the member's private `output/daw/bounces/` area. Bounce does not change the session. The API returns only secure member stream/download URLs, never a server filesystem path.

## Track freeze / thaw

Freeze is a reversible CPU-saving operation for Pro sessions:

1. Aura renders the track to a real waveform.
2. The editable clips, effect rack, automation, volume and pan are stored inside private track metadata.
3. The track is temporarily replaced by one 24-bit frozen waveform clip.
4. Inserts and automation are cleared from the active frozen track because they are already represented in the waveform.
5. Existing auxiliary sends remain active.

Thaw restores the original clips, effects, automation, fader and pan from the private freeze state. Frozen render files remain in the private work area so revision history that references them is not invalidated.

## Membership and privacy

Routing, buses, sends, crossfades, bounce and freeze/thaw require Pro. A downgraded Base account keeps all project files, but a session containing multitrack, take-lane, automation, bus/send or frozen-track state remains locked until Pro is restored.

Browser-safe session JSON exposes only routing controls required by the UI. Private `freeze_state`, clip source paths and other internal track metadata are deliberately omitted.

## Real-audio invariant

Buses and freeze cannot manufacture audio from MIDI or symbolic control data. The final mixer continues to require real waveform clips from recordings, neural generation or approved waveform processing.
