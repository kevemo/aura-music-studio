# Aura Sec — Private Native Engine Repository Blueprint

## Master product

**Elevate Souls Productions Content Creation Command Center**  
**Powered by Aura AI**

Aura Sec is the separate security product/service inside the ecosystem. The public web/control-plane repository must not become the long-term home of privileged endpoint implementation or bypass-sensitive detection IP.

## Required private repository

Recommended working repository name:

`aura-sec-engine`

Visibility requirement: **private**.

The private repository should begin before native endpoint protection, signing integration, proprietary detection logic or malware research fixtures are implemented.

## Why isolate the engine

The native security client will eventually contain higher-value material than ordinary web application code:

- privileged operating-system integrations;
- anti-tamper implementation;
- behavioural detection logic;
- private rule packs;
- quarantine/recovery implementation details;
- endpoint command verification;
- update trust-root logic;
- security test corpora metadata;
- signing/release pipeline configuration;
- threat-research tooling.

Public disclosure of interfaces and standards is useful. Public disclosure of implementation details that materially simplify bypass is not.

## Language strategy

New privileged/user-space endpoint components should prefer **Rust** where platform integration permits it because memory safety reduces a major class of vulnerabilities in security software.

Platform-native languages/frameworks remain appropriate where required:

- Windows: Rust/C/C++ FFI only around documented Windows APIs; minimise unsafe surface.
- macOS: Endpoint Security/System Extension integration through supported native interfaces; Swift/Objective-C host UI as appropriate with Rust shared core where practical.
- Linux: Rust shared core plus Aya/eBPF or supported kernel/user-space APIs where justified.
- Android: Kotlin/Java platform host plus Rust shared analysis core only when it materially improves safety/performance.
- iOS/iPadOS: Swift platform host using permitted Network Extension/security APIs; no unsupported full-device scanner claims.

## Proposed workspace

```text
aura-sec-engine/
  Cargo.toml
  rust-toolchain.toml
  crates/
    agent-core/
    agent-protocol/
    policy-engine/
    event-schema/
    telemetry-buffer/
    local-reputation/
    yara-x-adapter/
    quarantine/
    recovery/
    secure-update/
    device-identity/
    storage-analyser/
    network-model/
    platform-windows/
    platform-macos/
    platform-linux/
  apps/
    windows-service/
    windows-ui/
    macos-host/
    linux-daemon/
  mobile/
    android/
    ios/
  browser/
    native-messaging-host/
  fuzz/
  tests/
    benign-corpus-metadata/
    protocol-fixtures/
    ransomware-simulator-safe/
    update-adversary-fixtures/
  docs/
    threat-model/
    privileged-boundaries/
    release-signing/
    privacy/
```

Do **not** place live malware samples in normal Git history. Use a controlled research store with access policy, hashes and explicit handling procedures.

## Shared core responsibilities

### `agent-protocol`

Mirror the public bounded protocol contract:

- signed device heartbeat;
- typed command enums only;
- nonce/sequence replay defence;
- strict expiry;
- signed command receipts;
- evidence digest for verified completion;
- no arbitrary shell/script execution.

### `device-identity`

- device-generated keypair;
- platform hardware-backed key store where available;
- enrolment challenge proof;
- key rotation;
- revocation;
- device attestation adapter where platform support and privacy justify it.

### `policy-engine`

Policy is evaluated independently from Aura's natural-language output.

Inputs:

- typed proposed action;
- action risk class;
- device state;
- member policy;
- incident severity/confidence;
- required approval/strong-auth evidence;
- recovery readiness;
- licence state;
- command freshness.

Output is a bounded allow/deny/require-confirmation decision.

### `event-schema`

Normalise security events without forcing each operating system into identical semantics.

Common fields:

- event id;
- device id;
- monotonic/device timestamp + wall-clock timestamp;
- platform;
- process identity/tree where available;
- executable/file hash;
- signer/publisher identity;
- path category;
- network destination category;
- detection/rule ids;
- ATT&CK mapping where useful;
- confidence;
- evidence digests;
- privacy classification.

### `telemetry-buffer`

- bounded local queue;
- encrypted local persistence for queued records;
- priority dropping of low-value events under pressure;
- no unbounded logs;
- privacy redaction before upload;
- resumable authenticated delivery.

### `local-reputation`

Combine locally cached:

- trusted publishers;
- deny indicators;
- known hashes;
- domain indicators;
- allowlist exceptions;
- signed rule-pack versions.

