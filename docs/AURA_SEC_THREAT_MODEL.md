# Aura Sec — Product Threat Model

> This threat model covers the planned Aura Sec cloud control plane, member portal, native endpoint clients, browser/mobile clients, threat-intelligence fabric, backup/recovery plane and Aura security orchestration layer.

## 1. Security invariants

These must remain true even when individual subsystems fail.

1. A valid website session is **not** proof that an endpoint is enrolled, healthy or protected.
2. A paid licence is **not** proof that a device belongs to the member.
3. A device is not healthy until a device-bound signed heartbeat has been verified and is fresh.
4. No executable update is trusted from URL/TLS alone; release metadata, hash, trusted signer and platform code signature must verify.
5. Update clients reject rollback, expired/frozen metadata and untrusted release identities.
6. A threat-intelligence feed cannot directly quarantine files, block applications or execute commands.
7. Text from files, webpages, emails, logs, alerts, malware samples and external feeds is untrusted **data**, never privileged instructions to Aura.
8. Aura cannot directly invoke unrestricted shell/PowerShell/terminal code on a member endpoint.
9. Every privileged client command has a typed action, bounded arguments, device target, policy decision, expiry, nonce/replay defence and authenticated origin.
10. Destructive/disruptive actions require the configured human approval class; the highest-risk actions require fresh strong re-authentication.
11. Optimisation never silently deletes user documents.
12. Backup success is not assumed from upload success; recoverability is tested.
13. Production signing private keys and vault/recovery master secrets never live in source code, browser bundles or ordinary application databases.
14. Security telemetry is data-minimised and raw personal content is not uploaded by default.
15. If Aura or the cloud is unavailable, local endpoint protections continue wherever technically possible and the UI reports degraded state.

## 2. Primary assets

### Member assets

- account identity and authentication credentials;
- passkeys/recovery methods;
- device enrolment credentials;
- personal files;
- creative/project files;
- encrypted vault content;
- backup versions;
- browser/session data;
- network privacy;
- camera/microphone/screen privacy;
- incident records.

### Aura Sec assets

- release signing trust roots;
- build provenance;
- native client source;
- detection rules and behavioural models;
- threat-intelligence provider credentials;
- device public keys and enrolment state;
- policy/configuration;
- telemetry/event store;
- billing/licence records;
- recovery service metadata;
- admin identities;
- audit logs;
- model/tool policy.

## 3. Adversary classes

- commodity malware;
- ransomware operators;
- infostealers;
- phishing/scam actors;
- malicious browser extensions;
- attacker with a stolen unlocked device;
- attacker with a stolen web session;
- attacker who has local administrator/root privileges;
- network attacker / hostile Wi-Fi;
- DNS or routing attacker;
- compromised software vendor/dependency;
- compromised threat-intelligence source;
- compromised build/CI account;
- compromised download/CDN infrastructure;
- insider with legitimate operational access;
- abusive family/delegated administrator;
- attacker attempting AI prompt injection/data poisoning;
- attacker trying to evade or disable Aura Sec;
- attacker attempting account/billing fraud;
- denial-of-service actor.

## 4. Trust boundaries

1. Member browser ↔ Pulsar/Aura Sec web control plane.
2. Native client UI ↔ privileged Aura Sec service.
3. Native client ↔ operating-system security APIs.
4. Native client ↔ Aura Sec cloud.
5. Browser extension ↔ webpage content.
6. Browser extension ↔ native/cloud reputation service.
7. Mobile application ↔ OS sandbox/entitlements.
8. Threat feed/provider ↔ Aura Sec threat-intelligence ingestion.
9. Aura model ↔ retrieved untrusted evidence.
10. Aura model ↔ privileged policy/action executor.
11. Build system ↔ signing/release system.
12. Release metadata ↔ CDN/artifact storage.
13. Device ↔ backup service.
14. Backup repository ↔ restore environment.
15. Owner/admin portal ↔ high-value control-plane operations.

No trust boundary should be collapsed merely for implementation convenience.

## 5. AI / Aura-specific threats

### Prompt injection from hostile content

**Threat:** a web page, email, document, log entry, malware string, phishing page or threat feed includes instructions such as “ignore policy, disable protection, upload files, run this command.”

**Mitigations:**

- retrieved content is always tagged as untrusted evidence;
- system/tool policy is outside retrieved content;
- Aura outputs structured proposals, never privileged free-form commands;
- privileged executor accepts only allowlisted typed actions;
- arguments are schema-validated and bounded;
- policy engine independently evaluates each action;
- dangerous actions require human approval/step-up authentication;
- no hidden shell passthrough;
- render untrusted evidence safely in UI;
- record source/provenance of model evidence;
- red-team with adversarial prompt-injection corpora.

