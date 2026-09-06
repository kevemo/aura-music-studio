# Chat 1 — Rhiannon canonical timing consumption

This tranche implements Chat 1's presentation-side consumer for `RhiannonVoice.timing/v1`. It does not create or replace the Chat 2 speech, cloning, conversion, phoneme or viseme-generation runtime.

## Precise timing path

`RhiannonTimingHost.startPrecise(audio, timingTrack, jobId)` accepts only the canonical timing protocol with `precise_timing=true`, a non-fallback source, canonical allowlisted visemes and a bounded non-overlapping schedule. Playback is driven from the real `HTMLMediaElement.currentTime` clock and forwarded through `RhiannonTurnHost.speechFrame(...)`, preserving the existing `AuraHost.performance/v1` avatar bus.

The consumer fails closed when the speech job is stale, the timing schema is malformed, the viseme schedule overlaps or exceeds the browser budget, or media/timing duration diverges beyond the bounded tolerance. It does not execute arbitrary animation code.

## Current audio-only fallback

The current generic speech endpoint still returns completed audio without canonical phoneme/viseme timing. That path now uses Web Audio amplitude analysis against the real playback media element and sends only bounded `sil` / `aa` mouth states. Every fallback frame is explicitly marked:

- `precise_timing=false`
- `lip_sync_mode=amplitude_fallback`
- `phoneme_accurate=false`

This is intentionally not described as precise lip sync.

## Preserved authority

- Chat 1: Rhiannon identity, turn state, timing consumption and embodied presentation.
- Chat 2: speech/vocal generation, cloning/conversion and timing generation.
- Chat 6: commercial entitlement/payment truth.
- Chat 7: production infrastructure, model packaging, security and deployment hardening.

No provider credentials, voice models, raw embeddings, payment authority, LIVE transport, role elevation, arbitrary shell/plugin execution or client entitlement authority are introduced.

## Remaining completion boundary

Precise end-to-end Rhiannon lip sync is still not complete on this tranche alone. It requires a real configured Chat 2/provider/self-host voice runtime to return canonical timing metadata together with audio, plus a production Rhiannon rig containing the required authored facial/viseme targets. The recovered legacy static GLB remains reference-only and does not satisfy that rig gate.
