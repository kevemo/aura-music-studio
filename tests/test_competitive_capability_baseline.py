from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse


BASELINE = Path(__file__).resolve().parents[1] / "docs" / "competitive_capability_baseline.json"
ALLOWED_STATUSES = {"pending_repository_audit", "gap_confirmed", "implemented_verified"}
OFFICIAL_SOURCE_HOSTS = {
    "www.capcut.com",
    "www.adobe.com",
    "help.bandlab.com",
    "suno.com",
    "www.canva.com",
    "www.hootsuite.com",
    "www.tiktok.com",
}
REQUIRED_CAPABILITY_IDS = {
    "video.multitrack-timeline",
    "video.transitions",
    "video.item-effects-filters",
    "video.whole-track-effects",
    "video.keyframe-animation",
    "video.masks",
    "video.auto-captions-transcription",
    "audio.multitrack-daw",
    "audio.stem-separation",
    "audio.pitch-correction",
    "audio.mastering",
    "music.prompt-to-song",
    "music.reference-audio-upload",
    "music.extend-cover-remix",
    "music.section-editor",
    "design.templates",
    "design.brand-kit",
    "social.cross-network-publishing",
    "social.calendar-scheduling",
    "social.approval-workflows",
    "social.unified-engagement-inbox",
    "social.listening-mentions-sentiment",
    "social.analytics-reporting",
    "live.scenes-sources-overlays",
    "live.chat-moderation",
    "live.analytics",
    "live.private-auto-cue-prompter",
    "platform.aura-cross-workflow-orchestration",
    "platform.separate-creative-entitlements-and-esp-roles",
    "platform.project-confined-asset-handoffs",
}


def _load() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_competitive_baseline_has_unique_required_capabilities() -> None:
    payload = _load()
    capabilities = payload["capabilities"]
    ids = [entry["id"] for entry in capabilities]

    assert len(ids) == len(set(ids))
    assert REQUIRED_CAPABILITY_IDS <= set(ids)
    assert all(entry.get("required") is True for entry in capabilities)
    assert all(entry.get("verification_status") in ALLOWED_STATUSES for entry in capabilities)


def test_verified_capabilities_require_implementation_and_test_evidence() -> None:
    for entry in _load()["capabilities"]:
        if entry["verification_status"] != "implemented_verified":
            continue
        assert entry.get("implementation_refs"), entry["id"]
        assert entry.get("test_refs"), entry["id"]


def test_competitor_sources_are_https_and_official_hosts() -> None:
    payload = _load()
    source_ids = set()
    for source in payload["sources"]:
        parsed = urlparse(source["url"])
        assert parsed.scheme == "https"
        assert parsed.hostname in OFFICIAL_SOURCE_HOSTS
        assert source["id"] not in source_ids
        source_ids.add(source["id"])

    for entry in payload["capabilities"]:
        assert set(entry.get("source_ids", [])) <= source_ids


def test_confirmed_gap_is_not_mislabeled_as_implemented() -> None:
    capabilities = {entry["id"]: entry for entry in _load()["capabilities"]}
    track_effects = capabilities["video.whole-track-effects"]

    assert track_effects["verification_status"] == "gap_confirmed"
    assert track_effects["implementation_refs"]
    assert track_effects["test_refs"] == []
