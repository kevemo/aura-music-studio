# Chat 5 — LIVE Voice, Authorised Cloning, Real-Time Processing, Captions & Broadcast Integration

This document is the Chat 5 implementation addendum for Shared Skies Streaming Studios LIVE voice. It does not transfer voice-engine ownership into Chat 5.

## Authority

- **Chat 1** owns Rhiannon identity/voice identity and the provider-neutral Rhiannon voice/timing/provenance contracts.
- **Chat 2** owns reusable consent-gated voice profiles, speaking/singing cloning, voice conversion, vocal generation/processing and underlying speech/voice runtimes.
- **Chat 5** owns LIVE microphone/audio routing, Preview/Programme presentation, monitoring, broadcast-safe voice controls, TTS/caption/translation presentation when executable runtimes exist, co-host/guest audio routing, LIVE recording presentation and emergency continuity controls.
- **Chat 6** owns subscription/payment/Cosmic Creation Coin/Gift and commercial entitlement truth.
- **Chat 7** owns production workers, self-hosting, deployment, security and runtime monitoring.
- **Chat 8** independently audits implementation and launch readiness.

Chat 5 must never create a second voice-profile database, cloning engine or entitlement authority.

## Consent boundary

The product must keep these permissions separate: microphone transmission, LIVE recording, recording reuse, reusable voice-clone creation, public clone use and commercial clone use. Joining or being recorded on a LIVE is not consent to cloning. Chat 5 consumes authorised profile references only after the canonical Chat 2 authority can bind them server-side to the LIVE session.

## Current executable tranche

`shared_skies_live_voice.py` adds two bounded production surfaces:

1. `GET /shared-sky/studio/api/sessions/{session_id}/voice/readiness`
   - inventories real Preview and committed Programme audio/voice sources through the existing Shared Skies control room;
   - returns only redacted mixer/provenance/processor presentation state;
   - consumes the canonical Chat 1 Rhiannon capability/provenance contract;
   - reports file/batch STT as `offline_only`, never as LIVE captions;
   - reports configured-but-unverified speech synthesis as configured, not healthy/streaming;
   - keeps real-time voice conversion, streaming speech, LIVE TTS queue, real-time captions, translation and spoken private Auto Cue unavailable until executable adapters prove them;
   - exposes no raw model, embedding, training/reference recording, provider secret or client financial authority.

2. `POST /shared-sky/studio/api/sessions/{session_id}/voice/emergency/mute/{source_id}`
   - copies the immutable committed Programme snapshot;
   - mutes exactly one audio/voice source using the canonical control-room mixer state;
   - commits through the canonical transport adapter with optimistic session versioning;
   - is idempotent when the source is already muted;
   - does not depend on the failing AI/voice provider;
   - does not stop LIVE transport, alter Battle score, or mutate Coin/Gift/payment state;
   - deliberately does **not** claim to bypass a Chat 2 voice processor until an executable processor-bypass contract exists.

## Source and bus truth

The existing control-room source lineage remains canonical. Chat 5 projects audio-capable sources such as microphone, system/application audio, media audio, capture-card audio, remote guest audio and creative-studio/game sources into a redacted LIVE voice view. Internal legacy source IDs remain stable for compatibility.

Preview and Programme are separate. A private/backstage source may be present in Preview but cannot be active on Programme. Rhiannon private cue/spoken coaching must remain a separate future monitor route from Rhiannon Programme speech.

## Rhiannon and Chat 2 integration

The merged `RhiannonVoice.capabilities/v1`, `RhiannonVoice.timing/v1` and `RhiannonVoice.asset-provenance/v1` contracts are consumed rather than redefined. Chat 1 currently fails closed for cloned speech, singing voice, voice conversion, phoneme/viseme timing and streaming speech unless owning-runtime evidence is attached.

Chat 2's Voice House already records explicit consent evidence, allowed-use modes and revocation, and its rights ledger reloads a profile at the execution boundary before authorising use. Chat 5 does not expose a LIVE profile selector until there is a server-authoritative binding between a Shared Skies LIVE session and that Chat 2 profile authority.

## Real-time truth

A batch subprocess, completed audio file or configured provider URL is not real-time evidence. Real-time voice conversion remains incomplete until capture, processor, network/provider, jitter/buffer, latency, underrun/recovery and raw-microphone failover behaviour are executable and measured. The same rule applies to real-time captions, translation and dubbing.

The current local STT integration consumes a completed audio file. Therefore the LIVE readiness contract reports it as offline-only even when configured. The current speech synthesis handoff generates completed audio; it does not by itself constitute a bounded LIVE TTS queue or streaming speech path.

## Required next tranches

Future Chat 5 work, once the owning contracts exist, should attach:

- server-authoritative Chat 2 profile selection and revocation re-check at execution;
- real-time processor adapter with authenticated health/latency/jitter/queue metrics and immediate raw-mic bypass;
- bounded LIVE TTS queue with moderation, deduplication, limits, skip/cancel/clear and emergency priority;
- separate private-cue monitor and Programme speech buses for Rhiannon/Auto Cue;
- executable streaming transcription/caption timing and multi-speaker source association;
- executable translation/subtitle adapter and viewer language selection;
- per-participant/co-host processor isolation;
- isolated-track recording/provenance where privacy configuration permits;
- eight-participant concurrency and latency/load evidence.

None of those items is marked complete merely because a selector, waveform, mock caption stream or offline model exists.

## Security invariants

The LIVE layer must reject cross-user profile access, revoked/unauthorised profiles, raw model/embedding extraction, provider secret leakage, arbitrary provider/model invocation, unrestricted viewer TTS, client-supplied entitlement, client-supplied Moderator authority and any voice-triggered Battle/economy mutation. Agent role alone never grants Moderator authority; delegated LIVE moderation still requires Owner-enabled Moderator permission and explicit LIVE assignment.

## Definition of done

Full LIVE voice completion still requires a real microphone capture path, canonical control-room routing, authorised profile/runtime selection where supported, Preview before Programme, immediate processor bypass/failover, bounded TTS, executable captions, participant isolation, private cue separation, actual Programme recording/provenance, server-authoritative entitlement and production concurrency/latency tests. This tranche intentionally reports the remaining runtime gates instead of simulating them.
