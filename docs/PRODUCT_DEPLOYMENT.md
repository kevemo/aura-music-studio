# ESP Live Sound Studio — Product Deployment

**Elevate Souls Productions Presents: The Live Sound Studio**  
**Music Making for Professionals · Powered by Aura**

The primary deployment target is now **ESP-controlled self-hosting**. The product does not require Cloudflare, another paid domain, a paid app host, Firebase/Supabase, or a commercial music-generation API in order for the application architecture to operate.

See [`SELF_HOSTING.md`](SELF_HOSTING.md) for the public-address/network guide.

## What runs in the ESP Studio stack

The production `app.py` and supporting private services provide:

- public ESP-branded landing/pricing/discovery pages;
- installable PWA metadata and tightly scoped public service worker;
- sign-up and sign-in;
- ESP membership approval lifecycle;
- member dashboard and private Studio workspaces;
- Free / Base / Pro entitlement enforcement;
- Base daily confirmed-track accounting;
- Pro unlimited tool access;
- ESP owner/admin portal;
- PayPal manual payment-link routing and verified billing periods;
- private per-member projects/assets;
- browser recording;
- real-audio production orchestration;
- build-around-upload workflows;
- generative DAW/session/take/comp/revision systems;
- mastering, splitter/stems, tuning, FX and engineering jobs;
- asynchronous production worker;
- Aura speech, reasoning and controlled web gateway;
- private SearXNG metasearch;
- Aura Public Address Manager;
- owner-controlled backup/migration system;
- health endpoint at `/health`.

## Public and private URLs

Public/indexable:

- `/` — public landing page
- `/pricing` — Free / Base / Pro comparison
- `/signup` — membership request
- `/signin` — member sign-in
- `/ai-music-studio`
- `/ai-song-generator`
- `/backing-track-maker`
- `/stem-splitter`
- `/ai-mastering`
- `/ai-vocal-studio`
- `/robots.txt`
- `/sitemap.xml`
- `/manifest.webmanifest`

Private/member or owner surfaces include:

- `/dashboard`
- `/studio`
- `/production-suite`
- `/recording-studio`
- `/take-manager`
- `/history`
- `/owner`
- `/owner/backups`
- `/projects/...`

Private/member routes are deliberately excluded from the public sitemap and public service-worker cache.

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

The current PayPal invoice links are used as a manual billing bridge; opening a payment URL is never treated as proof of payment.

## Tier rules

### Free

Entry-level creative access, Aura Producer/songwriting tools, starter controls and basic previews. No confirmed finished full-track allowance.

### Base — $4.99/month

One **confirmed full track per day**. The member may regenerate the current draft before confirmation. Base includes the core full-song/backing-track workflow, browser recording, Build Around Upload complete-mix mode, MP3/WAV finished downloads, standard Aura Tune/FX/AutoMix, useful mastering and reduced splitter modes.

### Pro — $9.99/month

Unlimited full-track production and the complete enabled Studio: editable multitrack Build Around, detailed splitter/stems, Take Manager, phrase comping, automation, advanced instrument variants, Aura FX Designer, trusted plugin rack, advanced/custom Aura Tune, reference/album mastering, Sample Lab, Style DNA, repaint/remix/edit tools, consent-approved voice features, spatial/tone/video engineering, priority jobs and the complete download/export set.

## Self-host-first network architecture

Default private stack:

```text
browser on ESP host
        │
        ▼
127.0.0.1:8000
        │
        ▼
ESP Live Sound Studio
 ├─ membership/database
 ├─ private projects
 ├─ Aura production worker
 ├─ local/open model workers
 └─ private SearXNG
```

Optional public profile:

```text
Internet
   │
   ▼
free hostname OR direct public IP
   │
   ▼
Caddy on ESP host (80/443)
   │
   ▼
private Live Sound Studio service
```

The FastAPI application itself binds to host loopback in the Docker configuration. Caddy is the public reverse-proxy/TLS boundary when the public profile is enabled.

## No paid domain requirement

Aura Public Address Manager supports:

- local-only mode;
- direct public-IP mode;
- FreeDNS/afraid.org free hostname;
- DuckDNS free hostname.

Aura monitors the address, can refresh the configured free-DDNS record, checks DNS readiness and warns about likely CGNAT. Full LAN/router/public network details are owner-only; ordinary member diagnostics receive only redacted readiness information.

