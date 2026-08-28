# Creative Export Provenance & Review

This increment extends the pre-generation copyright/IP firewall into the professional editor export path.

## Production boundary

Every governed editor render records the authenticated member, project, sequence, output filename, media kind, format, SHA-256 digest, commercial-use request, rights attestation, exact-internal-duplicate signal and review state.

A request marked for commercial use must include an explicit member attestation that supplied material is owned or appropriately licensed. The platform does not infer those rights.

Commercial-use downloads remain blocked until the protected owner review workflow records `cleared_for_platform_export` with an opaque evidence reference. Review methods are bounded to manual IP review, an external similarity service or a licensed-catalog review.

## Similarity claims

The built-in deterministic check is an exact SHA-256 duplicate check against prior governed exports. It can detect byte-identical output already recorded by the platform. It is **not** perceptual, melodic, lyrical, acoustic or external-catalog similarity detection.

The response therefore labels its scope `internal_exact_sha256_only` unless an owner review explicitly records evidence from an external similarity service. The platform never turns either result into an automatic legal-clearance, copyrightability, uniqueness or non-infringement guarantee.

Exact duplicate detection is privacy bounded: a member can learn that their export matched an existing internal digest, but the record does not disclose another member's identity or project.

## Security and role boundaries

- Member provenance reads are restricted to the authenticated member ID from session context.
- Owner review uses the existing `owner_authorized` boundary and is hidden from public OpenAPI output.
- Evidence references are opaque identifiers; URLs, filesystem paths and arbitrary payloads are rejected.
- The workflow cannot grant/revoke ESP Creator, Agent, mentor, admin or owner roles.
- The workflow cannot change billing, subscriptions, membership or credits.
- Rendering remains non-destructive and never rewrites source media.
- This feature branch is denied Vercel deployment until validated and merged.

## Remaining release work

A future production integration may connect a licensed/accredited external similarity provider for audio, lyrics, images or video. Such a provider must be evaluated for lawful corpus access, privacy, retention, security, false-positive handling, regional restrictions and contractual rights before its output can be treated as review evidence.

No external corpus is silently scraped or queried by this increment.
