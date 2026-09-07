# Chat 5 Cosmic Coins & LIVE Gifts Integration Contract

## Purpose and commercial lock

This is the canonical integration contract for Chat 5's server-authoritative Cosmic Creation Coin economy and first-party Shared Sky LIVE Gift transaction system.

Commercial baseline:

- 1,000 Cosmic Creation Coins = £5.00;
- 1,000 integer Coin units;
- 500 integer GBP minor units;
- promotional/larger packs use only their explicitly stored server-side price/version;
- no automatic bulk discount exists;
- the creator marketplace 50/50 split is not a LIVE Gift payout policy;
- no LIVE Gift creator percentage, Coin-to-cash ratio or cash-out formula is configured by Chat 5.

A committed Gift receipt is not automatically cash, cleared creator earnings, recognised ESP revenue or a paid balance.

## Canonical modules

### `aura_music_studio.cosmic_economy`

Owns the durable financial schema and core transaction service:

- Coin accounts;
- append-only Coin ledger;
- Coin pack catalogue;
- pending Coin purchases;
- verified payment-event application;
- Gift catalogue;
- atomic Gift send;
- Gift reversal;
- creator Gift receipts;
- platform/Owner spending policy;
- payout-policy interface;
- durable outbox;
- transaction history;
- creator statements;
- core reconciliation.

### `aura_music_studio.cosmic_economy_integrations`

Owns integration hardening and compatibility seams:

- `IntegratedCosmicEconomy`;
- global purchase/Gift-send idempotency ownership;
- durable finance request admission/rate limiting;
- replay-safe rate idempotency reservations;
- non-financial operational evidence for rejected money actions;
- extended purchase/reversal reconciliation;
- `configure_economy_integrations(...)`;
- `economy_service(...)`.

### `aura_music_studio.cosmic_economy_command_idempotency`

`CommandBoundCosmicEconomy` binds consequential non-purchase commands to an exact request fingerprint and result reference.

Covered commands:

- Owner Coin adjustment;
- promotional Coin grant;
- Gift reversal.

A key reused with different user, amount, campaign, Gift, reason or reference is rejected with `IDEMPOTENCY_KEY_REUSED` rather than silently returning an unrelated financial result.

### `aura_music_studio.cosmic_purchase_checkout`

`CheckoutBoundCosmicEconomy` persists provider checkout identity:

- internal purchase ID;
- provider;
- provider payment ID;
- checkout URL;
- checkout status.

The same internal purchase reuses its stored provider checkout. A provider payment reference cannot be bound to two purchases.

### `aura_music_studio.cosmic_economy_personal_limits`

`PersonalLimitCosmicEconomy` is the canonical runtime returned by `economy_service(...)`.

It adds:

- member-controlled lower spending caps;
- personal limit audit history;
- atomic per-creator Gift-receiving controls.

The runtime inheritance chain includes checkout binding, exact command idempotency and the base integration hardening, so normal API/internal consumers cannot bypass those safety layers by constructing the standard service.

### `aura_music_studio.cosmic_payments`

Provider-neutral payment boundary:

- `CoinPaymentProvider` protocol;
- `CoinCheckout` result;
- `coin_payment_providers` explicit provider registry.

`CoinPaymentProvider.create_checkout(...)` receives a server-generated idempotency key. A compliant real provider adapter must use the provider's official idempotency/retrieve-or-create mechanism so one internal purchase cannot create multiple chargeable payment intents.

No provider is considered available until an official adapter is explicitly registered. Client redirects never credit Coins.

### `aura_music_studio.cosmic_economy_api`

Authenticated member, payment-webhook and core Owner routes.

### `aura_music_studio.cosmic_economy_owner_ops`

Audited Owner operations:

- risk review;
- creator receipt holds/releases;
- promotional Coin grants;
- Coin-pack availability;
- Gift catalogue availability;
- discrepancy review/resolution;
- truthful finance/liability snapshot.

### `aura_music_studio.cosmic_economy_owner_api`

Protected HTTP routes for Chat 9 Owner/Admin surfaces, including finance operational-event inspection and per-creator Gift-receiving controls.

## Persistence and transaction model

Chat 5 reuses `LSS_DB_PATH` and the repository SQLite/WAL conventions.

