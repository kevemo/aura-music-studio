# Chat 5 Provider Settlement Reconciliation Contract

## Purpose

This contract defines the production boundary between the server-authoritative Cosmic Creation Coin purchase ledger and a future official payment-provider adapter capable of retrieving authenticated settlement state.

It is a reconciliation layer only. It does not create a checkout, confirm a payment, credit or debit Coins, change a purchase status, calculate creator payout, recognise revenue, calculate tax, or repair financial history automatically.

The authoritative Coin pricing baseline remains unchanged: 1,000 Cosmic Creation Coins = £5 (500 GBP minor units), with integer Coin quantities and integer fiat minor units.

## Runtime module

`aura_music_studio.cosmic_economy_settlement`

Primary types:

- `VerifiedSettlementState`
- `CoinSettlementProvider`
- `CoinSettlementReconciler`

## Provider adapter capability

A real Coin payment provider may implement:

```python
class CoinSettlementProvider(Protocol):
    name: str

    def fetch_settlement_state(
        self,
        *,
        provider_payment_id: str,
        purchase_id: str,
    ) -> VerifiedSettlementState | None:
        ...
```

The provider adapter must retrieve state from an authenticated provider API or equivalently authoritative provider channel. Client redirects, browser query parameters, client JSON, screenshots, success pages, local storage, or an unverified callback are not settlement evidence.

If the configured payment provider does not implement authenticated settlement retrieval, reconciliation fails closed with `SETTLEMENT_RECONCILIATION_UNAVAILABLE`.

## Canonical provider state

`VerifiedSettlementState` contains:

- `provider`
- `provider_payment_id`
- `purchase_id` when the provider can bind it
- `status`
- `fiat_amount_minor`
- `fiat_currency`
- `verified`
- optional `observed_at`
- optional provider metadata retained only inside the adapter/service boundary

Accepted canonical statuses are:

- `pending`
- `confirmed`
- `failed`
- `cancelled`
- `refunded`
- `chargeback`

A provider-specific adapter is responsible for translating native provider states into these canonical values. Unknown provider states must not be guessed into a successful state.

## Reconciliation invariants

For each internal `coin_purchases` record, Chat 5 compares authoritative provider state against:

1. provider identity;
2. bound provider payment/reference ID;
3. internal purchase ID when the provider returns one;
4. exact fiat amount in integer minor units;
5. exact three-letter currency;
6. canonical payment status.

A matching settlement result is read-only. A mismatch is also read-only: it creates or returns a persistent reconciliation discrepancy for Owner/finance review and leaves the Coin ledger, wallet materialised state, and purchase status untouched.

## Persisted discrepancy types

The settlement reconciler may create:

- `PROVIDER_SETTLEMENT_REFERENCE_MISSING`
- `PROVIDER_SETTLEMENT_MISSING`
- `PROVIDER_SETTLEMENT_REFERENCE_MISMATCH`
- `PROVIDER_SETTLEMENT_PURCHASE_MISMATCH`
- `PROVIDER_SETTLEMENT_AMOUNT_MISMATCH`
- `PROVIDER_SETTLEMENT_CURRENCY_MISMATCH`
- `PROVIDER_SETTLEMENT_STATUS_MISMATCH`

These use the existing `economy_reconciliation_discrepancies` table. They are review evidence, not an automatic repair instruction.

Existing Owner discrepancy-resolution operations may mark a discrepancy reviewed/resolved, but resolution does not rewrite financial history. If the underlying mismatch remains, a later reconciliation can surface it again.

## Fail-closed validation errors

The service rejects invalid or unauthenticated provider evidence with explicit errors including:

- `UNVERIFIED_SETTLEMENT_STATE`
- `SETTLEMENT_PROVIDER_MISMATCH`
- `INVALID_SETTLEMENT_STATUS`
- `INVALID_SETTLEMENT_AMOUNT`
- `INVALID_SETTLEMENT_CURRENCY`
- `SETTLEMENT_RECONCILIATION_UNAVAILABLE`
- `SETTLEMENT_PROVIDER_UNAVAILABLE`

No error path is allowed to credit Coins because a provider lookup could not be completed.

## Owner/finance API

The existing Owner economy router exposes:

```text
POST /owner/economy/settlement-reconciliation/{provider_name}
POST /owner/economy/settlement-reconciliation/{provider_name}/purchases/{purchase_id}
```

Both routes require existing Owner authorization and resolve the provider through the existing `coin_payment_providers` registry.

Successful reconciliation attempts are appended to the existing audit ledger with aggregate counts or the individual purchase/result summary. Provider credentials and payment-instrument data must not be written to audit details.

## Batch behavior

`CoinSettlementReconciler.reconcile_provider(...)` scans internal purchases for one canonical provider, bounded to at most 1,000 records per invocation. It returns checked, matched, mismatched, and per-purchase results.

This bounded API is intended as a finance/operations primitive. Chat 10 may schedule or orchestrate it using the platform's approved operational infrastructure without duplicating the reconciliation logic.

## Security and privacy requirements

A production provider adapter must:

- keep credentials in approved secret storage/environment configuration;
- authenticate provider API responses;
- avoid storing raw card/bank/payment-instrument data in Chat 5 tables;
- avoid exposing provider secrets in HTTP responses, logs, discrepancies, outbox payloads, or audit details;
- use server-side provider references already bound to the Coin purchase;
- fail closed when provider state cannot be authenticated.

Chat 5 must not infer payment success from a checkout redirect or client-visible provider success page.

## Accounting boundary

Provider settlement reconciliation does not by itself establish:

- recognised ESP revenue;
- processor fees;
- tax liability;
- profit;
- creator LIVE Gift payable fiat;
- a creator payout percentage.

Those values remain `None`/unconfigured where the authoritative policy or accounting evidence is absent. The marketplace 50/50 split is not applied to LIVE Gifts.

## Tests

Focused deterministic coverage is in:

- `tests/test_cosmic_economy_settlement.py`
- `tests/test_cosmic_economy_settlement_api.py`

Coverage includes matching authenticated state, amount/status mismatches, missing provider records, unverified provider state, providers without settlement capability, batch reconciliation, non-mutating financial behavior, and Owner route registration.

No real provider charge or refund is performed by these tests.

## Remaining production handoffs

The reconciliation code is ready for integration, but external production settlement remains blocked until all of the following exist:

1. an approved production Coin payment provider;
2. provider credentials stored through the approved secret-management boundary;
3. an official provider adapter registered in `coin_payment_providers`;
4. that adapter implements authenticated `fetch_settlement_state(...)` retrieval;
5. Chat 10 validates operational scheduling, observability, rate/retry behavior, incident handling, and secret controls;
6. Chat 11 validates the final merged repository and release evidence.

Until those conditions are met, settlement reconciliation must report unavailable rather than simulate provider success.
