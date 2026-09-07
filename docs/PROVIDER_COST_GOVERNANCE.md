# Provider Cost Governance

## Purpose

Provider Cost Governance gives Mary and Kev an owner-only operational view of the real cost of running AI and rendering providers inside the Elevate Souls Productions Content Creation Command Center powered by Aura AI.

This subsystem is deliberately separate from:

- member subscriptions and billing status;
- Creation Coins / platform credits;
- ESP Creator, Agent, Creator+Agent and Owner permissions;
- TikTok LIVE metrics, diamonds or creator earnings.

Provider cost records can never activate a plan, grant an ESP role, debit a Creation Coin wallet or change a member entitlement.

## Truthful accounting model

The application never invents a provider price.

Every successful metered provider submission records a usage event. Cost is represented in this order:

1. **Actual cost** — an owner-reconciled amount from a genuine provider bill, invoice or trusted provider report.
2. **Operator estimate** — an explicitly configured per-submission estimate when actual cost is not yet known.
3. **Unpriced** — the usage event remains visible with an unknown cost when neither actual nor configured estimate is available.

The owner summary uses actual cost where known and configured estimate otherwise. Unpriced jobs remain counted separately; they are not presented as zero-cost jobs.

## Current automatic metering

`install_provider_cost_governance()` wraps successful `ComfyUIRenderer.submit()` calls at application startup.

The provider submission occurs first. If provider submission succeeds, operational metering is attempted. If the cost ledger is temporarily unavailable, the original successful member render remains successful; provider-cost accounting is not allowed to turn a valid creative job into a false render failure.

Current units:

- video — frames submitted;
- image — one render unit per successful provider submission.

The event key is idempotent for provider + service + operation + provider job reference, so the same accepted provider job is not counted twice.

## Privacy and data minimisation

The ledger does not persist raw member IDs, project names or provider job IDs. Those references are SHA-256 hashed before storage.

The owner API and owner dashboard do not expose the hashed reference fields either. They expose only operational aggregates and opaque provider-cost event IDs required for reconciliation.

The ledger must never store:

- prompts or generated creative content;
- provider credentials, API keys or access tokens;
- ComfyUI workflow filesystem paths;
- raw member/project/provider-job identifiers;
- Creation Coin wallet data.

## Currency

Operational currency defaults to GBP.

Configure another ISO three-letter currency only when the complete operational ledger for that deployment is intentionally being run in that currency:

```text
AURA_PROVIDER_COST_CURRENCY=GBP
```

Provider-cost events are kept in one configured operational currency. This stage does not perform foreign-exchange conversion.

## Operator estimates

Estimates are configured in minor currency units. For GBP, `45` means £0.45.

The most specific configured key wins:

```text
AURA_PROVIDER_COST_ESTIMATE_<PROVIDER>_<SERVICE>_<OPERATION>_MINOR
AURA_PROVIDER_COST_ESTIMATE_<PROVIDER>_<SERVICE>_MINOR
AURA_PROVIDER_COST_ESTIMATE_<PROVIDER>_MINOR
```

Example:

```text
AURA_PROVIDER_COST_ESTIMATE_COMFYUI_VIDEO_CREATE_MINOR=45
AURA_PROVIDER_COST_ESTIMATE_COMFYUI_VIDEO_MINOR=60
AURA_PROVIDER_COST_ESTIMATE_COMFYUI_MINOR=90
```

For a ComfyUI video `create` operation, the first value is used. A ComfyUI video operation without its own key falls back to the service value. A ComfyUI image operation can fall back to the provider value.

These values are operator estimates only. They must be set from genuine infrastructure/provider pricing or measured internal operating cost; they are never hard-coded product prices.

## Actual-cost reconciliation

Mary or Kev can reconcile a recorded event from the owner dashboard when a genuine actual figure becomes available.

Owner route:

```text
/owner/provider-costs
```

The reconciliation action stores the actual amount in minor units. Once reconciled, the actual amount replaces the estimate when effective spend is calculated.

Owner API:

```text
GET /owner/api/provider-costs/summary?days=30
```

Both surfaces require the existing signed owner session.

## Budgets and warnings

Daily and monthly operational budgets are optional:

```text
AURA_PROVIDER_COST_BUDGET_DAILY_MINOR=5000
AURA_PROVIDER_COST_BUDGET_MONTHLY_MINOR=100000
AURA_PROVIDER_COST_WARNING_PERCENT=80
```

The default warning threshold is 80 percent.

Budget handling in this stage is intentionally **warning-only**. A budget warning or over-budget state does not block a member render. Hard spend enforcement must not be enabled until provider billing, retry semantics and owner override/recovery procedures are explicitly designed and tested.

## Owner dashboard semantics

The owner dashboard separates:

- effective spend;
- jobs reconciled with actual cost;
- jobs using configured estimates;
- unpriced jobs;
- provider/service/operation breakdown;
- daily and monthly budget state;
- recent provider events for actual-cost reconciliation.

The page includes explicit language that provider operating cost is not Creation Coins, subscription revenue, creator earnings or an ESP permission system.

## Launch checklist

Before production launch:

1. Confirm `AURA_PROVIDER_COST_CURRENCY` for the production environment.
2. Set only estimates that are supported by genuine provider/infrastructure pricing evidence.
3. Configure daily/monthly warning budgets if Mary and Kev want spend alerts in the owner dashboard.
4. Verify the owner cost page is accessible only with an authorised owner session.
5. Run a real provider submission and confirm one idempotent ledger event is created.
6. Reconcile a test event with a known actual amount and verify the summary prefers actual cost.
7. Confirm prompts, secrets, raw member IDs, raw project names and raw provider job IDs are absent from the ledger and API response.
8. Recheck provider pricing whenever infrastructure/provider contracts change.

## Deployment policy

This feature is built and validated on `feature/provider-cost-governance`, where Vercel Git deployment is disabled. It should reach `development/full-site-build` only after the exact feature head passes the repository Security Gates and Command Center CI. Production Vercel deployment remains deferred until the complete site build is finished and passes the final launch gate.