Consequential financial writes use `BEGIN IMMEDIATE` transaction serialization. The append-only ledger is the source of financial truth. Materialised balances exist for fast admission but must reconcile to ledger state.

Coin ledger UPDATE and DELETE operations are blocked by database triggers. Corrections use compensating entries.

## Important tables

Core financial tables:

- `coin_accounts`;
- `coin_ledger_entries`;
- `coin_packs`;
- `coin_purchases`;
- `payment_webhook_events`;
- `coin_purchase_checkouts`;
- `gift_definitions`;
- `gift_transactions`;
- `creator_gift_receipts`;
- `gift_payout_policies`;
- `account_spending_limits`;
- `economy_risk_cases`;
- `economy_reconciliation_discrepancies`;
- `economy_outbox`.

Safety/integration tables:

- `economy_rate_events`;
- `economy_rate_idempotency`;
- `economy_operational_events`;
- `economy_command_idempotency`;
- `personal_spending_limits`;
- `personal_spending_limit_changes`;
- `creator_gift_controls`.

## Coin ledger rules

Financial quantities are integers.

Important entry types include:

- `PURCHASE_CREDIT`;
- `PROMOTIONAL_CREDIT`;
- `OWNER_APPROVED_ADJUSTMENT_CREDIT`;
- `OWNER_APPROVED_ADJUSTMENT_DEBIT`;
- `GIFT_DEBIT`;
- purchase refund/chargeback compensation;
- `GIFT_REVERSAL_CREDIT`.

Ordinary Gift sends cannot take available Coins below zero. Chargebacks after spend create explicit recovery debt rather than erasing unrelated financial history.

## Coin pack contract

Canonical seed pack:

- pack ID: `cosmic-1000-gbp`;
- version: `1`;
- Coin quantity: `1000`;
- fiat amount minor: `500`;
- fiat currency: `GBP`.

Owner catalogue changes can disable/enable a stored pack version without rewriting its historical Coin quantity, fiat price or currency.

Member route:

- `GET /economy/coin-packs`.

Owner routes:

- `POST /owner/economy/coin-packs`;
- `POST /owner/economy/coin-packs/{pack_id}/versions/{version}/availability`.

## Payment/purchase contract

Member command:

- `POST /economy/me/coin-purchases`;
- required `Idempotency-Key` header.

Flow:

1. server creates/replays the internal Coin purchase from authoritative pack/version data;
2. server checks `coin_purchase_checkouts`;
3. if checkout is already bound, it is returned without another provider call;
4. otherwise provider `create_checkout(...)` is called with the internal purchase ID as provider idempotency key;
5. provider payment ID + checkout URL are bound atomically to the purchase;
6. this route still credits zero Coins;
7. only a verified provider event may confirm and credit the purchase.

Webhook:

- `POST /auth/economy/payment-webhooks/{provider_name}`;
- adapter must verify authenticity before returning `VerifiedPaymentEvent`;
- duplicate provider events are idempotent;
- forged/unverified events are rejected;
- `confirmed` credits once;
- `failed`/`cancelled` close a pending purchase without Coin credit;
- refunds/chargebacks/dispute reversals use compensating accounting;
- persisted checkout status is synchronized from the resulting authoritative purchase state.

Production state remains fail-closed while no real Coin payment provider adapter/credentials are registered.

## Gift catalogue contract

Original first-party seed Gift:

- Gift ID: `starlight-spark`;
- version: `1`;
- Coin cost: `10`.

Historical Gift transactions snapshot Gift version and Coin cost.

Member route:

- `GET /economy/gifts`.

Owner routes:

- `POST /owner/economy/gift-catalogue`;
- `POST /owner/economy/gift-catalogue/{gift_id}/versions/{version}/availability`.

Availability changes do not rewrite historical Gift price/version evidence.

## Gift send contract

Member command:

- `POST /economy/me/gifts/send`;
- required `Idempotency-Key` header.

The server validates:

- authenticated canonical sender;
- sender Coin-account status;
- canonical eligibility adapter;
- authoritative live-session context;
- correct recipient membership in that live context;
- Gift ID/version availability;
- region/policy eligibility through shared adapter;
- platform spending policy;
- member personal spending cap;
- creator-specific Gift-receiving state;
- risk decision;
- sufficient available Coins;
- server-side Coin cost;
- emergency feature flags;
- optional Battle/session/round reference consistency.

