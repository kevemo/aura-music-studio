# ESP Social Provider Publishing

## Status

Pulsar-Frequency House now contains the **trusted worker and provider-adapter foundation** needed to move approved ESP Social Management content from the internal production queue to official platform APIs.

This does **not** mean provider OAuth applications are already configured or that live publishing should be enabled by default.

The worker remains disabled unless all of the following are deliberately configured:

1. the provider application exists and has the required provider product/scopes;
2. provider review/audit requirements have been satisfied for the intended visibility/use case;
3. the ESP member has completed the provider's official OAuth/consent flow;
4. the deployment stores the member's provider token in the dedicated social-token secret namespace;
5. the SocialConnection points to an implemented adapter and is explicitly marked active;
6. the content has passed ESP approval gates where required;
7. every attached publish asset comes from the ESP Social Media Library and is rights-confirmed/approved;
8. the scheduled time is due;
9. provider-specific metadata/consent requirements are satisfied.

`queued`, `publishing` and `published` are intentionally distinct states. Only provider-confirmed completion may produce `published`.

## Worker boundary

The privileged process is:

```text
aura-social-publish-worker
```

or:

```text
python -m aura_music_studio.esp_social_publish_worker
```

It is separate from Aura's normal durable task worker. The normal task worker remains unable to publish social content.

Docker Compose exposes the provider worker only through the opt-in `social-publishing` profile. The service mounts:

- durable application data, including private Social House queue state;
- Creative Project storage **read-only**, so rights-approved local media can be uploaded without granting the worker permission to edit creative projects.

The worker is also disabled internally unless:

```text
AURA_SOCIAL_PUBLISH_WORKER_ENABLED=true
```

## Token security

Raw provider access tokens must never be written into Social House JSON.

A SocialConnection stores an alias such as:

```text
social-token://creator-one
```

The worker maps that alias only to:

```text
AURA_SOCIAL_TOKEN_CREATOR_ONE
```

This restricted namespace is deliberate. A member-controlled SocialConnection cannot point the worker at unrelated deployment secrets such as owner/admin credentials, SMTP passwords or general connector keys.

## Media security

The provider worker does not accept arbitrary filesystem paths or arbitrary media URLs from content metadata.

Each publishable media reference must be attached as:

```text
library:<media_asset_id>
```

The resolver then verifies that the ESP Social Media Library asset:

- exists inside the same tenant Social House;
- is not archived;
- has `approval_state=approved`;
- has `rights_confirmed=true`;
- resolves through a supported source type.

Provider-pulled URLs must use public HTTPS and direct localhost/private IP targets are rejected.

Creative Project files are accepted only when the library provenance resolves to a real Creative Element and its file path remains inside that tenant's project directory.

## Queue and crash safety

Before an external provider call begins, a due variant is claimed by the trusted worker and changes from `queued` to `publishing`.

Provider job identifiers are persisted as soon as an adapter receives them. The worker polls existing provider jobs before claiming new work.

If the worker crashes after a provider call may have begun but before a provider job ID is saved, the item is **not automatically replayed** after its lease expires. It is failed into an ambiguous-state review path instead. This is intentional duplicate-post protection.

A single-worker lease also prevents two accidental provider-worker processes from consuming the same deployment queue concurrently.

## Implemented provider adapters

### TikTok — `tiktok_content_posting`

Implementation follows the official TikTok Content Posting API Direct Post workflow:

1. Query Creator Info;
2. require explicit creator consent in the variant;
3. require a creator-selected privacy value that is allowed by current Creator Info;
4. initialize `/v2/post/publish/video/init/` using `video.publish`;
5. transfer the video using `PULL_FROM_URL` or `FILE_UPLOAD`;
6. persist `publish_id`;
7. poll `/v2/post/publish/status/fetch/`;
8. record `published` only after TikTok reports `PUBLISH_COMPLETE`.

Current worker release enables TikTok **video Direct Post**. The platform registry can plan photo content, but photo provider execution remains a separate adapter extension.

TikTok's official documentation states that unaudited Direct Post clients are restricted to private visibility. Production public posting therefore requires TikTok's applicable audit/approval.

Official references:

- https://developers.tiktok.com/docs/en/content-posting-api-get-started
- https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post
- https://developers.tiktok.com/docs/en/content-posting-api-reference-get-video-status

### Instagram — `instagram_graph`

This adapter implements the Meta **Instagram API with Facebook Login** publishing path for Instagram Professional accounts.

It currently supports:

- one approved image post; or
- one approved video/reel with a provider-accessible HTTPS URL.

The adapter creates the media container, polls its provider processing state and invokes media publishing only when the container is ready.

The Graph API version is deployment configuration rather than a permanent source-code constant:

```text
AURA_META_GRAPH_VERSION=
```

That prevents a future Meta version change from silently altering a deployment without validation.

Meta also provides an Instagram Login/Business Login API path with a different permission set. That path should be represented by its own adapter/configuration rather than silently mixing OAuth models.

Official Meta-maintained Postman references:

- https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api
- https://www.postman.com/meta/instagram/collection/6yqw8pt/instagram-api

### YouTube — `youtube_data_v3`

The YouTube adapter uses OAuth-authorized `videos.insert` resumable media upload and then polls `videos.list` processing details.

The expected OAuth scope includes:

```text
https://www.googleapis.com/auth/youtube.upload
```

This release requires an approved, materialized local video asset. Default privacy is `private` unless the variant explicitly selects another valid YouTube privacy state.

The queue is not marked `published` until YouTube reports successful video processing.

YouTube states that uploads from unverified API projects created after July 28, 2020 are restricted to private viewing until the API project passes the required audit.

Official references:

- https://developers.google.com/youtube/v3/docs/videos/insert
- https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
- https://developers.google.com/youtube/v3/docs/videos/list

## Connection metadata

A provider connection uses the existing SocialConnection object. Example shape (illustrative aliases only; never place a real token here):

```json
{
  "platform": "instagram",
  "account_external_id": "<professional-account-id>",
  "state": "connected",
  "supports_auto_publish": true,
  "token_secret_ref": "social-token://creator-one",
  "metadata": {
    "publishing_adapter": "instagram_graph",
    "publishing_adapter_active": true
  }
}
```

The API's existing connection-state endpoint records capability state only. A later OAuth portal must create/update these fields from verified provider callbacks rather than asking members to paste access tokens into the browser.

## Next integration stage

The next provider work should focus on:

1. first-party OAuth start/callback portals for TikTok, Meta/Instagram and Google/YouTube;
2. encrypted refresh-token lifecycle and access-token refresh where the provider supports it;
3. provider webhook verification/ingestion;
4. TikTok photo publishing;
5. Instagram carousel publishing and the Instagram Login/Business Login adapter path;
6. fully resumable/recoverable interrupted YouTube upload sessions;
7. public-media delivery for approved Creative Project outputs where a provider requires URL pull;
8. provider account/capability synchronization back into the Social Management UI;
9. analytics ingestion after confirmed publication;
10. unified social inbox adapters where provider permissions permit.

None of those should weaken the existing ESP-only membership, niche, affiliation/no-poaching, approval, rights/provenance or provider-confirmation gates.
