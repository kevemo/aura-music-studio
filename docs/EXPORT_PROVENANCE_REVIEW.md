# Governed Export Provenance and Commercial Review

This subsystem records authenticated provenance for professional image/video exports and places commercial-use exports behind a fail-closed review gate.

## Guarantees

- Every governed export records the authenticated member ID, project, sequence, filename, media kind, format, SHA-256 digest, rights attestation state and requested commercial-use state.
- Member provenance reads are scoped to the authenticated member.
- Owner review uses the existing owner authorization boundary and is excluded from public OpenAPI.
- Commercial platform download remains blocked until explicit rights attestation exists and owner review records `cleared_for_platform_export`.
- Internal duplicate detection is exact SHA-256 matching only and never discloses another member's identity or project.
- External similarity is only marked complete when external similarity evidence is explicitly recorded.
- Review evidence references must be opaque identifiers; URLs and filesystem paths are rejected.
- The subsystem does not grant ESP roles and does not alter billing, subscriptions, membership or credits.

## Explicit non-guarantees

A cleared platform export is not a legal opinion or certification. The system always reports that automatic legal clearance, copyrightability and uniqueness are not guaranteed. Exact SHA-256 matching must not be described as melodic, lyrical, acoustic, perceptual or worldwide-catalog similarity analysis.

## Remaining production work

A future licensed/external similarity provider can be integrated behind the existing evidence interface. Provider results should trigger review rather than make definitive infringement findings automatically.