A successful commit atomically links:

- `GIFT_DEBIT` ledger entry;
- `gift_transactions` row;
- `creator_gift_receipts` row;
- `economy_outbox` committed-Gift event.

A failed Gift transaction changes no financial balance.

## Per-creator Gift receiving control

Owner route:

- `POST /owner/economy/creators/{creator_recipient_id}/gift-receiving`.

State is stored in `creator_gift_controls`.

A database trigger rejects insertion of a committed Gift transaction while that recipient is disabled. Because the trigger fires inside the same money-moving database transaction, any tentative ledger debit and creator receipt are rolled back with the Gift.

Disabled recipients produce `CREATOR_GIFT_RECEIVING_DISABLED` and non-financial operational evidence rather than consuming sender Coins.

## Idempotency and request admission

Purchase and Gift-send keys have global account ownership. Cross-account reuse is rejected.

Finance request admission is durable in the economy database.

Configuration:

- `LSS_ECONOMY_RATE_WINDOW_SECONDS` — default `60`;
- `LSS_COIN_PURCHASE_RATE_LIMIT` — default `8` per account/window;
- `LSS_GIFT_SEND_RATE_LIMIT` — default `120` per account/window.

Invalid limit configuration fails closed.

`economy_rate_idempotency` reserves a request key in the same transaction as rate admission, so concurrent same-key retries consume one admission slot.

`economy_command_idempotency` separately binds Owner adjustments, promotional grants and Gift reversals to an exact request fingerprint/result.

Chat 10 may add edge/distributed abuse controls. It must not remove this financial-service boundary.

## Spending controls

Platform/Owner policy is stored in `account_spending_limits` and supports daily/weekly/monthly hard limits and warning thresholds.

Member personal limits are stored separately in `personal_spending_limits`.

Member routes:

- `GET /economy/me/spending`;
- `PUT /economy/me/personal-spending-limits`.

The spending response includes:

- current spend totals;
- platform limits/warnings;
- personal limits;
- effective hard limits;
- remaining hard-limit allowance.

Effective hard limit = stricter non-null value of platform and personal cap.

Personal changes are recorded in `personal_spending_limit_changes` and emit `economy.personal_spending_limits_changed` outbox evidence.

## Finance operational evidence

Rejected money actions may create non-financial evidence in `economy_operational_events`.

Current event classes include:

- `economy.rate_limit_blocked`;
- `economy.spending_limit_blocked`;
- `economy.personal_spending_limit_blocked`;
- `economy.insufficient_balance`;
- `economy.risk_hold`;
- `economy.risk_block`;
- `economy.account_restriction_block`;
- `economy.creator_receiving_block`.

These records do not debit/credit Coins and do not expose provider secrets or internal fraud thresholds.

Owner inspection:

- `GET /owner/economy/operational-events`.

## Creator receipt/liability contract

`creator_gift_receipts` is separate from sender Coin balances, marketplace accounting and ESP recognised revenue.

Receipt states support:

- pending;
- held;
- cleared;
- adjusted;
- reversed;
- paid.

`paid` must not be used without a real payout path/policy.

No default payout policy is inserted. When none is configured, creator statements show that payout calculation is not configured and payable fiat remains null.

Owner statement route:

- `GET /owner/economy/creator-statements/{creator_recipient_id}`.

## Reversals and chargebacks

Gift reversal creates linked compensation and creator-receipt reversal while preserving the original Gift and ledger debit.

Gift reversal idempotency is bound to the exact Gift/reason/reference. A new key cannot re-reverse an already reversed Gift.

Payment refunds/chargebacks preserve original purchase credit evidence and create explicit reversing accounting. If spend has already occurred, recovery debt is represented explicitly.

No reversal path deletes committed financial history.

## Risk/review contract

The risk adapter returns one of:

- allow;
- monitor;
- hold;
- block.

Baseline deterministic protection blocks exact canonical-identity self-gifting. Rich linked-account/device/payment/collusion signals remain a Chat 10/shared-security integration.

Owner review routes:

- `GET /owner/economy/risk-cases`;
- `POST /owner/economy/risk-cases/{case_id}/review`;
- `POST /owner/economy/creator-receipts/{receipt_id}/hold`.

A heuristic signal alone does not silently erase money or automatically impose an irreversible creator sanction.