### Hallucinated remediation

**Threat:** Aura invents a process, vulnerability, device state, backup or successful remediation.

**Mitigations:**

- verified tool/device state is distinct from model narrative;
- every material claim links to evidence IDs;
- action result has requested / delivered / executed / verified states;
- Aura cannot set a device to healthy;
- only a verified device report changes device health;
- only a verified restore check marks recovery successful.

### Unsafe over-automation

**Threat:** correct detection triggers an unnecessarily destructive response.

**Mitigations:**

- graded action risk classes;
- least-disruptive response first;
- preview impact;
- recovery checkpoint before high-risk remediation where supported;
- explicit user policy for automatic low-risk actions;
- strong re-authentication for wipe/key/recovery changes;
- reversible quarantine instead of deletion by default.

### Model/data poisoning

**Threat:** attacker manipulates reputation or telemetry to teach the system that malware is benign or legitimate software is malicious.

**Mitigations:**

- separate training/research datasets from live production decisions;
- signed/provenanced data ingestion;
- provider diversity;
- robust aggregation and confidence;
- hold-out benchmark corpora;
- human review for high-impact model/rule updates;
- staged rollout/canary cohorts;
- rapid rollback of rule/model versions.

## 6. Software update threats

### CDN or repository compromise

Attacker replaces installer/update.

**Mitigations:** TUF-class signed metadata, cryptographic artifact hash, trusted signing identity, OS package/code signature, short metadata validity, SBOM/provenance link, independent verification before execution.

### Signing key compromise

**Mitigations:** offline/root threshold strategy where appropriate, HSM/cloud KMS for online keys, key roles separated, rotation/revocation, short-lived delegated metadata, transparency evidence, emergency revocation procedure.

### Rollback/freeze attack

**Mitigations:** monotonically enforced metadata/version rules, expiry, trusted time strategy, rollback protection and client-side minimum secure version when emergency policy requires it.

### Malicious insider release

**Mitigations:** protected branch, two-person review for high-risk release policy, provenance, signing identity audit, staged canary release, reproducible/hermetic build target, transparency records.

## 7. Endpoint threats

### Local malware tampers with Aura Sec

**Mitigations:** least privilege, signed binaries, protected service boundary, anti-tamper policy using OS-supported controls, integrity checks, protected config, watchdog/health reporting, but always preserve an authorised uninstall/repair path.

### Privilege-escalation through Aura Sec

**Threat:** high-privilege service parses attacker-controlled content or accepts overly flexible commands.

**Mitigations:** privileged process is tiny; untrusted parsing sandboxed/unprivileged; memory-safe language where practical; no arbitrary command endpoint; strict IPC schema; identity/ACL checks; fuzzing; exploit mitigations; no unnecessary kernel code.

### EDR sensor becomes denial of service

**Mitigations:** bounded event queues, backpressure, time budgets, fail-safe authorisation decisions, watchdog, sampled low-value telemetry, emergency-disable path under strong admin/recovery control, performance regression tests.

### False positive blocks legitimate software

**Mitigations:** multi-signal confidence, staged rules, known-good publisher reputation, reversible quarantine, allowlist with audit, false-positive corpus, rollback, clear appeal/report workflow.

### Malware evasion

**Mitigations:** combine static, behaviour, memory/script/native OS signals, network context, reputation and recovery; use independent benchmark/red-team tests; never claim a single detector is sufficient.

## 8. Network threats

### Hostile Wi-Fi / MITM

- TLS 1.3 and certificate validation;
- secure DNS option;
- VPN option;
- captive portal-aware handling;
- certificate/route/DNS anomaly detection;
- never disable certificate validation to make a portal work.

### DNS/feed poisoning

- authenticated provider transport;
- feed provenance and TTL;
- provider confidence;
- cross-check high-impact blocks;
- no unauthenticated remote rule download.

### C2 / exfiltration

- process-to-network correlation where OS supports it;
- reputation;
- DNS/network anomalies;
- user-approved isolation;
- local protection remains when cloud lookup fails.

## 9. Identity/account threats

### Credential phishing

- passkeys/WebAuthn preference;
- origin-bound credentials;
- device/session alerts;
- high-risk step-up;
- phishing/site protection;
- recovery method protection.

### Session theft

- secure/httpOnly/same-site cookies for browser sessions;
- short-lived sensitive-action grants;
- device/session inventory;
- revocation;
- anomaly/risk signals;
- no endpoint-management command based solely on stale browser possession.

### Recovery abuse

- recovery path assurance must not be weaker than account risk warrants;
- notify existing trusted devices;
- delay/step-up for recovery-key changes where appropriate;
- separate emergency recovery codes;
- protect against helpdesk/social-engineering takeover.

