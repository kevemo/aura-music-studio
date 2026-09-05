# Chat 5 Cosmic Coins & LIVE Gifts Integration Contract

## Purpose

This document is the integration contract for the server-authoritative Cosmic Creation Coin economy and first-party Shared Sky LIVE Gift transaction system implemented by Chat 5.

The authoritative commercial baseline is **1,000 Cosmic Creation Coins = £5.00**, represented as 1,000 integer Coin units and 500 integer GBP minor units. Promotional or larger packs do not inherit an automatic discount; each version carries its own server-authoritative Coin quantity, fiat minor-unit price and currency.

The existing creator marketplace 50/50 revenue split is **not** a LIVE Gift payout rule. No LIVE Gift creator percentage, Coin-to-cash ratio or cash-out formula is configured by this workstream.

## Canonical modules

- `aura_music_studio.cosmic_economy`
  - durable schema and transaction service;
  - append-only Coin ledger;
  - Coin packs and purchases;
  - payment-event application;
  - Gift catalogue and send/reversal transactions;
  - creator Gift receipts;
  - spending controls;
  - outbox events;
  - reconciliation.
- `aura_music_studio.cosmic_economy_integrations`
  - `IntegratedCosmicEconomy` is the canonical runtime class;
  - `configure_economy_integrations(...)` is the compatibility seam for canonical live-session, eligibility and risk adapters;
  - `economy_service(...)` builds the runtime service used by HTTP routes.
- `aura_music_studio.cosmic_payments`
  - `CoinPaymentProvider` protocol;
  - `CoinCheckout` result;
  - `coin_payment_providers` explicit provider registry.
- `aura_music_studio.cosmic_economy_api`
  - authenticated member routes and Owner routes.
- `aura_music_studio.cosmic_economy_owner_ops`
  - `EconomyOwnerOperations` for risk review, creator-receipt holds/releases, discrepancy queues and truthful finance/liability snapshots.

The production FastAPI application registers `cosmic_economy_router` in `aura_music_studio.api`.

## Persistence and transaction model

The implementation reuses the repository's existing `LSS_DB_PATH` SQLite database and WAL conventions. Consequential money-moving operations run inside `BEGIN IMMEDIATE` transactions. The append-only Coin ledger is the financial source of truth. `coin_accounts.available_balance` and `coin_accounts.recovery_debt` are transactional materialisations used for fast admission checks and must reconcile to the ledger.

Ordinary spending cannot create a negative available balance. A verified refund or chargeback can create explicit `recovery_debt` when the original purchased Coins have already been spent. Later credits pay recovery debt before becoming newly available Coins.

### Tables

- `coin_accounts`
- `coin_ledger_entries`
- `coin_packs`
- `coin_purchases`
- `payment_webhook_events`
- `gift_definitions`
- `gift_transactions`
- `creator_gift_receipts`
- `gift_payout_policies`
- `account_spending_limits`
- `economy_feature_flags`
- `economy_outbox`
- `economy_risk_cases`
- `economy_reconciliation_discrepancies`

`coin_ledger_entries` has database triggers that reject UPDATE and DELETE. Financial corrections use linked compensating entries.

The runtime hardening layer requires purchase idempotency keys to be globally unique within the purchase command family and Gift-send idempotency keys to be globally unique within the Gift command family. A key already bound to another account is rejected with `IDEMPOTENCY_KEY_SCOPE_MISMATCH`. If legacy data already contains one key bound to multiple accounts, runtime initialization fails closed with `IDEMPOTENCY_MIGRATION_CONFLICT`; it does not rewrite financial history.

## Ledger entry types

Current controlled entry types include:

- `PURCHASE_CREDIT`
- `PROMOTIONAL_CREDIT`
- `OWNER_APPROVED_ADJUSTMENT_CREDIT`
- `OWNER_APPROVED_ADJUSTMENT_DEBIT`
- `GIFT_DEBIT`
- `PURCHASE_REVERSAL_DEBIT`
- `REFUND_DEBIT`
- `CHARGEBACK_DEBIT`
- `GIFT_REVERSAL_CREDIT`

No Coin expiry entry is created because no approved Coin-expiry policy is configured.

## Baseline Coin pack

Seeded authoritative pack:

- `pack_id`: `cosmic-1000-gbp`
- `version`: `1`
- `coin_quantity`: `1000`
- `fiat_amount_minor`: `500`
- `fiat_currency`: `GBP`
- `is_baseline`: true

All Coin pack purchases copy the server-side pack version, Coin quantity, fiat minor-unit amount and currency into the purchase record. The client does not submit a trusted price.

## Payment-provider boundary

`CoinPaymentProvider` must provide:

```python
create_checkout(
    *,
    purchase_id: str,
    user_id: str,
    fiat_amount_minor: int,
    fiat_currency: str,
    coin_quantity: int,
) -> CoinCheckout

verify_webhook(*, headers: Mapping[str, str], body: bytes) -> VerifiedPaymentEvent
```

The provider registry is intentionally empty by default. Therefore real Coin checkout returns `PAYMENT_PROVIDER_UNAVAILABLE` until a verified provider adapter is registered by deployment code.

The existing manual subscription PayPal invoice/payment-link flow is not treated as proof of a Coin purchase and is not reused as the Coin webhook system.

A purchase is created as `pending`; the checkout/browser return path never credits Coins. Only `apply_verified_payment_event(...)` with `verified=True` can create a `PURCHASE_CREDIT`.

Public provider webhook route:

- `POST /auth/economy/payment-webhooks/{provider_name}`

The `/auth/` prefix is already outside member-session enforcement. The route itself remains fail-closed because it resolves an explicitly registered provider and delegates authenticity/signature verification to that provider adapter before applying any event.

## Member HTTP routes

- `GET /economy/coin-packs`
- `GET /economy/gifts`
- `GET /economy/me/balance`
- `GET /economy/me/history?limit=&offset=`
- `GET /economy/me/spending`
- `POST /economy/me/coin-purchases`
  - requires `Idempotency-Key` header;
  - returns a pending internal purchase plus provider checkout;
  - returns `coins_credited: false` until verified provider event.
- `POST /economy/me/gifts/send`
  - requires `Idempotency-Key` header;
  - sender identity comes only from `request.state.member.user_id`;
  - client cannot select another wallet/user ID.

Member balance/history routes are account-bound server-side, which prevents caller-controlled cross-account financial reads.

## Owner HTTP routes

All current `/owner/economy/...` routes require canonical `owner_authorized(request)`.

- `GET /owner/economy/reconciliation`
- `GET /owner/economy/creator-statements/{creator_recipient_id}`
- `GET /owner/economy/outbox`
- `POST /owner/economy/accounts/{user_id}/freeze`
- `POST /owner/economy/adjustments`
- `POST /owner/economy/spending-limits`
- `POST /owner/economy/feature-flags/{flag_name}`
- `POST /owner/economy/gift-catalogue`
- `POST /owner/economy/coin-packs`
- `POST /owner/economy/gifts/{gift_transaction_id}/reverse`

Consequential current Owner routes also write the repository's existing hash-chained `AuditLedger`.

Additional backend operations consumed by future Chat 9 Owner/Admin surfaces are available through:

```python
from aura_music_studio.cosmic_economy_owner_ops import EconomyOwnerOperations

ops = EconomyOwnerOperations(economy_service())
ops.list_risk_cases(...)
ops.review_risk_case(...)
ops.set_creator_receipt_hold(...)
ops.list_reconciliation_discrepancies(...)
ops.finance_snapshot()
```

`finance_snapshot()` deliberately returns `None` for recognised ESP revenue, processor fees, tax and profit unless real accounting/provider evidence exists. It also returns no creator payable fiat amount when no payout policy is active.

## Gift catalogue

Seeded original first-party Gift fixture:

- `gift_id`: `starlight-spark`
- `version`: `1`
- `display_name`: `Starlight Spark`
- `coin_cost`: `10`

This is an original Shared Sky Gift definition and is not copied from a competitor. Owners can publish new immutable Gift versions. Historical Gift transactions snapshot `gift_id`, `gift_version`, `unit_coin_cost`, quantity and total Coin cost; old transactions are never repriced from the current catalogue.

Presentation fields support asset reference, reduced-motion/static fallback reference and sound reference. Chat 4/3 owns the actual viewer/studio presentation.

## Gift-send command

Canonical runtime call:

```python
result = economy_service().send_gift(
    sender_user_id=canonical_user_id,
    recipient_creator_id=canonical_creator_id,
    live_session_id=canonical_live_session_id,
    gift_id=gift_id,
    gift_version=gift_version,
    quantity=quantity,
    idempotency_key=idempotency_key,
    battle_id=optional_battle_id,
    battle_round_id=optional_round_id,
)
```

Before commit it checks:

- canonical age/region/feature eligibility adapter;
- authoritative Shared Sky live-session/recipient adapter;
- account active state;
- Gift version availability;
- emergency feature flags;
- server Gift cost;
- hard spending limits;
- risk decision;
- sufficient available balance;
- idempotency scope and request fingerprint.

One successful database transaction creates/links:

1. `GIFT_DEBIT` ledger entry;
2. `gift_transactions` committed row;
3. `creator_gift_receipts` pending row;
4. `economy_outbox` committed Gift event.

