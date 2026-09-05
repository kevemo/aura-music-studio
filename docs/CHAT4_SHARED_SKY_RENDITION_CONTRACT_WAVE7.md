# Chat 4 Shared Sky Wave 7 — Chat 2 Rendition Contract Normalization

## Problem closed by this wave

Chat 4's original `Chat2PlaybackAdapter` interpreted `session.rendition_profile` as a legacy flat mapping such as:

```json
{
  "landscape_720p": {"video_bitrate": "2500k"}
}
```

The merged Chat 2 first-party media runtime uses the canonical profile shape:

```json
{
  "renditions": ["720p", "480p"]
}
```

Without normalization, the old Chat 4 adapter could project that canonical list as one malformed rendition named `renditions` whose profile was the entire list. That is metadata corruption and can make a viewer quality surface advertise a fake quality entry.

## Wave 7 contract

`Chat2PlaybackAdapter` now normalizes both contracts:

- canonical `{"renditions": [...]}` expands one viewer metadata entry per list item, preserving Chat 2 order;
- JSON-serialized rendition profiles are normalized the same way;
- the older flat name-to-profile map remains backward compatible;
- malformed canonical `renditions` values that are not lists fail closed to an empty rendition list;
- empty/invalid list items are ignored;
- duplicate canonical names are deduplicated while preserving first occurrence.

The current Chat 2 runtime emits rendition names as strings. Wave 7 also accepts future dictionary items only when they provide an explicit identity (`name`, `id`, `label`, or a string `profile`).

## Media authority boundary

Rendition profile normalization is descriptive metadata only. Chat 4 does not construct or infer a rendition manifest URL.

For dictionary-form canonical entries, only whitelisted descriptive fields are retained. Fields such as `manifest_url`, `playback_url`, `url`, `authorization`, `token`, cookies or other authority-bearing material are not projected from the profile.

The actual first-party HLS bootstrap, browser authorization exchange, media origin and any future per-rendition playable URL remain Chat 2-owned. Watch v2 continues to offer an actual selectable quality only when a real browser-safe `manifest_url` has been supplied by the playback authority; metadata names alone never become fake playable qualities.

## Regression coverage

`tests/test_shared_sky_live_rendition_contract_wave7.py` verifies:

- the merged Chat 2 shape `{"renditions": ["720p", "480p"]}`;
- the same contract when stored as JSON text;
- nested descriptive metadata without URL/token leakage;
- unchanged legacy flat-map behavior;
- malformed canonical profile fail-closed behavior;
- duplicate and invalid entry suppression.

## Integration rule

Wave 6 is merged into `development/full-site-build` as `c792f1ae0bfd8823100d3587ac10ea3e91a4d448`. Wave 7 is now retargeted directly to that integration branch. This documentation-only reconciliation commit exists to trigger fresh pull-request validation against the post-Wave-6 combined tree; no prior stacked workflow result is accepted for admission.

Wave 7 must pass the exact-head Command Center CI, Security Gates and Self-Host Smoke admission matrix before merge. If the integration base moves again, reconcile and rerun the matrix.

Final deployment remains Chat 11-owned.
