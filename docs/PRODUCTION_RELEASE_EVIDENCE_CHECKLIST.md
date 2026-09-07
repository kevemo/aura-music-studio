# Production Release Evidence Checklist

**Product:** Elevate Souls Productions Content Creation Command Center  
**Endorsement:** Powered by Aura AI  
**Release target:** `main`  
**Candidate source:** `development/full-site-build`

This checklist maps the external release evidence required by `aura_music_studio.production_release_admission` to concrete operator proof. It is intentionally fail-closed. Repository tests prove software contracts; they do not manufacture production credentials, provider approvals, infrastructure operation or third-party evidence.

## Rule for every evidence item

Every accepted evidence record must:

- name the exact 40-character release Git SHA;
- be for the `production` environment;
- have outcome `passed`;
- use an HTTPS evidence reference or controlled `urn:evidence:` reference;
- carry a SHA-256 digest of the evidence artifact/record;
- identify the verifier;
- have timezone-aware observation and expiry timestamps;
- remain unexpired when the release admission decision is evaluated;
- contain no secrets, private keys, raw access tokens or unnecessary personal data.

A different SHA, staging result, expired result, duplicate contradictory record or failed result does not authorise production release.

## Required production gates

| Gate ID | Required operator evidence | Current repository boundary |
|---|---|---|
| `domain_tls` | Production domain resolves to the intended ingress; valid HTTPS/TLS chain; HTTP redirects appropriately; production cookie/security assumptions verified against the real origin. | Code and Caddy/self-host contracts are integrated; real domain/TLS remains external evidence. |
| `production_secrets` | Production secrets are installed through the approved secret-management path; rotation/revocation procedure exercised; no repository/client exposure; required provider/admin/provenance/monitoring secrets verified without recording their values. | Fail-closed configuration checks exist; real secret installation is external. |
| `monitoring_alerting` | Metrics/logging/alerting are active in production; authenticated metrics access verified; at least one controlled alert reaches the intended responder; retention/escalation ownership recorded. | Monitoring/readiness contracts exist; real alert delivery is external. |
| `backup_restore_drill` | Production-representative encrypted backup created and restored into an isolated recovery target; integrity checked; measured RPO/RTO recorded; restoration does not require undocumented secrets. | Backup/restore evidence foundations exist; real drill evidence is external. |
| `deployment_rollback` | Exact candidate deployed through the production procedure and a controlled rollback/redeployment rehearsal succeeds; image/source identities and rollback point are recorded. | Self-host deployment/release contracts exist; real rehearsal is external. |
| `capacity_failure_testing` | Production-representative load/capacity test plus controlled dependency/failure scenarios; resource ceilings, recovery behaviour and any accepted limits recorded. | Topology and safety tests exist; real production-capacity evidence is external. |
| `privacy_security_review` | Current privacy/security review completed for the release scope; high-severity findings resolved or explicitly release-blocked; data/consent/retention and security controls reviewed. | Technical privacy/security controls are integrated; independent/current review is external evidence. |
| `incident_support_readiness` | Incident roles, support escalation, provider outage handling, customer communication and emergency rollback procedures are current and exercised/table-topped. | Product support and fail-closed controls exist; operational rehearsal is external. |
| `provider_payment_e2e` | Real provider applications/permissions and production credentials verified; payment/webhook/refund/payout flows tested as applicable; bank/Open-Banking reconciliation evidence captured where policy requires; external social/provider actions verified only through official authorised APIs. | Provider/payment code is integrated and fail-closed; live provider/financial evidence is external. |
| `production_data_ai_infrastructure` | Production database/object storage, GPU/AI renderer capacity and required service endpoints are provisioned; persistence/restart/recovery and access boundaries verified; actual model/provider capacity recorded. | Self-host and renderer topology contracts are integrated; real infrastructure/capacity is external. |

## GitHub release-control gate

Issue #362 is separate from the ten production evidence records above and must also be closed before production release.

GitHub must report both `main` and `development/full-site-build` as protected (or covered by an active enforcing ruleset) with release controls appropriate to this repository, including:

- pull-request-only release-critical changes;
- required approval and Code Owner review;
- stale-approval dismissal;
- conversation resolution;
- required Command Center CI, Security Gates and Self-Host Smoke checks;
- force-push and branch-deletion prevention;
- appropriate administrator/bypass enforcement.

The repository already contains `.github/CODEOWNERS`; CODEOWNERS text alone is not enforcement.

## Payment and provider truth

Never use browser redirects, local queue state, optimistic UI state or an internal ledger write as proof that an external provider action succeeded. Production evidence must come from the authoritative provider and, where required for financial reconciliation, independent banking evidence.

## Aura Sec boundary

The Command Center web/control-plane completion checkpoint does not authorise the separate Aura Sec native commercial endpoint product. Native commercial release remains subject to issue #64 and its own private-engine, signing/notarisation, device-attestation, updater/rollback, threat-intelligence, privacy/performance and independent security evidence.

## Release admission

Only after the exact release SHA has one valid, fresh, passing record for every required production gate, GitHub release controls are enforced, and all release-candidate repository checks are green should PR #25 be considered for promotion to `main`.

No item in this checklist should ever be marked passed merely to raise a percentage. Evidence first; status second.
