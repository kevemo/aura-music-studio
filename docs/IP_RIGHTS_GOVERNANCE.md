# IP Rights Governance

This subsystem records evidence for qualified intellectual-property review. It is not a legal-certification engine and it does not automatically remove, restore, block, suspend or alter content, accounts, subscriptions, billing or ESP roles.

## Current source basis

Research checked 27 August 2026:

- U.S. Copyright Office, Section 512 report and statutory notice/counter-notice framework: https://www.copyright.gov/policy/section512/
- UK Intellectual Property Office copyright guidance: https://www.gov.uk/guidance/copyright-notices
- European Commission Digital Services Act platform transparency / notice-and-challenge materials: https://digital-strategy.ec.europa.eu/en/policies/digital-services-act-package

The repository does not claim that the stored fields alone make a notice or counter-notice legally sufficient. Fact-specific disputes and jurisdiction-specific requirements require qualified legal review.

## Security boundaries

- Member identity comes from the authenticated server session, not client-supplied claimant IDs.
- Work, target, signature, contact and legal-decision evidence are referenced through bounded opaque identifiers; this increment does not accept arbitrary URLs, filesystem paths or raw identity documents.
- A counter-notice cannot be claimed by any member who discovers a notice identifier. Owner/legal review must bind the verified affected member first, and the authenticated session must match that binding.
- U.S.-DMCA-profile counter-notices require explicit jurisdiction/service attestation evidence before entering review.
- Terminal notice and counter-notice decisions require separate opaque decision-evidence references.
- Owner review routes use the existing owner authorization boundary and are hidden from public OpenAPI.
- Hash-chained events are tamper-evident; they are not described as physically immutable storage.

## Execution separation

`automatic_content_action_taken` and `automatic_content_restoration` remain false throughout this module. A separate governed moderation/enforcement subsystem must perform any actual restriction or restoration and record its own evidence. This separation prevents a rights allegation or AI classification from becoming an executable moderation command.

## Remaining release work

Before representing the service as a formal public legal-notice channel, ESP still needs jurisdiction-specific legal review, designated-agent/contact processes where applicable, secure external-rightsholder intake, secure storage/retention rules for personal contact and signature evidence, service-of-notice procedures, deadline/escalation policy, repeat-infringer policy review where applicable, and documented operational staffing.
