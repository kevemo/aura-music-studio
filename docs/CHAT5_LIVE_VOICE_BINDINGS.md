# Chat 5 — Server-authoritative LIVE Voice Profile binding references

## Scope

Shared Skies Streaming Studios may now bind one owned, audio-bearing Studio source to one currently authorised Chat 2 Voice House profile reference for a declared purpose.

This is a **reference binding only**. It is not a Voice Profile database, voice processor, cloned-voice execution path, real-time conversion claim or commercial entitlement decision.

## Canonical routes

- `GET /shared-sky/studio/api/sessions/{session_id}/voice/bindings`
- `PUT /shared-sky/studio/api/sessions/{session_id}/voice/bindings/{source_id}`
- `DELETE /shared-sky/studio/api/sessions/{session_id}/voice/bindings/{source_id}`

Routes are installed exactly once through the existing Chat 5 LIVE bootstrap.

## Stored data

The Chat 5 binding table stores only stable relationship metadata:

- authenticated tenant/user ID;
- Shared Skies Studio session ID;
- Shared Skies source ID;
- tenant-scoped Chat 2 project name;
- Chat 2 Voice Profile ID;
- requested authorised purpose;
- optimistic binding version and timestamps.

It does **not** store or copy:

- reference recordings;
- training artefacts;
- model files or embeddings;
- provider URLs or secrets;
- consent evidence;
- cloned/generated media;
- Chat 6 entitlement or Coin state;
- processor runtime configuration.

Generic source `config_json` is not used as authoritative Voice Profile binding storage.

## Binding validation

Before a binding can be written, Chat 5 verifies all of the following against current server state:

1. the authenticated tenant context matches the LIVE member;
2. the Studio session is owned by that member;
3. the source is owned by the member and belongs to the same Studio project;
4. the source type is audio-bearing;
5. the referenced Chat 2 project resolves inside that tenant;
6. the Voice Profile exists in the current Chat 2 `RightsLedger`;
7. the profile is tenant-compatible;
8. consent/verification is currently usable;
9. the requested purpose is currently allowed.

Binding does not call `authorize_voice_profile(...)`, because creating a reference is not processor execution and must not mark the Voice Profile as used.

## Revocation and revalidation

Binding rows are not cached authorisation grants. Listing bindings re-reads current Chat 2 profile authority. If a profile or project is removed, consent is withdrawn, the profile is revoked, or the requested use becomes unavailable, the binding is projected as not currently authorised and remains non-executable.

A revoked/unavailable binding can still be unbound safely.

## Concurrency

The first binding for a source is created without an `expected_version`. Replacing an existing binding requires the exact current binding version. Deleting a binding also requires its exact current version. Stale operators fail with a version conflict instead of silently overwriting another operator.

## Execution boundary remains closed

A binding response always reports:

- `processor_runtime_attached = false`;
- `processor_activation_allowed = false`;
- `real_time_processing_proven = false`;
- final Chat 2 re-authorisation required;
- Chat 6 remains entitlement authority;
- Chat 5 does not evaluate client entitlement as authority.

When an executable Chat 2 processor is later attached, every processor invocation must reload and re-authorise the selected profile at the final execution boundary through:

`authorize_voice_profile(rights_root, profile_id, purpose)`

The future execution path must also enforce applicable Chat 6 entitlement before processor use. A client-supplied profile ID, generic source configuration, a previous discovery response, or the existence of a binding row is never enough to activate processing.

## LIVE truth that remains unproven

This tranche does not prove or complete:

- cloned/synthetic LIVE voice execution;
- streaming voice conversion;
- measured voice latency;
- processor health;
- automatic raw-microphone bypass;
- LIVE TTS queue;
- real-time captions/transcription;
- real-time translation/dubbing;
- private spoken Auto Cue;
- provider/SFU/WebRTC readiness.

The existing authoritative Programme voice mute remains an independent safety control and does not depend on a voice processor.

## Security invariants

- no cross-tenant profile binding;
- no cross-project Studio source binding;
- no non-audio source binding;
- no raw Chat 2 voice artefacts exposed;
- no provider secret copied into Chat 5;
- no client-created entitlement authority;
- no processor activation from binding state;
- no Programme mutation when binding/unbinding;
- no Battle, Gift or Coin mutation.

## Next executable gate

After this reference layer is merged and exact-head validated, the next Chat 5 voice tranche is an executable processor-adapter boundary only when Chat 2 exposes a genuine runtime contract. That adapter must perform final Chat 2 re-authorisation, applicable Chat 6 entitlement checks, explicit Preview/Programme routing, measured latency/health, immediate bypass and failure recovery before real-time LIVE voice processing can receive completion credit.
