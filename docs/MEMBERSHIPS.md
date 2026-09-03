# Command Center Membership System

**Elevate Souls Productions Content Creation Command Center — Powered by Aura AI**  
**Elevate Your Soul Through Purposeful Media**

All accounts require approval by Elevate Souls Productions. Commercial subscription status and ESP organisational roles are separate security dimensions: Creator/Agent/Owner permissions never silently grant, extend or replace a paid creative subscription.

## Authoritative creative catalogue

The server-side catalogue in `aura_music_studio/plans.py` is the authoritative source for creative membership prices and entitlements. Checkout, verified provider activation, verified renewals, account/public presentation and tests must derive from that catalogue instead of repeating independent price constants.

### Free — £0

Free provides core Command Center exploration and selected basic creative capabilities. It does not include confirmed finished full-track allowance or paid-tier download/production entitlements.

### Tier 2 — £5.99/month

Tier 2 provides increased creative access, including the server-authoritative shared daily admission policy and the enabled Tier 2 Music, Video and Game capabilities. The current approved catalogue defines Tier 2 as monthly only; no annual Tier 2 price is invented by the application.

### Unlimited Pro — £9.99/month or £99/year

Unlimited Pro provides the highest enabled creative access, subject to fair-use, infrastructure, provider-capacity, rate-control, anti-abuse, safety and legal safeguards.

The approved billing periods are explicit:

- monthly: **£9.99** (`999` GBP minor units)
- annual: **£99.00** (`9900` GBP minor units)

The annual option is not implemented as twelve assumed monthly payments. It is a distinct catalogue period that must be bound to a provider price and verified as exactly £99.00 before annual access is activated or renewed.

## Approval and payment lifecycle

1. A user selects Free, Tier 2 or Unlimited Pro and signs up.
2. The account becomes `pending_approval`.
3. Kev or Mary approves or rejects the request through the owner-controlled review flow.
4. Free becomes active after approval without a subscription payment.
5. Approved paid accounts become `approved_pending_payment`.
6. Paid checkout is offered only through a configured provider path for the selected plan and billing period.
7. A browser success/return page is informational only and never proves payment.
8. Paid entitlement activates only from verified provider evidence or an explicit owner/admin verification path.
9. Renewals extend access only when the verified paid amount, currency, plan, customer/subscription binding and billing period match the canonical catalogue.
10. Expired paid membership falls back to Free without mutating ESP Creator/Agent/Owner role state.

## Stripe period contract

The hardened Stripe membership path defaults legacy clients to `month` but supports explicit `month` and `year` periods where the plan permits them.

- Existing monthly Tier 2 and Pro Price IDs remain deployment configuration.
- Unlimited Pro annual checkout requires `STRIPE_PRO_ANNUAL_PRICE_ID`.
- If the annual Price ID is absent, annual Stripe checkout fails closed rather than substituting a monthly price or inventing provider success.
- Checkout and subscription metadata carry both `plan_id` and `billing_period`.
- Signed webhook processing verifies the exact canonical amount and currency before local entitlement activation or renewal.
- Provider-side upgrade/downgrade metadata cannot silently change local entitlement without verified paid invoice evidence.
- Cancellation marks the provider binding cancelled but preserves already-paid access until the verified paid period expires.
- Refund evidence never grants or upgrades access; supported membership refund evidence enters a reconciliation/review state.

## Manual PayPal compatibility

Legacy/manual PayPal invoice links are not authoritative price sources. The application displays the amount from the canonical catalogue and never treats opening or returning from a PayPal link as payment proof. A production operator must independently verify that any configured invoice/link is denominated in the intended currency and charges the exact canonical amount before relying on it.

Annual Pro PayPal is configuration-only (`LSS_PAYPAL_PRO_ANNUAL_URL`); the application does not invent an annual invoice link. Live provider correctness remains a production evidence gate rather than something repository tests can manufacture.

## Billing-period persistence

Subscription state, payment records and Stripe customer/subscription bindings persist an explicit `billing_period`. Existing pre-period rows are migrated conservatively as `month`, preserving backward compatibility with the previous monthly-only schema.

A verified monthly period currently records a 31-day local entitlement window; a verified annual period records a 365-day local entitlement window. Provider reconciliation and renewal evidence remain authoritative for whether a new period is granted.

## Output ownership

Elevate Souls Productions Content Creation Command Center and Elevate Souls Productions do not claim ownership of a member's original inputs or eligible generated outputs. Rights in AI-assisted outputs remain subject to applicable law, licences of underlying open models and any third-party/source-material rights. Members must have the rights required for material they upload or ask the Command Center to transform.

## Release truth

Repository tests can verify catalogue consistency, route ownership, amount/period validation and entitlement behavior. They cannot prove a live Stripe/PayPal account is correctly configured, a real charge settled, a refund completed, or a bank received funds. Production payment credentials, live Price IDs/invoice configuration, signed webhook delivery, real-money payment/refund testing and financial reconciliation remain external release evidence and must fail closed until independently verified.