A provider account/record still has to exist for a free-DDNS hostname. Aura maintains it after ESP supplies the private provider credential.

## HTTPS

For a free hostname that resolves to the ESP server:

```env
LSS_PUBLIC_BASE_URL=auto
LSS_PUBLIC_SITE_ADDRESS=your-free-host.example
LSS_COOKIE_SECURE=true
```

Caddy handles HTTPS on the ESP host once DNS and inbound ports 80/443 are correctly routed.

Direct-IP mode deliberately defaults to HTTP rather than falsely assuming a browser-trusted certificate can be issued for every arbitrary IP:

```env
LSS_DDNS_PROVIDER=direct
LSS_PUBLIC_SITE_ADDRESS=http://:80
LSS_COOKIE_SECURE=false
```

## Secure initialization

Before package installation:

```bash
python scripts/setup_self_host.py --provider direct
```

After installation:

```bash
aura self-host-init --provider direct
```

The initializer creates strong ESP owner/provenance secrets itself. Tokenized DDNS credentials are not accepted as CLI flags and must be placed privately in `.env` so they do not enter shell history.

## Public launch

```bash
docker compose --profile public up -d --build
```

Local-only/private launch:

```bash
docker compose up -d --build
```

Convenience launchers:

```bash
bash scripts/start_self_host.sh
```

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_self_host.ps1
```

## Public-address owner controls

The ESP owner dashboard reports:

- recommended public URL;
- configured hostname;
- LAN/router/public address diagnosis;
- DNS A/AAAA result;
- CGNAT warning;
- HTTPS readiness;
- DDNS refresh state.

It never displays the DDNS update token/URL.

## Membership approval email URL

Set:

```env
LSS_PUBLIC_BASE_URL=auto
```

and membership email links resolve from Aura Public Address Manager's current recommended URL. ESP does not need to manually rewrite the approval-link base every time an ISP address changes.

## Email approval delivery

The Studio supports SMTP directly. Gmail can be configured with:

```env
LSS_SMTP_HOST=smtp.gmail.com
LSS_SMTP_PORT=587
LSS_SMTP_USERNAME=elevatesoulsproductions@gmail.com
LSS_SMTP_PASSWORD=<deployment secret / app-specific credential>
LSS_SMTP_STARTTLS=true
```

If SMTP is not configured, the application writes a development-outbox message and explicitly reports that it was not delivered.

## Backups and machine migration

Aura's backup engine creates portable ZIP archives containing:

- a transactionally consistent SQLite backup of accounts/memberships/billing/jobs;
- private project files;
- project sessions/revisions/work files when enabled;
- finished outputs when enabled;
- per-file SHA-256 hashes and a backup manifest.

It deliberately excludes deployment `.env` and provider/payment/email/model secrets.

Create:

```bash
aura backup
```

Optional standard `age` encryption:

```bash
aura backup --age-recipient age1...
```

Verify:

```bash
aura backup-inspect backups/ESP_Live_Sound_Studio_....zip
```

Restore requires the Studio/web/worker to be stopped and explicit confirmation:

```bash
aura restore-backup backup.zip --offline-confirmed
```

Aura verifies every manifest checksum before replacement and, by default, preserves the old database/project tree beside the restored state. The owner portal also provides backup creation/list/download controls; restore remains CLI/offline-only intentionally.

Model checkpoints, Docker images and deployment secrets are not duplicated into every backup because they can be reinstalled/reconfigured separately and may be extremely large.

## Real-audio compute architecture

The customer/account web service is separate from heavy neural music generation. The Studio prefers a self-hosted ACE-Step worker and can route to additional local/open engines.

MIDI, MusicXML and notation remain control layers only; they cannot be promoted to Final Master.

GPU compute remains a physical resource. Avoiding paid generation APIs does not make unlimited generation computationally free; throughput is bounded by the GPU hardware ESP owns or otherwise has access to.

## Startup-cost objective

Assuming ESP already has suitable hardware, storage, electricity and Internet connectivity, the software is designed so the initial **additional domain/hosting/backend cost can be £0**.

External rails still exist where the real-world function requires them (for example ISP connectivity, DNS if a hostname is used, public certificate authorities, PayPal and Gmail), but they do not own the Studio database, projects or music-production code.
