# Global Safety, Compliance & Multilingual Governance

This document records the production baseline for the Elevate Souls Productions Content Creation Command Center. It is an engineering governance layer, not a certification that the service complies with every law in every jurisdiction.

## Core requirements

- No hate, bullying, harassment, exploitation, credible threats, or facilitation of serious harm.
- Do not present Aura, ESP, or the platform as a licensed medical, legal, financial, mental-health, or emergency professional. High-risk professional topics must include an appropriate boundary notice and route users to suitably qualified or emergency help where appropriate.
- Preserve strict member / ESP Creator / ESP Agent / mentor / administrator / owner authorization boundaries. Compliance checks never grant roles or permissions.
- Apply privacy by design and data minimisation. Child-related processing requires child-specific safeguards and jurisdiction-aware review.
- Target WCAG 2.2 for accessibility.
- Label or disclose AI-generated or materially AI-edited content when required by applicable law, platform policy, or context.
- Provide locale-aware safety notices. Automated translations are accessibility aids; legally significant policy translations require professional jurisdiction-specific review.
- Keep every legal/platform rule source versioned by source, effective/check date, scope, and authority. Stale or uncertain rules must escalate to human review rather than being treated as definitive.

## TikTok LIVE production preflight

The `/compliance/tiktok-live/preflight` endpoint is a bounded policy aid. It currently checks declared LIVE conditions for:

- creator age (18+ for LIVE);
- hate and protected-class attacks;
- bullying and harassment;
- firearms/explosive weapons and physical altercations;
- gambling/gambling-like participation;
- manipulative Gift/engagement pressure;
- commercial disclosure;
- third-party tools such as translation, voice-to-text and overlays being actively moderated;
- content originality / permission uncertainty;
- sexualized or explicit content requiring current regional review;
- AI-content transparency;
- professional-topic disclaimers.

A `pass` result does not guarantee TikTok eligibility and does not certify legal compliance. Current TikTok rules and applicable local law remain authoritative.

## Current primary authoritative sources

- TikTok Community Guidelines: https://www.tiktok.com/community-guidelines
- UK ICO children's information / UK GDPR guidance: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/childrens-information/
- European Commission AI-generated content transparency code: https://digital-strategy.ec.europa.eu/en/policies/code-practice-ai-generated-content
- W3C WCAG 2.2: https://www.w3.org/TR/WCAG22/
- US FTC Children's Privacy / COPPA: https://www.ftc.gov/business-guidance/privacy-security/childrens-privacy
- California consumer privacy rights: https://privacy.ca.gov/california-privacy-rights/rights-under-the-california-consumer-privacy-act/

## Continuous compliance

Legal and platform requirements change. Production operation therefore requires ongoing policy-source monitoring, effective-date tracking, periodic legal/privacy review in operating jurisdictions, incident response, rights-request workflows, moderation/appeals evidence, and controlled updates to the policy registry. The application must fail closed or require human review where a material rule cannot be determined reliably.
