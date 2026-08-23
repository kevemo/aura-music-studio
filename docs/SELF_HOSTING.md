# ESP Live Sound Studio — Self-Hosted Public Deployment

**Elevate Souls Productions Presents: The Live Sound Studio**  
**Powered by Aura · Music Making for Professionals**

This deployment path is intentionally **Cloudflare-free** and does not require ESP to buy another domain or pay a web-hosting subscription at startup.

## Design principle

The Studio owns and operates its application stack:

- customer website and membership portal;
- Free / Base / Pro entitlement system;
- ESP owner approval and billing ledger;
- SQLite account/job database;
- private per-member projects;
- local/open AI model workers;
- Aura search/reasoning/speech modules;
- recording, DAW, splitting, mixing and mastering;
- background production workers;
- public-address monitoring.

A free DDNS provider, when used, supplies **only a DNS hostname**. It does not host ESP member data or music.

## Public-address modes

### 1. Free hostname — recommended

Aura supports replaceable adapters for:

- **FreeDNS / afraid.org** using its official tokenized Direct URL;
- **DuckDNS** using a subdomain + token.

The initial free hostname/account must be created with the chosen provider. After that, Aura keeps its DNS record updated automatically. No paid domain is required.

Example configuration for FreeDNS:

```env
LSS_DDNS_PROVIDER=freedns
LSS_PUBLIC_HOST=your-free-host.example
LSS_FREEDNS_UPDATE_URL=<official HTTPS Direct URL stored only in .env>
LSS_PUBLIC_SITE_ADDRESS=your-free-host.example
LSS_PUBLIC_BASE_URL=https://your-free-host.example
LSS_COOKIE_SECURE=true
```

Example configuration for DuckDNS:

```env
LSS_DDNS_PROVIDER=duckdns
LSS_DUCKDNS_SUBDOMAIN=esp-live-sound-studio
LSS_DUCKDNS_TOKEN=<deployment secret>
LSS_PUBLIC_HOST=esp-live-sound-studio.duckdns.org
LSS_PUBLIC_SITE_ADDRESS=esp-live-sound-studio.duckdns.org
LSS_PUBLIC_BASE_URL=https://esp-live-sound-studio.duckdns.org
LSS_COOKIE_SECURE=true
```

Never commit DDNS tokens or the tokenized FreeDNS update URL.

### 2. Direct public IP — maximum DNS independence

```env
LSS_DDNS_PROVIDER=direct
LSS_PUBLIC_SITE_ADDRESS=http://:80
LSS_COOKIE_SECURE=false
```

Aura reports a direct-IP URL when a global IPv4 address is detected. Direct-IP mode is HTTP by default because the Studio does not assume a globally trusted certificate exists for an arbitrary address.

### 3. Local-only mode

Leave DDNS disabled and do not start the public Caddy profile. The Studio remains available to the owner locally at:

```text
http://127.0.0.1:8000
```

This is also useful when the Internet is unavailable: installed local music/audio engines can continue to work without public access.

## Start the private Studio stack

```bash
cp .env.example .env
# configure ESP owner/admin/email/payment/model settings

docker compose up -d --build
```

The main FastAPI service binds only to `127.0.0.1:8000` on the host. SearXNG remains internal to the Compose network.

## Start public access

After configuring a hostname or direct-IP mode:

```bash
docker compose --profile public up -d --build
```

The public profile starts Caddy on TCP 80/443 and UDP 443. Caddy reverse-proxies to the private Studio service. With a real public hostname and correctly routed ports, Caddy manages HTTPS certificates automatically.

## Router / firewall requirement

A self-hosted public server must be reachable from the Internet. Normally this means forwarding:

- TCP 80 → Studio host
- TCP 443 → Studio host
- UDP 443 → Studio host (optional HTTP/3)

Aura's automatic UPnP port-forward setting is deliberately **disabled by default**:

```env
LSS_UPNP_PORT_FORWARD=false
```

If ESP deliberately enables it and installs `miniupnpc`, Aura may request TCP 80/443 mappings from a compatible router. Manual router configuration remains the safer default.

## CGNAT

Some ISPs place customers behind Carrier-Grade NAT. In that situation the router's WAN-facing address may itself be private or in `100.64.0.0/10`, which can prevent unsolicited IPv4 connections from reaching the Studio.

Aura's owner dashboard compares router-facing/public address information and raises a CGNAT warning when it sees a strong signal. If CGNAT exists, the independent options are:

1. ask the ISP for a routable public IPv4 address;
2. use a globally routable IPv6 address if the ISP/router/firewall support it;
3. only if necessary, add a replaceable relay/tunnel later.

A relay is **not** a mandatory part of the Live Sound Studio architecture.

## Aura Public Address Manager

The `aura-address-manager` Compose service runs continuously and writes only redacted status to the shared data volume. The ESP owner dashboard displays:

- DDNS provider name;
- configured hostname;
- LAN IPv4;
- router-facing IPv4 when UPnP discovery works;
- detected global IPv4;
- global IPv6 candidates;
- DNS A/AAAA resolution;
- likely-CGNAT warning;
- recommended public URL;
- hostname/HTTPS readiness.

It never writes the DuckDNS token or FreeDNS Direct URL into its status file.

## Public-IP discovery independence

Aura first tries to read the router-facing address locally using UPnP when available. If that cannot provide a global address, the default configuration can ask free HTTPS reflector endpoints what source IPv4 they see.

Those endpoints are replaceable:

```env
LSS_PUBLIC_IPV4_DISCOVERY_URLS=https://your-own-ip-reflector.example/ip
```

ESP can therefore host its own tiny reflector later and remove the default public reflectors without changing the Studio architecture.

## What still exists outside the ESP server

Self-hosting removes the need for a paid app host, but public Internet operation still inherently uses:

- the ISP providing Internet connectivity;
- DNS if a hostname is used;
- public certificate authorities for browser-trusted HTTPS;
- PayPal for ESP subscription payments;
- Gmail/SMTP while ESP uses Gmail for membership approval mail.

None of those services owns the Studio's project database, music-generation code or member project storage.

## Startup-cost objective

The intended initial infrastructure bill is **£0 additional hosting/domain cost**, assuming ESP already has suitable always-on hardware, Internet connectivity, storage and enough compute for the selected local AI models.

GPU capacity is a physical resource. The software remains self-hostable, but truly unlimited generation is limited by whatever GPU hardware ESP actually has available.
