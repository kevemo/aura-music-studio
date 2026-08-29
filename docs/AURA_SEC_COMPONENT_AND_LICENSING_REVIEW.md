# Aura Sec — Component & Licensing Review

> Research/design record, not legal advice. Every dependency must be rechecked at the exact version selected for a production release and included in the product SBOM/licence notices.

Aura Sec should not become a bundle of copied antivirus projects. The preferred design is an original, memory-safe endpoint/control plane that uses well-maintained standards and narrowly selected third-party libraries where their licences, security posture and maintenance model fit a commercial product.

## Selection rules

A component is not approved merely because it is open source.

Evaluate:

1. licence and redistribution obligations;
2. project maintenance and recent releases;
3. security disclosure process;
4. implementation language / memory-safety profile;
5. privilege level required;
6. attack surface added;
7. update/signing model;
8. compatibility with supported operating systems;
9. whether it can be sandboxed or run out-of-process;
10. whether Aura Sec can replace it without breaking core protection;
11. testability and deterministic failure behaviour;
12. supply-chain provenance and dependency quality.

## Preferred / evaluate for direct integration

### YARA-X

**Purpose:** high-performance local pattern matching for malware/content detections.

**Current research:** VirusTotal describes YARA-X as its Rust rewrite of YARA, focused on performance and safety, with C/C++, Python and Go APIs. The project reports production use scanning very large file volumes and is BSD-3-Clause licensed.

**Aura Sec position:** strong candidate for the local static rule engine, subject to exact-version security/licence review. Rule licensing remains separate from engine licensing; Aura Sec must not import third-party rule collections without verifying each collection's licence.

### osquery

**Purpose:** cross-platform operating-system inventory and instrumentation.

**Current research:** osquery exposes OS information as queryable tables and its project licence permits choosing Apache-2.0 or GPL-2.0-only.

**Aura Sec position:** evaluate use under Apache-2.0 for non-real-time inventory/posture collection where it improves portability. Do not make the antivirus/EDR protection path dependent on an external osquery daemon being healthy.

### Zeek

**Purpose:** high-fidelity network transaction visibility.

**Current research:** Zeek is a mature network security monitor under a permissive BSD licence.

**Aura Sec position:** useful for optional home/business network analysis or research infrastructure. It is probably too heavy to make mandatory on every consumer endpoint. Consider server/home-gateway deployment and use a lighter native client network sensor on normal devices.

### Aya (Linux eBPF)

**Purpose:** Rust eBPF tooling for Linux telemetry/filtering.

**Current research:** Aya is implemented in Rust, supports BTF/portable deployment patterns and is dual MIT/Apache-2.0.

**Aura Sec position:** strong Linux sensor candidate. Any eBPF program still requires strict verifier-compatible design, kernel-version testing, resource limits and fail-safe detach behaviour.

### rustls

**Purpose:** TLS client/server implementation for Rust services/agents.

**Current research:** rustls is a memory-safe Rust TLS library, implements modern TLS and offers Apache-2.0, MIT or ISC licence choices.

**Aura Sec position:** preferred evaluation candidate for Rust user-space network clients when platform-native TLS APIs are not the better option. Cryptographic provider and FIPS requirements must be selected per deployment, not assumed.

### The Update Framework implementations

**Purpose:** defend software-update metadata and clients against rollback, freeze and repository/signing compromise classes.

**Current research:** TUF is a CNCF graduated secure-update framework. Python-TUF is MIT/Apache-2.0; tuf-js is MIT. The current Rust implementation is explicitly marked beta/unstable and should not be assumed production-ready solely because it is Rust.

**Aura Sec position:** use TUF design/conformance as the secure-update baseline. Choose a mature implementation appropriate to the update service/client language, or implement only with conformance testing and expert review. Do not choose an immature client solely for language consistency.

### Sigstore / Cosign

**Purpose:** sign and verify software artefacts and provenance with transparency evidence.

**Current research:** Sigstore supports identity-bound short-lived signing and a transparency log; the common project is Apache-2.0 licensed.

**Aura Sec position:** strong fit for build artefacts, SBOMs, provenance and CI attestations. It does not replace Windows Authenticode, Apple Developer ID/notarisation, mobile-store signing or the Aura Sec client's secure update metadata verification.

### SQLite

**Purpose:** small local device/control-plane state stores where appropriate.

**Aura Sec position:** acceptable for bounded non-secret metadata and encrypted-at-rest contexts when filesystem/key handling is appropriate. High-value secrets should use OS keychain/credential-store facilities rather than simply storing them in a SQLite field.

## Standards/formats rather than copied engines

### Sigma

