# ESP Brand & Commercial Opportunities

Private Elevate Souls Productions commercial-growth workflow inside Pulsar-Frequency House.

## Built in this stage

- Owner-created brand/campaign briefs with niche, region and platform eligibility.
- Compensation summary, usage/licensing terms, exclusivity terms and disclosure requirements.
- Creator-controlled applications and portfolio references.
- Agent-assisted applications only for actively assigned ESP creators with recorded creator opt-in.
- Creator+Agent (`both`) accounts can act in Agent view for assigned creators without losing their Creator self-service path.
- Owner-assisted applications still require creator opt-in.
- Agent review and shortlisting; final approval/rejection remains ESP Owner-controlled.
- Campaign deliverables, due dates, submission evidence, revision/review state, published evidence and metrics.
- Payment/invoice **state tracking only**. This module does not transfer money, store bank credentials or execute contracts.
- Immutable-style activity rows for campaign/application workflow actions.
- Private Level Up Hub navigation and capability-status overlay for Creator, Agent, Both and Owner roles.

## Security boundaries

The module reuses the existing ESP membership boundary. Agent access to another creator's application requires an active `esp_agent_creator_assignments` relationship. Revoking that assignment removes Agent visibility. No global creator directory is exposed by this feature.

## Future extensions

- brand contact/CRM pipeline and opportunity sourcing
- creator media-kit/profile auto-fill
- external e-signature/contract adapters after approved provider selection
- invoice/accounting provider adapters
- deadline notifications and escalations
- campaign content approval UI with Creative Library previews
- case-study generation from verified campaign outcomes
- richer commercial analytics and owner forecasting

Provider-backed actions must remain disabled until genuine credentials, permissions and legal/commercial approvals are configured.
