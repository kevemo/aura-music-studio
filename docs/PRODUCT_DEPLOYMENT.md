# The Live Sound Studio — Product Deployment

**Elevate Souls Productions Presents: The Live Sound Studio**  
**Music Making for Professionals**  
Internal AI producer: **Aura**

## What runs in the main Studio service

The production `app.py` serves one FastAPI application containing:

- public landing page and pricing;
- sign-up and sign-in;
- ESP membership approval lifecycle;
- member dashboard;
- Free / Base / Pro entitlement enforcement;
- Base daily confirmed-track accounting;
- Pro unlimited tool access;
- ESP owner/admin portal;
- PayPal manual payment-link routing;
- verified monthly billing-period ledger;
- project and asset APIs;
- real-audio production orchestration;
- mastering, splitter/stems, DAW/session and export APIs;
- health endpoint at `/health`.

## Product URLs

- `/` — public landing page
- `/pricing` — Free / Base / Pro comparison
- `/signup` — membership request
- `/signin` — member sign-in
- `/dashboard` — member plan/status/tools
- `/owner` — ESP owner administration
- `/docs` — API documentation

## Membership lifecycle

1. Applicant chooses Free, Base ($4.99) or Pro ($9.99).
2. Account is created as `pending_approval`.
3. Approval request is emailed to `elevatesoulsproductions@gmail.com`.
4. Kev or Mary approves/rejects through the secure approval link.
5. Free becomes active immediately after approval.
6. Base/Pro become `approved_pending_payment` and receive the configured PayPal link.
7. ESP verifies the PayPal payment in the owner dashboard.
8. Verification creates a 31-day paid membership period.
9. Additional verified payments extend the current paid-through date.
10. If a paid period expires, access automatically returns to payment-pending until renewal is verified.

The current PayPal invoice/payment links are therefore safe to use as a manual billing bridge without pretending they are recurring subscription webhooks.

## Tier rules

### Free

Basic song/project creation, basic lyrics, Aura Producer planning and basic previews. No confirmed finished full-track allowance.

### Base — $4.99/month

One **confirmed full track per day**. The member may regenerate the same draft track repeatedly before confirmation. Confirmation consumes the day's allowance. Base includes MP3/WAV finished downloads, basic mastering, uploads, backing-track creation and harmony tools.

### Pro — $9.99/month

Unlimited confirmed tracks and the complete enabled studio: splitter/stems, multitrack DAW, take lanes, automation, advanced/reference mastering, Sample Lab, Style DNA, covers/remixes/repaint, Harmony Architect, consent-approved voice duplication, audio-to-MIDI control analysis, BandLab/stem exports and all download formats.

## Security secrets

Never commit real values. Configure these in the deployment host's secret manager:

- `LSS_ADMIN_KEY` — long random ESP owner-dashboard key
- `LSS_SMTP_USERNAME`
- `LSS_SMTP_PASSWORD`
- `LSS_SMTP_FROM`
- any model/worker authentication tokens if used

For production HTTPS:

```env
LSS_PUBLIC_BASE_URL=https://your-live-domain.example
LSS_COOKIE_SECURE=true
```

For local HTTP testing:

```env
LSS_PUBLIC_BASE_URL=http://127.0.0.1:8000
LSS_COOKIE_SECURE=false
```

## Email approval delivery

The Studio supports SMTP directly. Gmail can be configured with:

```env
LSS_SMTP_HOST=smtp.gmail.com
LSS_SMTP_PORT=587
LSS_SMTP_USERNAME=elevatesoulsproductions@gmail.com
LSS_SMTP_PASSWORD=<deployment secret / app password>
LSS_SMTP_STARTTLS=true
```

If SMTP is not configured, the code writes the message to a development outbox and explicitly reports that it was **not sent**.

## Local production-style launch

```bash
cp .env.example .env
# fill secrets

docker compose up --build
```

Then open:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/owner`

Persistent named volumes hold account/billing data and projects.

## Real-audio compute architecture

The customer/account web service is intentionally separate from the heavy neural music worker.

The Studio orchestrator can call a self-hosted ACE-Step worker and compatible open engines. MIDI, MusicXML and notation remain control layers only; they cannot be promoted to Final Master.

For a fully open/self-hosted installation, run the model worker on GPU compute and point the Studio at it using `AURA_ACESTEP_API_URL`. Commercial provider adapters remain optional fallbacks and are not required by the product architecture.

## Free-host deployment note

Do not choose a host solely because it offers free CPU web hosting. A public membership product also requires **persistent account/billing storage**, HTTPS and secret management. The neural music layer additionally needs GPU compute. The repository is now host-independent so these can be selected separately without rewriting the product.