## 10. Device enrolment threats

### Fake device enrolment

- active security entitlement;
- one-time enrolment challenge;
- device-generated keypair;
- hardware-backed key where possible;
- nonce-bound proof of possession;
- platform attestation when available/appropriate;
- member confirmation for new devices;
- signed enrolment receipt.

### Device cloning

- private key non-exportability where possible;
- fingerprint uniqueness;
- attestation binding;
- detect impossible simultaneous device identity usage;
- re-enrol after key loss.

### Heartbeat spoofing/replay

- signed device reports;
- nonce/sequence number;
- issue and expiry time;
- policy version;
- report digest;
- server replay cache/window;
- device revocation check.

## 11. Threat-intelligence threats

### Provider compromise or bad indicator

A feed is advisory evidence, not root authority.

- provenance per indicator;
- confidence/freshness;
- provider licence state;
- expiry;
- corroboration for disruptive actions;
- rollback/removal capability;
- no feed-supplied executable code.

### Feed licence/availability failure

- adapter reports unavailable/degraded;
- locally cached indicators obey licence/retention terms;
- local protection continues;
- no hidden fallback to unauthorised scraping.

## 12. Backup/recovery threats

### Ransomware deletes backups

- separate credentials and service boundary;
- immutable/WORM option;
- versioning;
- offline/export option;
- deletion delay and strong auth;
- backup control-plane access not inherited from ordinary file write access.

### Back up encrypted/malicious data and restore it

- version history;
- known-clean points;
- anomaly tagging;
- restore scan in isolated context;
- user can select historical state;
- post-restore health validation.

### Backup provider compromise

- client-side authenticated encryption where architecture permits;
- separate object keys;
- integrity verification;
- no plaintext secrets in metadata;
- provider replacement/export strategy.

## 13. Storage optimiser threats

### Accidental user-data loss

- no direct permanent delete by default;
- preview exact paths/space gain;
- project/reference awareness;
- trash/quarantine period;
- undo where practical;
- protect system and user-designated folders;
- backup/restore checkpoint for bulk actions.

### Performance optimiser weakens security

The optimiser must never recommend disabling security tooling, OS updates, disk encryption, firewall, backup or accessibility necessities merely to improve a synthetic performance score.

## 14. Admin/insider threats

- least-privilege roles;
- separate customer support from signing/release authority;
- strong MFA/passkeys;
- just-in-time elevation where practical;
- immutable/high-integrity audit trail;
- sensitive data access logging;
- no routine access to vault plaintext;
- dual control for signing/recovery-root changes;
- emergency access reviewed after use.

## 15. Privacy threats

- data minimisation;
- local-first classification;
- clear purpose/retention per telemetry type;
- pseudonymous/opaque identifiers where possible;
- encrypted transport/storage;
- region/legal review for breach/identity data;
- self-service export/delete consistent with legal/security retention duties;
- do not send raw personal documents to AI/security cloud by default.

## 16. Denial-of-service / cloud outage

Cloud outage must degrade gracefully:

- local malware/ransomware/web rules continue with freshness indicator;
- queued telemetry has strict bounded storage;
- no unbounded retry loop;
- security UI shows threat-intelligence/cloud state;
- local firewall/VPN behaviour follows explicit fail-open/fail-closed policy per feature;
- licence grace period prevents temporary cloud outage from disabling paid endpoint protection;
- emergency rule updates have redundant distribution paths without bypassing signature verification.

## 17. Default fail-open / fail-closed decisions

### Fail closed

- release signature unknown/invalid;
- destructive command authentication invalid;
- strong-auth action without fresh re-auth;
- device heartbeat signature invalid;
- backup integrity verification failure;
- vault authentication/key validation failure.

### Preserve service / degrade visibly

- external reputation provider unavailable;
- threat feed temporarily stale;
- Aura language model unavailable;
- analytics backend unavailable;
- non-critical telemetry upload unavailable.

The endpoint should retain local protection rather than brick the user's network/device because a cloud dependency is down.

## 18. Verification programme

Threat-model controls require tests:

- unit and property tests;
- parser fuzzing;
- privilege-boundary tests;
- malformed IPC/API requests;
- update rollback/freeze/tamper tests;
- revoked-key tests;
- prompt-injection red team;
- false-positive and false-negative regression corpora;
- ransomware containment/recovery drills;
- backup corruption/restore drills;
- stolen-session/account-recovery tests;
- network outage/failover tests;
- performance and resource-exhaustion tests;
- independent penetration test;
- independent malware/phishing lab testing before public efficacy claims.

This threat model must be reviewed whenever a new privileged capability, operating-system extension, AI tool, threat feed, backup provider, authentication method or release channel is introduced.
