# Chat 5 — Authorised LIVE Voice Profile Discovery

## Purpose

Shared Skies Streaming Studios can now discover tenant-scoped Chat 2 Voice House profiles that are currently consent-valid and authorised for a requested use. This is a read-only candidate discovery surface. It does **not** create a second Voice Profile database, does not grant entitlement, and does not prove executable or real-time LIVE voice processing.

## Route

`GET /shared-sky/studio/api/sessions/{session_id}/voice/profiles`

Required query values:

- `chat2_project_name`: the authenticated member's Chat 2 project containing the authoritative `.aura_rights` ledger.
- `purpose`: one of `speech`, `voice_conversion`, `singing`, `backing_harmony`, or `dubbing`; defaults to `speech`.

The caller must be an authenticated ESP hub member and must own the referenced Shared Skies Studio session. Tenant context must match the authenticated member.

## Authority boundaries

Chat 2 remains the only Voice Profile / consent authority. Chat 5 reloads the authoritative `RightsLedger` and exposes only profiles whose current tenant, consent/verification state, revocation state, and requested allowed use pass Chat 2 checks.

Returned profile data is deliberately narrow. Raw reference files, model/provider secrets, provider runtime state, and executable processor material are never returned.

Discovery is **candidate-only**. A later server-authoritative LIVE binding must still be implemented before a profile can be attached to a LIVE processor. Every eventual processor invocation must re-read and authorise the selected profile at the final execution boundary using Chat 2 `authorize_voice_profile(rights_root, profile_id, purpose)` rather than trusting an earlier discovery result.

Chat 6 remains authoritative for commercial entitlement. Chat 5 does not infer or accept entitlement from the client.

## Explicitly unproven by this tranche

- server-authoritative LIVE source-to-profile binding;
- executable cloned/synthetic voice processor attachment;
- real-time voice conversion;
- streaming speech;
- measured LIVE voice latency;
- automatic raw-microphone bypass on processor failure;
- real-time captions or translation;
- commercial entitlement;
- private spoken Auto Cue routing.

Joining or recording a LIVE never implies cloning consent.

## Safety / privacy behaviour

- Cross-tenant profile discovery fails closed.
- Revoked, unconsented, invalid-verification, wrong-purpose, and foreign-tenant profiles are omitted.
- Discovery does not create Chat 5 profile authority.
- Client-provided profile IDs or entitlement claims are not execution authority.
- Existing Programme/private isolation and emergency Programme voice mute remain independent of profile discovery.

## Next executable step

Add a server-authoritative Shared Skies LIVE source → Chat 2 Voice Profile binding reference. The binding must retain only a stable profile reference and authoritative Chat 2 project/right-root locator, remain tenant-scoped, and re-run Chat 2 authorisation on every processor execution. Processor activation must still fail closed until Chat 2 supplies an executable runtime contract and Chat 6 entitlement is proven where required.
