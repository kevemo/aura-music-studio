# Creative Copyright / IP Firewall

Policy version: `esp-creative-ip-v1-2026-08-28`

This layer protects the creation pipeline before a music, image, video, text or voice generation request reaches a renderer/model. It is intentionally stricter than relying only on post-publication takedowns.

## Production rules

- Prefer original creation over direct imitation of a named creator or real person.
- Block requests that explicitly ask to recreate, duplicate, copy, remake or reproduce an existing song, recording, melody, lyrics, music video, artwork or image without a verified rights basis.
- Block requests to copy lyrics from an existing work unless an authorized rights-cleared workflow supplies the material.
- Block voice/likeness cloning or impersonation of another person unless an authorized consent workflow verifies the right to use that voice/likeness.
- User-supplied lyrics must carry an explicit ownership/license confirmation before song generation.
- Reference audio must carry an explicit ownership/license confirmation before song generation. Uploaded performance/reference audio should also retain Asset Library provenance and SHA-256 identity evidence.
- Creative Project references already require `rights_confirmed=true` before they can be attached.
- Aura Voice House remains the authorized voice route: consent evidence, a one-time spoken challenge, allowed-use limits and revocation remain separate controls.
- Never treat a commercial-use grant as proof that an output qualifies for copyright protection or cannot infringe third-party rights.
- Never claim this deterministic preflight is a copyright database search, legal opinion, jurisdiction-wide clearance or uniqueness guarantee.

## Safe redirection

When a direct imitation request is blocked, Aura should help the member describe neutral attributes instead: genre, era, tempo, instrumentation, vocal range/timbre characteristics, production texture, song structure, mood, lighting, camera language, palette and similar high-level characteristics. The safe rewrite must not reinsert a real person's voice, likeness or protected work.

## Existing safeguards retained

The repository already contains stronger rights controls in several pathways. `performance_input_api.py` requires an explicit rights confirmation and writes Asset Library rights/provenance records. `voice_house_api.py` requires explicit voice consent, stores consent evidence, uses a challenge phrase, limits authorized uses and supports revocation. `creative_project.py` requires rights confirmation for creative references. The new firewall is additive and does not weaken those controls.

## External policy basis checked 2026-08-28

The design uses public platform safeguards as comparative inputs, not copied proprietary logic:

- Suno Safety: https://suno.com/safety — states that AI should enable originality rather than imitation and prohibits attempts to recreate existing songs, upload material without rights, or use another person's voice/likeness without permission.
- Suno Terms: https://suno.com/terms — prohibits submissions/generation that infringe IP or that a user does not have the right to upload/use.
- Suno Voice Model terms: https://about.suno.com/terms — states a Voice Model must resemble the user's own voice and prohibits creating another person's voice model.
- Donna AI Terms, updated May 25, 2026: https://www.musicdonna.com/terms — requires users not to submit material infringing copyright, trademark, privacy or other IP rights, including celebrity voices and copyrighted lyrics.

These sources can change. The repository must continue using versioned policy review rather than claiming permanent parity or worldwide legal certification.

## Release boundary

The deterministic rules catch high-confidence request intent. They cannot reliably determine whether a newly generated melody, image, recording or text is substantially similar to a protected work. A later production increment should add output-side similarity/provenance review for commercial export where technically and legally appropriate.
