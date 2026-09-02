# ESP Social Provider Publishing

## Current implementation

The ESP Content Creation Command Center contains a fail-closed publishing pipeline for the provider surfaces implemented by this runtime. Planning capabilities can be broader than provider execution; a planned format never becomes automatically publishable merely because a connection record says it can.

Implemented automatic publishing surfaces are:

| Platform | Runtime adapter | Automatic publishing surface |
| --- | --- | --- |
| Facebook Pages | `facebook_pages_graph` | Page post |
| Instagram | `instagram_graph` | Post, reel |
| TikTok | `tiktok_content_posting` | Video |
| YouTube | `youtube_data_v3` | Video, short |

Other platform/content combinations remain planning-only until a bounded provider adapter actually implements them.

The authoritative capability resolver is `aura_music_studio.esp_social_publish_capabilities`. Content validation, queue transitions and the privileged provider worker all use the same runtime capability contract. The worker revalidates capability immediately before any provider call, which prevents stale or optimistic metadata from widening provider access.

## Connection models

TikTok, Instagram and YouTube use the private ESP member OAuth flow. Their provider applications, scopes and callbacks must be configured in the deployment before the Connections screen offers authorization.

Facebook Pages uses a separate bounded member OAuth flow because Page publishing requires explicit Page selection and Page-specific permission verification. Before redirecting to Meta, the member must supply the numeric Facebook Page ID they intend to authorize. The callback verifies the required Page-publishing permissions and resolves only that exact Page from the authorized account; it never silently selects a different Page when several are available.

The selected Page token is stored only in the existing encrypted, per-member Social OAuth vault. Social House JSON receives a `social-oauth://...` reference rather than the raw Page credential. The browser never receives or renders the raw Page access token.

Production activation requires a configured Meta Graph version (`AURA_FACEBOOK_GRAPH_VERSION` or `AURA_META_GRAPH_VERSION`), a Meta application ID/secret, the Social OAuth Fernet master key and the correct callback origin. The bounded deployment-variable contract is documented in:

`config/social/facebook-page-oauth.env.example`

Meta remains authoritative for Page permissions, application review and publication at provider-call time. The Facebook OAuth implementation does not grant personal-profile publishing, Reels, Stories, video publishing, inbox/DM or analytics authority.

Deployment-managed `social-token://` aliases remain a supported restricted secret-reference type for explicitly configured Social connections. They are not a substitute for member OAuth and must map only into the dedicated `AURA_SOCIAL_TOKEN_*` deployment-secret namespace.

## Worker boundary

The privileged publisher runs separately from Aura's ordinary durable task worker:

```text
aura-social-publish-worker
```

or:

```text
python -m aura_music_studio.esp_social_publish_worker
```

It remains disabled unless:

```text
AURA_SOCIAL_PUBLISH_WORKER_ENABLED=true
```

Docker Compose exposes the provider worker through the opt-in `social-publishing` profile. Normal Aura task execution cannot publish social content.

## Fail-closed requirements

A variant can move through provider publishing only when all applicable checks pass:

1. the member has ESP Social access;
2. the Social House connection is connected;
3. automatic publishing is enabled for that connection;
4. the credential reference uses an approved `social-token://` or encrypted `social-oauth://` path;
5. the configured adapter exists in the runtime and belongs to the requested platform;
6. the requested content type is implemented by that adapter;
7. the adapter is explicitly active;
8. approval requirements have been satisfied;
9. scheduled publication is due;
10. attached library media is rights-confirmed and approved;
11. provider-specific consent/privacy/media requirements pass;
12. deployment/provider credentials and configuration are available;
13. the provider confirms success.

`queued`, `publishing` and `published` are deliberately distinct states. Only provider-confirmed completion can produce `published`.

## Token security

Raw provider credentials must never be written into Social House JSON. A deployment-managed connection may store only a restricted alias such as:

```text
social-token://facebook-pages-legacy
```

which maps only to the social-token environment namespace, for example:

```text
AURA_SOCIAL_TOKEN_FACEBOOK_PAGES_LEGACY
```

OAuth-backed member connections use encrypted `social-oauth://` credential references managed by the dedicated OAuth vault. These reference types cannot be used to read unrelated owner/admin secrets, SMTP passwords or arbitrary connector keys.

For Facebook Page OAuth specifically, only the explicitly selected Page token is encrypted into the member vault after Page identity and permissions are verified. The temporary user token used during authorization is not persisted into Social House JSON.

## Media security

Publishable media references come through the ESP Social Media Library. The resolver verifies that the asset:

- belongs to the same Social House;
- is not archived;
- has `approval_state=approved`;
- has `rights_confirmed=true`; and
- resolves through a supported source type.

Provider-pulled URLs must use public HTTPS. Localhost/private-network targets are rejected. Creative Project files are accepted only when provenance resolves to a real Creative Element inside the tenant project directory.

## Queue and crash safety

A due variant is atomically claimed before an external provider call and moves from `queued` to `publishing`. Provider job identifiers are persisted as soon as they are received, and existing provider jobs are polled before new work is claimed.

If a worker crashes after a provider request may have begun but before a provider job ID is safely stored, the item enters an ambiguous-state review path rather than being blindly replayed. A single-worker lease prevents concurrent consumers from publishing the same deployment queue.

## Provider-specific boundaries

### Facebook Pages

`facebook_pages_graph` publishes bounded Page feed posts and a single approved image where the provider can access the media URL. The dedicated member OAuth flow requires explicit numeric Page selection and verifies `pages_show_list`, `pages_manage_posts` and `pages_read_engagement` before storing the selected Page token. Reels, Stories, personal-profile publishing, inbox and analytics are not exposed by this adapter/OAuth capability.

Facebook Page token expiry currently requires reconnect; the runtime does not claim an unimplemented automatic Page-token refresh lifecycle.

### Instagram

`instagram_graph` supports the implemented Professional-account post/reel path. Planning can include additional Instagram formats, but unsupported planned formats remain planning-only.

### TikTok

`tiktok_content_posting` implements video Direct Post with provider creator-info/privacy/consent checks and provider status polling. TikTok photo planning does not imply photo publishing.

### YouTube

`youtube_data_v3` uses OAuth-authorized video upload and provider processing checks for video/short surfaces. The queue is not marked published until the provider confirms processing success.

## Deployment truth

Code completion does not manufacture third-party authorization. Production provider calls still require the real provider applications, credentials, scopes, review/audit status and permissions required by Meta, TikTok, Google/YouTube or Instagram for the intended account and visibility.

The Connections UI reports those boundaries rather than presenting them as unfinished application placeholders: provider-authorized capabilities are enabled only when the runtime and deployment can truthfully support them.
