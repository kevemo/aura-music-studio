# Chat 10 Production Incident Runbooks

These runbooks are operational response procedures for the Elevate Souls Productions Content Creation Command Center and the Elevate Souls Productions Secure Lattice System (SLS). They do not authorize a production deployment and do not replace domain-owner decisions. Preserve evidence before destructive recovery steps whenever safety permits.

## General incident rules

1. Record UTC incident start time, reporter, affected environment, deployed revision and observed symptoms.
2. Prefer fail-closed containment over speculative recovery when financial, identity, security or native-device authority may be affected.
3. Do not paste secrets, tokens, private keys, payment payload secrets or member private data into tickets/chat/log summaries.
4. Preserve relevant logs, database snapshots, queue state, provider event IDs and package/build hashes before mutation when practical.
5. Use reversible actions first. Record every operator action and timestamp.
6. Escalate business-domain correctness to the owning chat/team rather than rewriting domain logic during an incident.
7. A service becoming reachable again is not proof that data/security correctness is restored.

---

## 1. Deployment rollback

Trigger examples: elevated 5xx rate after release, readiness failures, authentication failures, corrupt assets, severe latency regression, broken migrations.

### Contain

- Stop further promotion of the suspect revision.
- Record current revision/image/config identifiers without secret values.
- Check `/health/live` separately from `/health/ready`; do not treat liveness as dependency readiness.
- If writes risk corrupting state, disable/route away mutating traffic before rollback where deployment controls permit.

### Recover

- Roll back application revision/image to the last independently verified release candidate.
- Do **not** automatically roll back a database schema/data migration unless its downgrade procedure is explicitly tested.
- Restore configuration references to the last known-good version; never copy secrets into source control.
- Verify runtime storage connectivity and required providers with bounded checks.

### Verify

- Confirm `/health/live` and `/health/ready` behavior.
- Exercise authenticated sign-in/session flow and one safe read path.
- Verify queue consumers, payment webhook admission, Shared Sky and critical creative job admission as applicable.
- Compare error/latency metrics with pre-incident baseline.

### Evidence

Record suspect revision, rollback revision, start/end UTC, reason, data migration state, checks executed and unresolved follow-up.

---

## 2. Database backup restore / disaster recovery

Trigger examples: database corruption, accidental deletion, unrecoverable migration, storage loss.

### Contain

- Stop writers or isolate the damaged environment before restoring.
- Capture a copy/hash of the damaged database if readable for forensics.
- Identify candidate backup/snapshot and its creation time/hash.

### Restore safely

- Restore into an **isolated recovery path/environment first**.
- Verify backup archive hashes.
- Run SQLite integrity validation against the restored copy.
- Run application-level data checks covering representative member/project/account records without exposing private content.
- Record restore duration and backup age.

### Promote

Only replace the damaged production store after the isolated restore passes and the incident owner has explicitly authorized the cutover. Preserve the prior damaged store rather than overwriting it when storage permits.

### Chat 10 evidence command

Synthetic CI mechanism only:

```bash
python scripts/run_restore_drill.py --environment ci --output /tmp/chat10-restore-evidence.json
```

A production DR claim requires evidence from an actual production backup/snapshot; synthetic evidence must never be relabeled.

---

## 3. Queue backlog / dead-letter recovery

Trigger examples: queued work grows continuously, workers stop claiming, stale leases, repeated job failures, DLQ growth.

### Diagnose

- Inspect queue summary: queued/running/failed/dead-letter counts and oldest queued/dead-letter age.
- Identify whether failures are infrastructure/transient or deterministic business errors.
- Confirm worker clock/storage health and lease behavior.

### Recover

- Requeue stale running work only through the queue's bounded stale-recovery path.
- Exhausted stale work must remain dead-lettered until reviewed.
- Never bulk replay a DLQ of payment, Coin/Gift, publishing, provider or other external side-effect jobs without domain-specific idempotency proof.
- `retry_dead_letter` requires explicit idempotency verification.
- Resolve a dead-letter item only with an operator reason.

### Escalation

- Coin/Gift/economy jobs: Chat 5/domain owner.
- Shared Sky transport: Chat 2.
- Social publishing: Chat 9/domain owner.
- Creative render implementation errors: owning creative workstream.