Use Sigma-compatible rule semantics for telemetry detections and ATT&CK tagging. Aura Sec should maintain its own tested rule-pack lifecycle with provenance, licence, false-positive fixtures and staged rollout.

### STIX 2.1 / TAXII 2.1

Use for threat-intelligence interchange between the threat cloud and external partners/providers where useful. Internal hot-path device decisions do not need to serialize every event as STIX.

### MITRE ATT&CK

Use as a behavioural coverage and investigation taxonomy. ATT&CK coverage is not itself proof that a detection works.

## GPL / AGPL projects — legal/architecture review required before distribution

The following are valuable research and interoperability references, but should not be copied into a proprietary endpoint executable without a specific legal/technical decision.

### ClamAV — GPLv2

ClamAV provides a mature cross-platform antivirus engine and signatures. Its GPLv2 licence creates redistribution/derivative-work obligations that require review for any planned commercial bundling or linking strategy.

**Aura Sec default:** do not make embedded ClamAV the proprietary core. It may be useful in research/test infrastructure or as an independently managed optional scanner where the deployment/licensing model is appropriate.

### Suricata — GPLv2

Suricata is a powerful IDS/IPS ecosystem. It is not a lightweight consumer-endpoint dependency, and its GPL licence plus deployment footprint make it more appropriate for separate network/security infrastructure evaluation.

### Wazuh — GPLv2 / mixed component licensing

Wazuh demonstrates mature XDR/SIEM architecture with endpoint agents and central services. Use it as architecture/research comparison, not source to copy into Aura Sec. Any interoperability must respect component-specific licences.

### Velociraptor — AGPLv3

Velociraptor is a capable DFIR/endpoint visibility platform. Its AGPLv3 licensing is particularly important for network-accessed modified deployments.

**Aura Sec default:** research/reference or separately licensed/operated integration only after legal review; not a silent dependency of the proprietary cloud or client.

## Threat-intelligence services and feeds

Data feeds have licences and acceptable-use rules separate from application code.

### NVD

Use supported NVD 2.0 APIs/feeds for CVE/CPE and assessment data. Cache responsibly, respect rate limits and store retrieval provenance/freshness.

### CISA Known Exploited Vulnerabilities

Use as a strong real-world exploitation signal, but still validate that the affected product/version exists on the device.

### FIRST EPSS

Use exploitation probability as one prioritisation signal, not a guarantee that a vulnerability will or will not be exploited.

### Have I Been Pwned / Pwned Passwords

Use only through documented APIs and subscription terms. Prefer privacy-preserving Pwned Password lookup patterns and never upload a member's plaintext password.

### URLhaus

Potential malicious-URL intelligence adapter. Respect feed purpose, redistribution and terms.

### Spamhaus

Commercial usage may require a paid/licensed data service. Do not scrape or redistribute data outside terms.

### OpenPhish

Commercial/high-frequency feeds are licenced products. Treat provider state as configured/unconfigured and never copy the feed into public repository fixtures.

### Commercial reputation APIs

Google/Microsoft/VirusTotal and other reputation services can be useful, but their API terms, data retention, privacy and commercial quotas must be reviewed before inclusion. No provider should be treated as an unlimited free backend.

## Build-versus-buy recommendation

### Build as Aura Sec IP

- unified endpoint event schema;
- privacy-preserving device identity;
- behavioural correlation engine;
- response policy engine;
- ransomware containment/recovery orchestrator;
- Aura explanation and approval model;
- safe storage/performance planner;
- threat-intelligence fusion/risk model;
- multi-device security graph;
- incident timeline/evidence model;
- licence/device entitlement service;
- secure member control plane;
- cross-platform capability/truth model;
- rule testing and staged rollout service.

### Integrate reviewed permissive components where they improve security

- pattern matching;
- TLS;
- TUF-compatible update libraries;
- Sigstore tooling for CI/release transparency;
- platform-native secure storage/crypto;
- OS inventory libraries/tooling where justified;
- Linux eBPF support libraries.

### Keep replaceable adapters

- threat-intelligence feeds;
- breach monitoring;
- reputation services;
- cloud backup object provider;
- email/SMS/push notification provider;
- payment provider;
- external malware sandbox/research services.

No third party may become the single point whose outage silently turns the product from protected to unprotected. The client must expose degraded state and preserve local defences where possible.

## Repository/IP rule

The public web repository may hold interface contracts, public-safe architecture and non-secret integration code. It must never contain:

- production signing private keys;
- recovery/master encryption keys;
- commercial feed credentials;
- proprietary high-confidence detection heuristics if publication would materially aid bypass;
- private malware samples;
- customer incident data;
- anti-tamper secrets;
- unreleased exploit-specific bypass details;
- device enrolment secrets.

The native security engine and private detection content should live in a private security repository before privileged client implementation begins.
