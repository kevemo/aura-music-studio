from __future__ import annotations

import os

from . import __version__


def job_types_for_capabilities(capabilities: list[str]) -> list[str]:
    caps = {str(x).strip().lower() for x in capabilities if str(x).strip()}
    result: set[str] = set()
    if "music_generation" in caps or "all" in caps:
        result.update({"produce", "build_around"})
    if "engineering" in caps or "all" in caps:
        result.update({
            "engineering:split",
            "engineering:master",
            "engineering:autotune",
            "engineering:restore",
            "engineering:spatial",
        })
    if "stem_separation" in caps:
        result.add("engineering:split")
    if "mastering" in caps:
        result.add("engineering:master")
    if "autotune" in caps:
        result.add("engineering:autotune")
    if "restoration" in caps:
        result.add("engineering:restore")
    if "spatial_audio" in caps:
        result.add("engineering:spatial")
    return sorted(result)


def node_version(node: dict) -> str | None:
    software = node.get("software") or {}
    value = software.get("live_sound_studio_version") if isinstance(software, dict) else None
    return str(value).strip() if value else None


def compatibility(node: dict, coordinator_version: str | None = None) -> dict:
    coordinator = coordinator_version or __version__
    reported = node_version(node)
    require_same = os.getenv("LSS_NODE_REQUIRE_SAME_VERSION", "true").lower() == "true"
    compatible = bool(reported and reported == coordinator) if require_same else bool(reported)
    reason = None
    if not reported:
        reason = "Node has not reported a Live Sound Studio version"
    elif require_same and reported != coordinator:
        reason = f"Node version {reported} does not match coordinator version {coordinator}"
    return {
        "compatible": compatible,
        "coordinator_version": coordinator,
        "node_version": reported,
        "same_version_required": require_same,
        "reason": reason,
    }
