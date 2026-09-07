# Jurisdiction-aware compliance applicability

This subsystem turns the existing static compliance manifest into bounded decision support for feature launches and user flows.

## What it does

`GET /compliance/applicability` evaluates reviewed policy evidence by country, optional state/region, feature, user role, age and effective date. It returns active and upcoming policy evidence, freshness warnings, age-scope findings and a fail-closed decision when local coverage is missing or stale.

The initial registry is deliberately small and based only on reviewed official sources already used by the Command Center: TikTok Community Guidelines, UK ICO guidance, the official EU AI Act text, FTC children's privacy guidance, California privacy rights and WCAG 2.2. It is not represented as exhaustive worldwide legal coverage.

## Freshness and effective dates

Every policy record includes `effective_from`, optional `effective_to`, `reviewed_at` and `next_review_at`. A policy can be active, upcoming or retired. Queries made after `next_review_at` are flagged as stale and return `requires_qualified_legal_review` until refreshed evidence is recorded.

Upcoming rules are returned separately so product teams can prepare before an effective date without treating them as currently active.

## Owner review evidence

`POST /owner/compliance/policy-registry/reviews` is hidden from public OpenAPI and requires the existing owner authorization boundary. Reviews are append-only. A newer review for the same policy and jurisdiction supersedes the prior record for applicability decisions without deleting history.

Evidence references must be opaque identifiers. Raw identity documents, file paths, URLs used as evidence payloads, passwords or platform credentials do not belong in this registry.

## Security boundaries

The engine does not grant or revoke ESP Creator, Agent, mentor, admin or owner roles. It does not alter membership, billing, subscriptions or credits. It does not perform TikTok account actions. It does not certify legal compliance, provide legal advice, guarantee platform eligibility or claim complete jurisdictional coverage.

Unknown, incomplete, stale, age-sensitive or jurisdictionally unsupported situations fail closed to qualified legal review.

## Extending coverage

Add jurisdictional evidence only after reviewing an official government, regulator, platform or recognized standards-body source. Record the source URL, effective date, review date, next review date, feature/role scope, confidence and an opaque internal evidence reference. Keep country/state isolation precise; a California-specific rule must never be silently applied as a nationwide US rule.
