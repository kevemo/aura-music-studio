# Chat 5 — Server-Authoritative LIVE Voice Source → Profile Binding

## Scope

Shared Skies Streaming Studios may now retain a server-authoritative relationship between one owned LIVE Studio audio-bearing source and one currently authorised Chat 2 Voice House profile reference.

This relationship is **not** a second Voice Profile database, a cloned-voice model store, a processor runtime, a commercial entitlement or proof of real-time voice conversion.

## Canonical authority

- Chat 2 remains authoritative for Voice Profile metadata, consent, revocation, permitted uses and final `authorize_voice_profile(rights_root, profile_id, purpose)` execution authorisation.
- Chat 5 owns only the LIVE relationship between an owned Studio session/source and the Chat 2 profile reference, plus LIVE routing/presentation.
- Chat 6 remains authoritative for subscriptions, payments, Cosmic Creation Coins, Gifts and any commercial entitlement required before a premium voice processor may execute.
- Chat 7 remains authoritative for production workers, self-hosted processor infrastructure, deployment, monitoring and security.

## Storage contract

The current Shared Skies database gains `shared_sky_live_voice_bindings`, keyed by `(session_id, source_id)`.

It stores only:

- authenticated tenant/user reference;
- Studio session ID;
- Studio source ID;
- Chat 2 project name required by the current Voice House rights-root contract;
- Chat 2 Voice Profile ID;
- approved purpose;
- optimistic binding version;
- created/updated timestamps.

It does **not** store reference recordings, embeddings, model files, provider secrets, training artefacts, copied Voice Profile records or client-manufactured entitlement.

Generic source `config` is deliberately not the authoritative binding location because source configuration is a broad presentation/configuration surface. A client-supplied `voice_profile_ref` in generic source metadata remains non-authoritative runtime metadata.

## Routes

### List bindings

`GET /shared-sky/studio/api/sessions/{session_id}/voice/bindings`

Returns only the current tenant/session binding projections. Each projection re-checks the referenced Chat 2 profile enough to report whether the stored reference is still currently authorised or has become invalidated/unavailable.

### Create or rebind

`PUT /shared-sky/studio/api/sessions/{session_id}/voice/bindings/{source_id}`

The server requires:

- authenticated member context;
- owned Shared Skies Studio session;
- source ownership through that session project;
- an audio-bearing canonical Shared Skies source;
- a current tenant-scoped Chat 2 Voice Profile;
- current Chat 2 consent/verification state;
- requested purpose included in the Voice Profile's authorised uses.

Existing bindings use optimistic version matching. A stale operator/client cannot silently overwrite a newer binding.

### Remove binding

`DELETE /shared-sky/studio/api/sessions/{session_id}/voice/bindings/{source_id}?expected_binding_version={version}`

Removal also requires the exact current binding version. Removing a reference does not claim that a processor was stopped, because this tranche does not attach an executable processor.

## Revocation and invalidation

A stored reference is never permanent execution authority.

If the Chat 2 profile is later revoked, loses consent, becomes unusable for the bound purpose or becomes unavailable, the binding projection becomes `invalidated`. The historical relationship may remain stored, but it cannot be treated as usable voice-processing authority.

## Final execution boundary

`authorize_live_voice_binding_for_execution(user_id, session_id, source_id)` exists as the Chat 5 rights boundary for a future executable processor.

Immediately before processing it:

1. revalidates the owned Studio session/source relationship;
2. reloads the current authoritative binding;
3. resolves the tenant-scoped Chat 2 rights root;
4. calls the current merged Chat 2 `authorize_voice_profile(rights_root, profile_id, purpose)` contract.

This means a discovery response, previously valid binding, client-supplied profile ID or stale cached Voice Profile object can never replace final Chat 2 re-authorisation.

The helper intentionally does **not** execute a processor and does **not** evaluate Chat 6 entitlement. A future executable processor path must satisfy both final Chat 2 authorisation and the applicable server-authoritative Chat 6 entitlement immediately before execution.

## Event contract

Successful binding changes emit bounded canonical Studio events:

- `studio_live_voice_profile_bound`
- `studio_live_voice_profile_unbound`

The events contain stable relationship references only. They do not contain raw recordings, model data, provider secrets or financial state.

## Explicitly not proven by this tranche

This binding reference does not prove or enable:

- cloned/synthetic LIVE voice processing;
- streaming voice conversion;
- streaming speech;
- measured LIVE voice latency;
- automatic raw-microphone processor bypass;
- real-time captions/transcription;
- real-time translation/dubbing;
- private spoken Auto Cue;
- premium/commercial voice entitlement;
- voice worker/provider readiness;
- Programme-safe processed voice output.

Real-time voice remains incomplete until an executable Chat 2 streaming processor is attached behind these authority gates and proves the full microphone → authorised processor → processed output → Preview → Programme → measured latency → bypass → failure recovery path.

## Regression expectations

Tests must prove:

- only currently authorised Chat 2 profiles can be bound;
- source/session/project ownership fails closed;
- non-audio sources cannot be bound;
- stored data is reference-only and redacted;
- stale rebind/unbind versions fail;
- later Chat 2 revocation invalidates the binding;
- final execution re-authorises Chat 2 and fails after revocation;
- final Chat 2 execution authorisation updates Chat 2 usage metadata;
- routes mount exactly once;
- no binding response claims processor runtime, real-time capability or Chat 6 entitlement.

## Next executable step

Attach a real Chat 2 processor adapter only when Chat 2 exposes an executable/health-proven processor contract. The adapter must call `authorize_live_voice_binding_for_execution(...)` immediately before each processing start/restart and separately enforce applicable Chat 6 entitlement. Chat 5 must then prove Preview/Programme routing, measured latency, immediate safe bypass, processor/provider failure recovery, no echo/duplicate audio and truthful degraded/unavailable state before real-time capability receives completion credit.
