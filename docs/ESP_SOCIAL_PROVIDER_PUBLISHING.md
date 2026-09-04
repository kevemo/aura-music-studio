# ESP Social Provider Publishing

## Current implementation

The ESP Content Creation Command Center contains a fail-closed publishing pipeline for the provider surfaces implemented by this runtime. Planning capabilities can be broader than provider execution; a planned format never becomes automatically publishable merely because a connection record says it can.

Implemented automatic publishing surfaces are:

| Platform | Runtime adapter | Automatic publishing surface |
| --- | --- | --- |
| Facebook Pages | `facebook_pages_graph` | Page post |
| Instagram | `instagram_graph` | Post, reel |
| Threads | `threads_graph` | Single post: text, one image, or one video |
| TikTok | `tiktok_content_posting` | Video |
| YouTube | `youtube_data_v3` | Video, short |

Other platform/content combinations remain planning-only until a bounded provider adapter actually implements them.

The authoritative capability resolver is `aura_music_studio.esp_social_publish_capabilities`. Content validation, queue transitions and the privileged provider worker all use the same runtime capability contract. The worker revalidates capability immediately before any provider call, which prevents stale or optimistic metadata from widening provider access.

## Connection models

TikTok, Instagram, YouTube and Threads use private ESP member OAuth flows. Their provider applications, scopes and callbacks must be configured in the deployment before the corresponding authorization flow can become ready.

Threads has a dedicated bounded OAuth implementation rather than widening the older generic TikTok/Instagram/YouTube service. It requests only `threads_basic` and `threads_content_publish`, exchanges the authorization code for a short-lived token, upgrades that credential to a long-lived Threads user token, verifies the token and granted scopes through Meta before activating publishing, verifies the app-scoped Threads profile, and stores the long-lived credential only in the existing encrypted per-member Social OAuth vault. Social House JSON receives only a `social-oauth://...` reference.

Threads long-lived tokens are refreshed through Meta's `th_refresh_token` flow only when they are near expiry and still unexpired. An expired long-lived token fails closed and requires the member to reconnect. The Threads extension delegates every non-Threads credential to the existing provider-specific vault behavior.

Threads deployment variables are documented in:

`config/social/threads-oauth.env.example`

Facebook Pages uses a separate bounded member OAuth/deployment flow because Page publishing requires explicit Page selection and Page-specific permission verification. Before redirecting to Meta, the member must supply the numeric Facebook Page ID they intend to authorize. The callback verifies the required Page-publishing permissions and resolves only that exact Page from the authorized account; it never silently selects a different Page when several are available.

The selected Page token is stored only in the existing encrypted, per-member Social OAuth vault. Social House JSON receives a `social-oauth://...` reference rather than the raw Page credential. The browser never receives or renders the raw Page access token.

Production activation requires the relevant provider app configuration, the Social OAuth Fernet master key and the correct public callback origin. Facebook's bounded deployment-variable contract is documented in:

`config/social/facebook-page-oauth.env.example`

Meta remains authoritative for Page/Threads permissions, application review and publication at provider-call time. The Facebook OAuth implementation does not grant personal-profile publishing, Reels, Stories, video publishing, inbox/DM or analytics authority, and the Threads flow does not request replies or insights permissions.

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

For Threads, the short-lived authorization token is exchanged before persistence. The long-lived token is encrypted with the deployment Fernet key, tied to the member credential row, and never written to Social House JSON or rendered by the callback page. The worker receives only the decrypted access token inside the tenant-bound privileged publishing context.

For Facebook Page OAuth specifically, only the explicitly selected Page token is encrypted into the member vault after Page identity and permissions are verified. The temporary user token used during authorization is not persisted into Social House JSON.

## Media security

Publishable media references come through the ESP Social Media Library. The resolver verifies that the asset:

- belongs to the same Social House;
- is not archived;
- has `approval_state=approved`;
- has `rights_confirmed=true`; and
- resolves through a supported source type.

Provider-pulled URLs must use public HTTPS. Localhost/private-network targets are rejected. Creative Project files are accepted only when provenance resolves to a real Creative Element inside the tenant project directory.

Threads image/video publishing deliberately accepts only a provider-accessible public HTTPS URL. A server-local creative file is not silently uploaded or exposed by the Threads adapter; it remains blocked until a separately reviewed provider-safe materialization/publication path exists.

## Queue and crash safety

A due variant is atomically claimed before an external provider call and moves from `queued` to `publishing`. Provider job identifiers are persisted as soon as they are received, and existing provider jobs are polled before new work is claimed.

If a worker crashes after a provider request may have begun but before a provider job ID is safely stored, the item enters an ambiguous-state review path rather than being blindly replayed. A single-worker lease prevents concurrent consumers from publishing the same deployment queue.

Threads follows the same crash-safety rule. Container creation returns a stored provider job ID before publication. Polling checks the container state first; `IN_PROGRESS` stays pending, `ERROR`/`EXPIRED` fail closed, `FINISHED` is published through the provider endpoint, and an already-`PUBLISHED` container is treated as provider-confirmed rather than blindly published a second time.

## Provider-specific boundaries

### Facebook Pages

`facebook_pages_graph` publishes bounded Page feed posts and a single approved image where the provider can access the media URL. The dedicated member OAuth flow requires explicit numeric Page selection and verifies `pages_show_list`, `pages_manage_posts` and `pages_read_engagement` before storing the selected Page token. Reels, Stories, personal-profile publishing, inbox and analytics are not exposed by this adapter/OAuth capability.

Facebook Page token expiry currently requires reconnect; the runtime does not claim an unimplemented automatic Page-token refresh lifecycle.

### Instagram

`instagram_graph` supports the implemented Professional-account post/reel path. Planning can include additional Instagram formats, but unsupported planned formats remain planning-only.

### Threads

`threads_graph` implements Meta's bounded two-stage single-post flow: create a media container, check provider processing state, then publish the finished container. This release supports text-only posts or exactly one approved image/video. Image/video media must already have a public HTTPS URL that passes the ESP Social Media Library resolver.

The dedicated member OAuth flow requests exactly `threads_basic` and `threads_content_publish`. Before Social Manager marks the connection authorised, the server exchanges the code, obtains a long-lived token, obtains an app token for server-side token inspection, verifies that the user token is valid and contains both required permissions, and verifies the Threads profile identity. Long-lived tokens are refreshed through `th_refresh_token` near expiry while still valid.

Carousel publishing, replies, quote posts, locations, topic tags, analytics and inbox behavior are not claimed by this adapter. The OAuth flow does not request the corresponding permissions. Meta application setup, redirect URI registration, app review and provider availability remain authoritative production requirements.

### TikTok

`tiktok_content_posting` implements video Direct Post with provider creator-info/privacy/consent checks and provider status polling. TikTok photo planning does not imply photo publishing.

### YouTube

`youtube_data_v3` uses OAuth-authorized video upload and provider processing checks for video/short surfaces. The queue is not marked published until the provider confirms processing success.

## Deployment truth

Code completion does not manufacture third-party authorization. Production provider calls still require the real provider applications, credentials, scopes, review/audit status and permissions required by Meta, TikTok, Google/YouTube, Instagram or Threads for the intended account and visibility.

The Connections UI should report those boundaries rather than present them as available when deployment/provider authority is missing: provider-authorized capabilities are enabled only when the runtime, encrypted credential layer and deployment can truthfully support them.