## Promotional Coins

Promotional grants use `PROMOTIONAL_CREDIT`, not purchase credit.

Owner route:

- `POST /owner/economy/promotional-credits`.

Grant requires canonical user ID, integer Coin quantity, campaign reference, idempotency key and Owner reason/audit evidence.

Promotional issuance remains separately visible in finance reporting.

## Reconciliation

`economy_service().reconcile()` checks at minimum:

- materialised account balance/recovery debt vs ledger derivation;
- committed Gift vs `GIFT_DEBIT`;
- committed Gift vs creator receipt;
- confirmed purchase vs purchase credit;
- refunded/charged-back purchase vs reversal entry;
- reversed Gift vs reversal credit;
- reversed Gift vs reversed creator receipt.

Unknown mismatches are persisted in `economy_reconciliation_discrepancies`.

Owner routes:

- `GET /owner/economy/reconciliation`;
- `GET /owner/economy/discrepancies`;
- `POST /owner/economy/discrepancies/{discrepancy_id}/resolve`.

Resolving a discrepancy records review state only. It does not auto-repair financial data. If the underlying mismatch still exists, the next reconciliation can surface it again.

## Owner finance reporting

Protected finance snapshot:

- `GET /owner/economy/finance-snapshot`.

It reports evidence-backed purchase, wallet, Gift, receipt, promotional issuance, risk and discrepancy state.

The following remain null/unknown unless real configured accounting evidence exists:

- recognised ESP revenue;
- creator payable fiat when no payout policy exists;
- processor fees;
- tax;
- profit.

Gross Coin sale data must not be presented as profit.

## Realtime event contract — Chats 3 and 4

Committed Gift event:

- `shared_sky.gift.committed`.

Reversal event:

- `shared_sky.gift.reversed`.

Display-safe payload may contain event/Gift transaction/live-session IDs, privacy-appropriate sender display reference, recipient creator ID, Gift ID/version/presentation metadata, quantity, optional Battle/round references, occurrence time, correlation ID and accessibility animation references.

Do not expose raw payment data, provider secrets, fraud scores or unapproved creator payout values.

Chats 3/4 must not infer financial success before the committed authoritative result/event.

## Battle contract — Chat 6

Chat 5 owns financial commit/reversal truth only.

Chat 6 receives stable Gift transaction/event IDs plus optional Battle/session/round references.

Chat 6 owns Battle eligibility under Battle rules, score value, team allocation, round state and reversal correction policy.

Chat 5 never mutates Battle score directly.

## Integration seams

Use:

`configure_economy_integrations(live_sessions=..., eligibility=..., risk=...)`

### Chat 1

Replace compatibility adapters with canonical shared IDs/event/audit/error contracts as they land.

### Chat 2

Provide authoritative `LiveSessionDirectory.gift_context(...)` implementation.

### Chat 4

Consume member balance, Gift catalogue, spending state, personal-cap surfaces, send-Gift command and committed/reversed realtime events.

### Chat 6

Consume committed/reversed Gift IDs and optional Battle references only.

### Chat 9

Consume creator statements, member history, finance snapshot, operational events, risk queues, creator receiving controls, receipt controls, promotional grants, catalogue controls and reconciliation review APIs. Do not create duplicate finance truth.

### Chat 10

Own production secret/config management, external queues/outbox delivery, distributed edge abuse controls, advanced fraud signals, backup/DR, invariant alerts and infrastructure observability.

### Chat 11

Verify schema/migrations, provider configuration, financial invariants, CI evidence, reconciliation and release blockers before production enablement.

## Production blockers / intentional fail-closed state

Chat 5 is not authorised for final production enablement until all applicable blockers are resolved:

1. real Coin payment-provider adapter and credentials are not configured;
2. canonical age/region eligibility policy is not connected;
3. authoritative Shared Sky live-session adapter is not connected;
4. no approved LIVE Gift payout/cash-out formula exists;
5. canonical realtime outbox dispatcher is not connected;
6. advanced linked-account/device/payment/collusion/account-takeover risk signals are not connected;
7. external provider settlement reconciliation cannot run without the real provider integration;
8. final Chat 10 security/operations acceptance and Chat 11 release acceptance are outstanding.

The correct behaviour while these dependencies are absent is fail-closed or feature-gated operation, never fake success.