---

## 4. Credential leak / secret rotation

Trigger examples: credential committed, pasted publicly, logged, shared to unauthorized party, anomalous provider use.

### Immediate containment

- Revoke/rotate the exposed credential at the authoritative provider first.
- Disable the affected integration temporarily if revocation cannot be confirmed.
- Do not rely on deleting the secret from the latest Git commit; repository history and caches may retain it.

### Repository response

- Run the committed-secret scanner.
- Remove the value from tracked files/history using an approved history-rewrite procedure when required.
- Rotate downstream secrets that could have been reached with the exposed credential.
- Review provider audit logs and application access logs for unauthorized use.

### Verify

- New secret is supplied only through the approved secret store/environment mechanism.
- Old secret fails authentication.
- Application integration passes a bounded non-destructive verification.
- No secret value appears in incident notes or CI artifacts.

---

## 5. Payment/webhook failure

Trigger examples: signed webhooks stop arriving, verification failures rise, duplicate events, subscription state diverges.

### Contain

- Do not activate paid entitlement from browser return/success URLs.
- Keep unverifiable provider events quarantined/unapplied.
- Preserve provider event ID, signature-verification result and sanitized metadata.

### Diagnose

- Check provider status independently.
- Check webhook endpoint reachability and configured verification credential presence without logging the credential.
- Confirm environment separation: test/sandbox credentials must not be treated as production proof.
- Inspect idempotency/event ledger state for duplicates.

### Recover

- Replay only through the provider-supported signed event mechanism or an explicitly verified reconciliation process.
- Never manufacture a success event in the database.
- Reconcile server-authoritative subscription/native entitlement state against provider evidence.

### Escalation

Creation Coin/Gift financial correctness or reversals belong to Chat 5/domain owner.

---

## 6. Coin/Gift economy anomaly

Examples: wallet mismatch, duplicate gift receipt, unexplained liability, reversal inconsistency.

### Chat 10 containment only

- Preserve ledger/event/job evidence.
- Pause the affected transactional path if continued processing can worsen exposure.
- Do not directly edit balances to "fix" totals.
- Do not replay failed financial jobs without Chat 5 idempotency/reconciliation authority.

### Escalate

Chat 5 owns financial ledger correctness, reversals/refunds, reconciliation and economy controls. Chat 10 supports infrastructure evidence, database/queue health and incident containment only.

---

## 7. Shared Sky outage

Examples: viewers cannot play streams, ingest unavailable, destination relay failures, realtime disconnect surge.

### Triage

- Separate internal player/community failures from ingest/transport failures.
- Check liveness/readiness and relevant queue/provider dependencies.
- Identify scope: one stream, one destination, one region, or platform-wide.

### Contain/recover

- Avoid showing fake viewer/live/health states.
- Fail affected destination state visibly and preserve provider/session identifiers.
- Do not silently switch to an unapproved external destination.

### Escalate

- Transport/ingest/destination: Chat 2.
- Professional studio/control room: Chat 3.
- Viewer/player/community realtime: Chat 4.
- Multi-host/battles: Chat 6.
- Creative studio Go Live & Create: Chat 7.

---

## 8. Social publishing outage

Examples: OAuth expired, provider rate limit, publish jobs repeatedly fail, provider API unavailable.

### Contain

- Do not display a post as published until provider evidence confirms it.
- Keep failed jobs retryable only according to provider/domain idempotency rules.
- Preserve provider request/event IDs and sanitized error class.

### Recover

- Re-authorize through approved OAuth flow when required.
- Respect provider Retry-After/rate limits.
- Reconcile scheduled/published state against provider truth before replay.

### Escalate

Chat 9 owns social product workflow and publishing semantics; Chat 10 owns infrastructure/queue/rate/observability support.

---

## 9. Game Forge sandbox/export incident

Examples: export memory pressure, path traversal attempt, tampered package, unexpected network behavior, generated-code execution concern.

### Immediate containment

- Disable the affected export/runtime route if host execution, secret access or tenant escape is suspected.
- Preserve game/build ID, content hash, export ID, package hash and sanitized logs.
- Do not execute an untrusted generated package to diagnose it on the application host.

