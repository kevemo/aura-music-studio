# Mary & Kev Owner MFA

The Elevate Souls Productions Content Creation Command Center supports a second authentication factor for privileged Mary/Kev owner administration.

This layer is deliberately separate from normal member authentication, ESP Creator/Agent roles, subscription state and Pulsar Credits. Paying for a plan or receiving an ESP role never grants owner access.

## Security model

Owner authentication uses two factors when `LSS_OWNER_MFA_REQUIRED=true`:

1. `LSS_ADMIN_KEY` — deployment owner administration key (first factor).
2. A separate six-digit TOTP authenticator code for the selected owner (second factor).

Mary and Kev have independent TOTP seeds. The seeds are read only from deployment environment/secrets configuration and are never written to SQLite, rendered in HTML, returned by an API, logged by the owner MFA subsystem or committed to this repository.

A correct administration key does **not** create an owner session while MFA is required. It creates only a random five-minute MFA challenge. Only the SHA-256 hash of that challenge is stored server-side. The browser challenge cookie is HttpOnly, SameSite Strict, scoped to `/owner`, and Secure when secure cookies are enabled.

Successful TOTP verification creates the existing opaque server-side owner session and binds the verified Mary/Kev persona. Switching from Mary to Kev (or Kev to Mary) also requires the target owner's current TOTP while MFA is enabled. The current owner identity remains unchanged until verification succeeds.

Accepted TOTP counters are replay protected per owner. A used code from the same 30-second counter cannot establish another challenge. Challenges expire after five minutes and are exhausted after five incorrect attempts.

## Required deployment secrets

Configure these values in the production secret manager/environment, never in source control:

- `LSS_ADMIN_KEY`
- `LSS_OWNER_MARY_TOTP_SECRET`
- `LSS_OWNER_KEV_TOTP_SECRET`
- `LSS_OWNER_MFA_REQUIRED=true`

Each TOTP secret must be Base32 and decode to at least 160 bits of random key material.

Do not reuse the same TOTP seed for Mary and Kev. Do not derive either TOTP seed from `LSS_ADMIN_KEY`, an email address, a password, a memorable phrase or another application secret.

## Safe enrolment order

1. Generate two cryptographically random TOTP seeds on a trusted administrative machine or approved password/authenticator management system, outside the website.
2. Enrol Mary's seed in Mary's authenticator application and verify that the generated codes match the deployment-side test environment.
3. Enrol Kev's separate seed in Kev's authenticator application and verify it independently.
4. Store both Base32 seeds in the production secret manager under the environment names above.
5. Keep `LSS_OWNER_MFA_REQUIRED` disabled until **both** owner secrets have been configured and tested.
6. Enable `LSS_OWNER_MFA_REQUIRED=true`.
7. Verify a complete Mary login, Kev login, Mary→Kev switch and Kev→Mary switch before declaring the production owner console ready.

When MFA is required but either owner seed is absent or invalid, owner MFA fails closed rather than silently falling back to one-factor access.

## Rotation and recovery

There is intentionally no web page that reveals, resets or bypasses an owner TOTP seed. A compromised/lost owner authenticator must be recovered by an authorised deployment operator through the secret manager:

1. Put owner administration into a controlled maintenance procedure if necessary.
2. Generate a new random seed for the affected owner out-of-band.
3. Replace only that owner's deployment secret.
4. Enrol and verify the new seed directly with that owner.
5. Re-test owner login and owner switching before normal operation resumes.

If the first factor is suspected compromised, rotate `LSS_ADMIN_KEY` as well and invalidate/revoke existing owner sessions through the operational response process.

Do not add a weaker email/SMS/web recovery bypass merely for convenience. Any future recovery mechanism must preserve or improve the privileged-access assurance level.

## Operational requirements

Production should also enforce:

- HTTPS and Secure cookies.
- Strong, unique `LSS_ADMIN_KEY` stored only in the deployment secret manager.
- Separate Mary/Kev authenticator ownership.
- Restricted access to deployment secrets and database backups.
- Monitoring/audit review for privileged changes.
- Secret rotation after suspected exposure.

The repository contains no production TOTP seeds.

## Future phishing-resistant upgrade

TOTP materially improves the current administration-key-only boundary, but WebAuthn/passkeys are a stronger phishing-resistant option. The recommended future upgrade is to add registered hardware/platform passkeys for Mary and Kev while retaining a controlled migration/recovery process. That work should not weaken or remove the current fail-closed owner boundary until it is fully validated.
