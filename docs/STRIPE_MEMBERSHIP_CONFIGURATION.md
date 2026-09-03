# Stripe membership configuration contract

This document names the deployment-only Stripe configuration required by the Elevate Souls Productions Content Creation Command Center creative membership checkout.

It contains **no real credentials or provider Price IDs**. Real values belong only in the production/staging secret store and Stripe Dashboard.

## Canonical creative membership prices

| Membership | Monthly | Annual |
| --- | ---: | ---: |
| Basic (`base`) | £5.99 | £59.99 |
| Unlimited Pro (`pro`) | £9.99 | £99.00 |

`aura_music_studio/plans.py` is authoritative for these amounts. Provider configuration must match it exactly; provider-side discounts, stale Price objects or amount drift must not silently extend local entitlement.

## Required Stripe deployment variables

```text
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
STRIPE_BASE_PRICE_ID=
STRIPE_BASE_ANNUAL_PRICE_ID=
STRIPE_PRO_PRICE_ID=
STRIPE_PRO_ANNUAL_PRICE_ID=
LSS_PUBLIC_BASE_URL=
```

The four Price IDs must represent recurring GBP prices whose amounts and periods correspond to the canonical catalogue above. Do not reuse a monthly Price ID for an annual purchase.

## Trust boundary

- Browser return/success state is informational only and is never payment proof.
- Pending membership checkout is bound to the exact plan and billing period approved by ownership before a Stripe Checkout Session is created.
- Access activates or renews only from signed, server-verified Stripe webhook evidence.
- The local subscription ledger revalidates plan, period, currency and amount before mutating creative membership entitlement.
- ESP Creator/Agent/Admin/Owner authority is outside commercial membership billing and cannot be purchased through this path.
- Production settlement/refund/reconciliation is an external evidence gate. Repository configuration shape does not prove live money movement.

## Launch readiness

`python -m aura_music_studio.commercial_launch_readiness` validates configuration shape and canonical catalogue drift without exposing secret values or making network calls. A production release still requires the real provider-payment evidence defined by the production release checklist.