### Aura Web export checks

- Verify build hash is current.
- Verify asset rights/publication gates.
- Confirm aggregate asset/media admission.
- Run package integrity verifier before download/use.
- Treat `package_ready` separately from `production_release_ready`.
- Publisher authenticity remains false until trusted release signing is verified.

### Godot preview checks

- Treat as source/developer preview, not production parity.
- Creator text must remain data; fixed reviewed GDScript only.
- Do not claim production until pinned headless Godot validation plus signing evidence exists.

### Escalate

Game Forge product/gameplay corrections belong to Chat 8. Chat 10 handles sandbox/export/runtime production-security boundaries.

---

## 10. Account compromise

Examples: suspicious login, session theft, unexpected owner/admin action, credential stuffing.

### Contain

- Revoke affected sessions.
- Reset/rotate relevant authentication credentials through the supported account recovery path.
- Require strong re-authentication for privileged recovery where available.
- Preserve login/session/security event evidence.

### Investigate

- Review session creation/revocation history.
- Check suspicious IP/user-agent/time patterns without publishing personal data.
- Review owner/admin privilege changes and sensitive exports/actions.
- If a shared secret or OAuth token may be compromised, execute the credential-leak runbook.

### Verify

- Old sessions cannot authenticate.
- New account session works.
- Privileged role/permission state matches authoritative owner policy.

---

## 11. SLS native bad update / compromised native release

Trigger examples: release signature failure, unexpected native binary hash, failed attestation wave, harmful/incorrect device command, notarisation/signing concern.

### Immediate containment

- Stop release promotion and command rollout.
- Mark suspect release as untrusted/revoked through the authoritative release-trust mechanism where supported.
- Do not bypass signature/attestation checks to restore availability.
- Preserve release ID/hash/signature/key ID, affected device IDs, command IDs/sequences and verification failures.

### Device safety

- A connected/enrolled/licensed device is not automatically protected.
- Stale/offline/unverified devices remain explicitly non-protected.
- Do not issue arbitrary shell commands from the portal.
- Privileged remediation must remain within typed allowlisted SLS commands, strong re-authentication, bounded approvals, signed delivery and anti-rollback sequence controls.

### Signing-key compromise

- Revoke/retire the compromised signing identity through the authoritative trust store/process.
- Rotate to a separately protected signing key.
- Re-sign/rebuild only from a known-clean source/release pipeline.
- Require independent verification before resuming rollout.

### Platform signing

Do not claim Windows/macOS/mobile/other platform signing or notarisation unless the real platform evidence exists. A configured identifier or local test signature is not production evidence.

---

## 12. Monitoring/alerting outage

Trigger examples: metrics endpoint unreachable, collector stops scraping, alert delivery fails.

- Treat loss of observability as an operational degradation, not proof that systems are healthy.
- Verify `/health/live`, `/health/ready` and authenticated `/internal/metrics` independently.
- Restore telemetry transport/collector/alert delivery.
- Record the monitoring blind window.
- For high-risk financial/security/native changes during the blind window, consider pausing them until observability is restored.

---

## 13. DNS/SSRF security incident

Trigger examples: outbound request reaches private IP, DNS answer changes between validation and connection, unsafe redirect, metadata-service access attempt.

### Contain

- Disable Aura outbound web access if private-network reachability is suspected.
- Preserve requested URL, redirect chain, resolved addresses and connected peer evidence when available; do not record credentials/query secrets.
- Block the abusive account/request path according to platform moderation/security policy.

### Known release gate

Current public-host DNS validation occurs before the HTTP transport establishes its connection. A proxy-aware binding between validated destination and actual connected peer remains required production evidence. Do not claim DNS-rebinding immunity until that transport control is implemented and tested.

---

## 14. Post-incident closure

Before closing any production incident:

- Root cause is identified or explicitly recorded as unresolved.
- Data/security/financial correctness is verified, not only availability.
- Temporary bypasses are removed.
- Secrets used during response are rotated if exposed.
- Follow-up tests/alerts/runbook changes are committed.
- Release blockers are updated for Chat 11.
- Incident evidence has a retention location and owner without embedding secret/private values.