A cloud outage must not erase all endpoint protection.

### `yara-x-adapter`

Use reviewed YARA-X integration for static content/pattern detections when the exact dependency version and rule licences are approved.

Untrusted file parsing and content scanning should be isolated from the highest-privilege service where practical.

### `quarantine`

- quarantine by object identity/hash;
- move/copy atomically where possible;
- preserve metadata needed for restore;
- authenticated quarantine manifest;
- deny execution from quarantine;
- restore only with explicit policy/approval;
- rescan before restore.

### `recovery`

- recovery checkpoint abstraction;
- backup/snapshot capability detection;
- versioned file restore;
- post-restore hash/health verification;
- rollback state reporting;
- no claim of recovery readiness without recent verification.

### `secure-update`

- TUF-class metadata verification;
- trusted release roles/keys;
- short metadata expiry;
- rollback/freeze protection;
- artifact SHA-256 verification;
- platform code-signature verification;
- SBOM/provenance references;
- staged channels: canary → beta → stable;
- emergency revocation/minimum-secure-version policy.

## Windows-first implementation

The first production native client should target Windows because it provides the broadest initial desktop customer surface.

### User-space service first

Start with the smallest possible privileged Windows service. Avoid writing a kernel driver merely to appear more advanced.

Initial supported integrations:

- Windows Filtering Platform for network policy/filtering;
- AMSI-compatible scanning integration where appropriate;
- Authenticode/publisher verification;
- Windows Event Log/ETW sources selected for documented supported telemetry;
- Security Center / Microsoft Defender coexistence status rather than blindly disabling built-in protection;
- Windows update/application inventory;
- TPM/Secure Boot/BitLocker posture;
- supported startup/task/service inventory;
- Volume Shadow Copy / recovery posture only through documented mechanisms;
- safe filesystem watcher/event sources.

A kernel component is allowed only if a concrete detection/prevention requirement cannot be safely implemented through supported user-space/system interfaces. If introduced it receives its own threat model, signing path, fuzzing, verifier/static analysis and emergency-disable procedure.

## macOS implementation

- Endpoint Security client in System Extension;
- Network Extension for permitted content filter/DNS/VPN functions;
- Keychain/Secure Enclave-backed device secrets where available;
- FileVault/Gatekeeper/notarisation posture;
- notarised host application and extension;
- minimise entitlements.

## Linux implementation

- Rust daemon with systemd hardening;
- fanotify/eBPF/LSM integrations selected per capability and distro/kernel support;
- Aya where eBPF use is approved;
- package manager inventory;
- nftables/network posture;
- full-disk encryption and Secure Boot posture where observable.

## Mobile

### Android

- VpnService for authorised network filtering/DNS/VPN mode;
- Android Keystore hardware-backed keys where available;
- safe-browsing/QR/scam checking;
- package/device posture only through permitted APIs;
- no privilege-escalation/sideload design intended to bypass Android sandboxing.

### iOS/iPadOS

- permitted Network Extension capabilities;
- identity/passkeys;
- secure vault;
- malicious-link/DNS/network protection;
- privacy/security posture available through Apple-supported APIs;
- never market it as arbitrary third-party app memory/file scanning.

## Build security

Minimum private-repo CI gates:

```text
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all
cargo audit
cargo deny check
unit/property tests
protocol conformance tests
fuzz smoke tests
SBOM generation
secret scan
SAST
```

Release pipeline additionally requires:

- protected release branch/tag;
- two-person release policy review;
- build provenance;
- signed SBOM;
- artifact signing;
- OS platform signing/notarisation;
- TUF-class metadata publication;
- canary validation;
- malware/benign regression suite;
- performance regression suite.

## Secrets and keys

Never commit:

- Windows code-signing private key;
- Apple signing private key/certificate secret material;
- TUF offline root private keys;
- online signing service credentials;
- backup encryption root keys;
- threat-feed API secrets;
- production device private keys;
- anti-tamper shared secrets.

Use HSM/KMS or platform-secure signing services according to role and threat model. Offline root keys should remain offline where the update design calls for them.

## Repository relationship

The current web repository remains the source of public-safe contracts and the Command Center integration.

The private engine repository consumes versioned protocol contracts and publishes only:

- signed release metadata;
- release version/status;
- SBOM/provenance references suitable for member verification;
- non-sensitive capability declarations;
- health/receipt messages through the authenticated device protocol.

It must never require the web application to possess private release-signing keys.