A retry with the same account/key/fingerprint returns the original transaction. A changed request rejects the key. Another account attempting to reuse the key is rejected.

## Eligibility/live compatibility interfaces

Until canonical Chat 1/2 contracts are merged, Chat 5 uses narrow Protocols and fails closed by default.

```python
from aura_music_studio.cosmic_economy_integrations import configure_economy_integrations

configure_economy_integrations(
    live_sessions=my_live_session_directory,
    eligibility=my_age_region_policy,
    risk=my_risk_adapter,
)
```

### `LiveSessionDirectory`

```python
gift_context(
    *, live_session_id: str, recipient_creator_id: str
) -> LiveGiftContext
```

`LiveGiftContext` carries authoritative session ID, recipient creator ID, `active`, `gift_eligible`, `recipient_eligible`, and optional Battle/round references.

Default: `UnavailableLiveSessionDirectory`, which raises `LIVE_VALIDATION_UNAVAILABLE` and changes no financial state.

### `EconomyEligibilityDirectory`

```python
check(
    *,
    feature: str,
    user_id: str | None = None,
    creator_recipient_id: str | None = None,
    live_session_id: str | None = None,
) -> EligibilityDecision
```

Expected feature keys currently include `coin_purchase`, `gift_send` and `gift_receive`.

Default: `UnavailableEconomyEligibilityDirectory`, which fails closed with `ELIGIBILITY_POLICY_UNAVAILABLE`. The current implementation does not invent one global age threshold.

### `GiftRiskEvaluator`

```python
evaluate(
    *,
    sender_user_id: str,
    recipient_creator_id: str,
    live_session_id: str,
    total_coin_cost: int,
) -> RiskDecision
```

Supported decisions are `allow`, `monitor`, `hold`, `block`. Baseline behavior blocks only exact canonical-identity self-gifting; Chat 10/canonical security systems can add device/payment/account-takeover and collusion signals behind the same interface without exposing thresholds to clients.

## Spending controls

`account_spending_limits` supports integer-Coin daily/weekly/monthly hard limits and warning thresholds. Gift sends are denied before commit when the new total would exceed a hard limit. Warnings are represented separately from hard limits.

No dark-pattern purchase escalation is implemented.

## Creator Gift receipts and payout boundary

`creator_gift_receipts` is separate from sender wallets and from marketplace accounting. Receipt states support pending/held/cleared/adjusted/reversed/paid data representation, but Chat 5 does not mark a Gift receipt paid merely because the Gift committed.

No default row is inserted into `gift_payout_policies`. Therefore:

- `active_payout_policy()` returns `None`;
- creator statements return `payout_formula_configured: false`;
- `payable_fiat_total_minor` is `None`;
- receipt `payable_amount_minor` remains `NULL`;
- no 50/50, 70/30, 80/20 or other creator share is assumed.

A future payout implementation must attach a real versioned approved policy before setting payable/paid monetary fields.

## Reversals, refunds and chargebacks

Committed ledger history is never deleted.

- provider refund -> `REFUND_DEBIT`;
- provider chargeback -> `CHARGEBACK_DEBIT`;
- won/reversed dispute -> new `PURCHASE_CREDIT` compensation;
- Gift reversal -> `GIFT_REVERSAL_CREDIT` plus Gift/receipt reversal state.

If a purchase reversal exceeds currently available Coins, the account records explicit `recovery_debt`. Future credits service recovery debt before becoming available.

Current model does not claim exact FIFO/LIFO source-bucket attribution between every purchased Coin and every downstream Gift. A future payment-source attribution policy can be added without rewriting Gift history.

## Realtime/outbox contract for Chat 4 and Chat 3

After, and only after, a Gift database transaction commits, `economy_outbox` contains event type:

- `shared_sky.gift.committed`

Safe payload fields include:

- `event_id`;
- `gift_transaction_id`;
- `live_session_id`;
- sender canonical user/display reference subject to the eventual privacy adapter;
- `recipient_creator_id`;
- Gift ID/version/display name/unit Coin cost;
- safe asset and reduced-motion asset references;
- quantity and total Coin cost;
- Battle/round references when present;
- occurrence time;
- correlation ID.

It does not contain payment instrument data, provider webhook secrets, internal fraud scores or a fabricated creator payout value.

Chat 4 renders the committed viewer Gift activity. Chat 3 may use the same committed event for studio overlays. Neither surface should infer success from animation timing or optimistic client balance mutation.

The current module exposes `pending_outbox()` and `mark_outbox_published()`. A canonical Chat 1/10 outbox dispatcher must connect this durable queue to the repository's final realtime/event transport. Failure to display an event after financial commit must not silently undo the committed ledger transaction.

