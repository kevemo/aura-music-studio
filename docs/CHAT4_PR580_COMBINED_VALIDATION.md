# Chat 4 PR #580 Combined Validation Marker

PR #580 was retargeted to the authoritative integration branch after Chat 2 transport merged as `1a6976a32a0deb832aca2ef983b899811ee1f92b`.

This commit intentionally advances the PR head after retargeting so pull-request workflows validate the current combined tree containing:

- merged Chat 2 transport/control-plane code;
- Chat 4 playback and browser-authorization adapters;
- Chat 4 display-only Chat 5 Gift seam;
- Chat 4 upcoming LIVE publication, discovery and reminder domain;
- Chat 4 Upcoming LIVE viewer pages and associated tests.

No production deployment is authorized by this marker. Merge requires the resulting exact-head CI, Security Gates and Self-Host Smoke to pass; final production acceptance remains Chat 11-owned.
