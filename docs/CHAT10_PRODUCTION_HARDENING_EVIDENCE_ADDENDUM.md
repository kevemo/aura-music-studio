# Chat 10 Production Hardening Evidence Addendum

Status: implementation/evidence addendum for Chat 10. This document is not production-release approval. Chat 11 remains the repository-wide release gate.

This addendum supersedes the outdated implementation-status wording in sections 11, 13 and 16 of `CHAT10_SLS_PRODUCTION_OPERATIONS_INTEGRATION_CONTRACT.md` where noted below. It does not weaken any external evidence requirement.

## 1. Aura Web DNS-rebinding / SSRF transport status

The previous contract said that DNS validation was not transport-bound. That statement is superseded for direct Aura Web HTTP(S) egress.

Direct Aura Web fetches now:

- resolve the requested hostname and reject any answer set containing private, loopback, link-local, reserved or otherwise forbidden addresses;
- pass the exact validated numeric address set to the transport layer;
- connect to those numeric addresses rather than re-resolving the target hostname in the HTTP client;
- preserve the original hostname for the HTTP `Host` header, TLS SNI and certificate hostname verification;
- re-run URL/DNS admission independently for every redirect target;
- ignore ambient `HTTP_PROXY`, `HTTPS_PROXY` and related process proxy variables for this boundary;
- reject embedded URL credentials before DNS resolution;
- retain bounded timeouts and response-size admission.

An explicitly configured `AURA_WEB_EGRESS_PROXY` is a separate deployment mode. It fails closed unless `AURA_WEB_TRUST_EGRESS_PROXY_DNS=true` explicitly delegates equivalent target-DNS / SSRF enforcement to that approved proxy. Enabling that switch is configuration, not evidence that the proxy is correctly enforcing policy. Production use of explicit proxy mode therefore still requires deployment evidence for the proxy's target-resolution and private-network blocking behavior.

This closes the prior direct-transport DNS TOCTOU implementation blocker. It does not constitute a penetration test or production-network proof.

Authoritative implementation/tests:

- `aura_music_studio/web_access.py`
- `aura_music_studio/web_transport.py`
- `tests/test_web_dns_pinning.py`

## 2. Trusted reverse-proxy / client attribution status

The previous contract described trusted client-address attribution as an unresolved implementation requirement. The code/configuration boundary is now implemented for the current direct-Caddy single-host topology.

Production behavior:

- Uvicorn starts with `--no-proxy-headers`; it does not rewrite ASGI client/scheme from untrusted forwarded headers before application security runs.
- Caddy overwrites an internal `X-ESP-Proxy-Auth` header with a deployment-supplied `LSS_TRUSTED_PROXY_TOKEN`.
- Production Compose requires the same token for Caddy and the application and refuses startup when it is absent.
- The application requires a non-placeholder token of at least 32 characters in production/staging before accepting forwarded identity.
- Token comparison is constant-time.
- An authenticated forwarded request must contain exactly one valid IP address in `X-Forwarded-For` and a scheme of `http` or `https`.
- Forwarded chains are rejected in this direct-Caddy topology rather than guessed through.
- After successful attribution, the application rewrites only its request-scope client/scheme and removes the internal proxy-authentication header before downstream routing.
- Missing/weak proxy configuration fails with 503; unauthenticated/spoofed forwarded identity fails with 403; malformed authenticated forwarding metadata fails with 400.
- The durable authentication limiter therefore keys on the raw ASGI peer or an authenticated Caddy-supplied client address, never on arbitrary raw `X-Forwarded-For` input.

Authoritative implementation/tests:

- `aura_music_studio/auth_security.py`
- `Dockerfile`
- `deploy/Caddyfile`
- `deploy/production/docker-compose.production.yml`
- `deploy/production/production.env.example`
- `tests/test_auth_security_production.py`
- `tests/test_trusted_proxy_deployment_contract.py`

Remaining evidence requirement: a real deployed production/staging stack must demonstrate that the proxy token is injected from the deployment secret store, that the application observes distinct real client addresses through Caddy, and that direct/untrusted paths cannot spoof or bypass attribution. Repository tests do not substitute for that deployment evidence.

## 3. Authentication rate limiting topology statement

Production/staging authentication abuse admission uses the shared SQLite database and one serialized sliding-window ledger across application workers that share that database. Client identifiers are hashed before persistence and limiter-store errors fail closed.

This remains a **single-host/shared-database** guarantee. It must not be represented as cross-host distributed rate limiting. Multi-host deployment requires a shared external limiter or equivalent trusted edge enforcement with failover/bypass evidence.

Generic background-job exception retries remain intentionally disabled unless a domain explicitly proves retry/idempotency safety. Stale lease recovery and operator-authorised dead-letter replay after explicit idempotency verification remain the safe recovery mechanisms.

## 4. Bounded load-smoke evidence

`aura_music_studio.load_probe` now provides a bounded HTTP admission smoke with:

- loopback-only default targeting;
- explicit opt-in for remote targets and HTTPS-only remote admission;
- hard request/concurrency/timeout limits;
- no embedded URL credentials or fragments;
- transport failures recorded as results rather than aborting the evidence run;
- success ratio, requests/second, elapsed time, status counts and transport-error distributions;
- min/median/p95/p99/max latency;
- explicit minimum-success and maximum-p95 thresholds;
- an evidence label stating that the result is a bounded smoke, not production soak/capacity proof.

Self-Host Smoke executes 120 loopback liveness requests at concurrency 10 with a 100% success requirement and a 1500 ms p95 ceiling, then retains `chat10-bounded-load-smoke` as an Actions artifact when produced.

Authoritative implementation/tests:

- `aura_music_studio/load_probe.py`
- `tests/test_load_probe.py`
- `.github/workflows/self-host-smoke.yml`

This is useful regression evidence only. Realistic workload mixes, sustained soak, media/render concurrency, database contention, queue saturation, failure injection, GPU/renderer capacity, network impairment, browser/device performance and production cost/capacity remain external release evidence.

## 5. Remaining external release evidence

The following remain unresolved and must not be inferred from passing repository tests:

- real production backup/snapshot restore with representative application-level validation;
- deployed provider/network verification and real alert delivery;
- deployment proof for trusted proxy secret injection and real client attribution;
- explicit-proxy target-DNS/SSRF enforcement evidence if explicit proxy mode is enabled;
- independently trusted native/publisher signing, key custody, platform notarisation and device-attestation evidence;
- independent penetration testing and applicable malware/phishing/ransomware/security benchmarking for SLS claims;
- cross-host rate-limit enforcement if the release topology becomes multi-host;
- browser/device compatibility and accessibility matrix evidence;
- realistic load, soak, fault-injection and capacity evidence;
- production rollback exercise and operational incident/monitoring evidence;
- repository branch/ruleset protection appropriate to the release process;
- Chat 11 final repository-wide release admission.

Passing CI, Security Gates and Self-Host Smoke proves only the exact repository candidate and checks that actually ran. It does not turn missing external evidence into a pass.