## Battle contract for Chat 6

Chat 5 emits financially committed/reversed Gift references. It does not mutate Battle score.

Committed event:

- `shared_sky.gift.committed`

Reversal event:

- `shared_sky.gift.reversed`

Both contain stable `gift_transaction_id`, live-session reference, optional Battle/round reference, occurrence time and correlation ID. Chat 6 must deduplicate by the stable Gift transaction/event reference and applies its own Battle eligibility/scoring/correction rules.

No wagering, pooled betting, staking or random-prize gambling behavior exists in Chat 5.

## Statements/history contract for Chat 9

User Coin history:

```python
economy_service().transaction_history(user_id, limit=100, offset=0)
```

Creator Gift-receipt statement:

```python
economy_service().creator_statement(creator_recipient_id, start_at=None, end_at=None)
```

Statements show Gift/Coins by state and do not fabricate a GBP/USD creator earnings amount when payout policy is absent.

## Reconciliation contract for Chat 10/11

```python
economy_service().reconcile()
```

Current deterministic checks cover:

- ledger net vs materialised wallet available/recovery-debt state;
- Gift transaction vs referenced `GIFT_DEBIT` amount;
- Gift transaction vs referenced creator receipt Coin value.

Mismatches are persisted to `economy_reconciliation_discrepancies`; unknown mismatches are not silently auto-repaired.

Provider-vs-internal purchase reconciliation requires a real configured payment provider/adaptor capable of provider-side settlement retrieval. Until that exists, Chat 5 can reconcile verified provider events already received but cannot truthfully claim external settlement reconciliation.

## Emergency controls

Seeded feature flags:

- `coin_purchases_enabled = true`
- `gift_sends_enabled = true`
- `creator_receiving_enabled = true`
- `creator_payout_enabled = false`

Money movement still fails closed where provider, live-session or eligibility dependencies are unconfigured. Owner routes can disable the purchase/Gift flags without erasing history.

## Deterministic test fixtures

`tests/test_cosmic_economy.py` uses:

- a deterministic allow-eligibility adapter;
- a typed fake live-session directory;
- fake verified provider events only;
- temporary SQLite databases;
- no real payment provider and no real charge.

Coverage includes the canonical baseline, client-success non-crediting, forged/unverified event rejection, duplicate webhooks, cross-account idempotency rejection, concurrent overspend prevention, live/eligibility fail-closed behavior, exact self-gift block, hard spending limits, Gift version preservation, chargeback recovery debt, append-only reversals, no default creator payout formula, account freeze/kill switch and reconciliation invariants.

## Legacy/migration policy

The current audited `main` branch did not contain a canonical production Coin ledger to migrate. Chat 5 therefore creates the new tables idempotently in the existing database and does not invent opening ledger entries for unknown balances.

If a future deployment exposes legacy/demo balances without trustworthy transaction provenance:

1. do not copy the number into an authoritative wallet silently;
2. identify whether it is demo/test or real customer state;
3. create an explicit reviewed opening-balance/import record with provenance and correlation evidence if legally/accountingly justified;
4. reconcile imported totals before enabling purchase/Gift sends;
5. never destroy prior evidence to make totals appear correct.

## Current production blockers / feature gates

The following are intentional blockers, not simulated success paths:

1. **Payment provider credentials/adapter not configured.** `coin_payment_providers` is empty by default; real checkout/webhook verification is unavailable until an official provider integration is registered.
2. **Canonical age/region eligibility policy not connected.** Purchases and Gift sends/receives fail closed rather than assuming a global rule.
3. **Authoritative Shared Sky live-session adapter not connected on the audited `main` baseline.** Gift sends fail with `LIVE_VALIDATION_UNAVAILABLE` until Chat 2/shared contracts are wired.
4. **No approved LIVE Gift creator payout policy.** Creator receipt accounting works, but payable fiat/cash-out remains disabled and `creator_payout_enabled` is false.
5. **Canonical realtime outbox publisher is not yet connected.** Financial events are durably queued but need Chat 1/10 transport integration.
6. **Advanced fraud/device/payment-risk signals are not connected.** The boundary and review case model exist; only exact canonical self-gifting is blocked by the baseline evaluator.
7. **No external provider settlement reconciliation can be claimed before a real provider adapter exposes authoritative settlement/refund/dispute records.**
8. **Final production enablement is intentionally withheld pending Chat 10 hardening and Chat 11 release acceptance.**

These blockers must remain visible in release reporting. They must not be bypassed with fake payment success, fake live context, default payout percentages or client-side balances.
