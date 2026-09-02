# PayPal verified billing events

Pulsar-Frequency House keeps membership approval and creative entitlements separate from payment-provider callbacks. A browser return from PayPal is never proof of payment, and a webhook cannot activate access by itself.

## Required environment

```env
LSS_PAYPAL_CLIENT_ID=<PayPal REST app client id>
LSS_PAYPAL_CLIENT_SECRET=<deployment secret>
LSS_PAYPAL_WEBHOOK_ID=<registered webhook id>
LSS_PAYPAL_ENVIRONMENT=live
```

Use `LSS_PAYPAL_ENVIRONMENT=sandbox` only with sandbox credentials and a sandbox webhook.

Register the HTTPS listener URL as:

```text
https://<public-host>/webhooks/paypal
```

At minimum, subscribe the PayPal REST app to `INVOICING.INVOICE.PAID` while the existing invoice-payment bridge is in use. The same verified event ledger can later receive subscription lifecycle events when native recurring PayPal subscriptions replace the manual invoice bridge.

## Security model

For every delivery, Pulsar requires the PayPal transmission headers, rejects non-PayPal certificate hosts, obtains an OAuth access token from the configured PayPal environment, and posts the event evidence to PayPal's `verify-webhook-signature` endpoint. Only `SUCCESS` events are written to the local evidence ledger. Event IDs are primary keys so exact retries are idempotent. A repeated event ID with a different transmission ID or payload is rejected as conflicting evidence instead of silently replacing or reusing the original record.

Webhook receipt never changes a user's plan. An ESP administrator may activate a verified invoice event through the owner-only API only when all of the following are true:

- the stored event was previously signature-verified;
- event type is `INVOICING.INVOICE.PAID`;
- the invoice resource itself reports `PAID` rather than a partial/pending state;
- invoice currency and amount exactly match the requested Pulsar tier;
- payer email matches the approved Pulsar account;
- the user's requested tier matches the activation tier;
- the payment event has not already been consumed by the subscription ledger.

Owner/admin event review:

```text
GET /admin/billing/paypal-events
X-LSS-Admin-Key: <owner deployment secret>
```

Evidence-backed activation:

```text
POST /admin/membership/activate-paypal-event
X-LSS-Admin-Key: <owner deployment secret>
Content-Type: application/json

{"user_id":"...","plan_id":"base","event_id":"WH-..."}
```

The existing manual activation endpoint remains available as a compatibility bridge while PayPal invoice links are still the configured checkout method. For production operations, verified provider-event activation is the stronger evidence path.

## PayPal references

Implementation follows PayPal's current REST webhook guidance and invoicing webhook event model. PayPal requires the registered webhook ID for verification and documents that invoice-paid notifications can include partial/pending situations, which is why Pulsar performs its own final-state, amount, currency and account checks before access changes.
