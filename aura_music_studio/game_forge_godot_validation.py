from __future__ import annotations

import re

# Pinned from the official godotengine/godot 4.7.2-stable GitHub release. CI verifies the
# downloaded Linux editor ZIP against this immutable digest before executing it.
GODOT_HEADLESS_VERSION = "4.7.2-stable"
GODOT_HEADLESS_LINUX_X86_64_FILENAME = "Godot_v4.7.2-stable_linux.x86_64.zip"
GODOT_HEADLESS_LINUX_X86_64_BINARY = "Godot_v4.7.2-stable_linux.x86_64"
GODOT_HEADLESS_LINUX_X86_64_SHA256 = "cadd3204e728a35d3f13adb7fd0d7902636b79f6b95c40c265eb73b6c35329e4"
GODOT_HEADLESS_LINUX_X86_64_URL = (
    "https://github.com/godotengine/godot/releases/download/"
    f"{GODOT_HEADLESS_VERSION}/{GODOT_HEADLESS_LINUX_X86_64_FILENAME}"
)


def godot_headless_validation_contract() -> dict:
    """Describe the narrow CI guarantee without overstating runtime parity.

    This gate proves that representative deterministic Aura-generated Godot 4 source projects
    parse and boot under the pinned official engine. It is not a gameplay/physics/cinematics parity
    claim and therefore does not promote the external adapter to production-equivalent status.
    """
    return {
        "configured": True,
        "godot_version": GODOT_HEADLESS_VERSION,
        "platform": "linux.x86_64",
        "binary_filename": GODOT_HEADLESS_LINUX_X86_64_FILENAME,
        "binary_sha256": GODOT_HEADLESS_LINUX_X86_64_SHA256,
        "official_release_asset_pinned": True,
        "validates_generated_2d_project": True,
        "validates_generated_3d_project": True,
        "headless_parse_and_boot": True,
        "runtime_parity_claimed": False,
        "production_ready": False,
        "scope": (
            "Pinned-engine structural/runtime boot validation for deterministic generated source "
            "projects; Aura gameplay/physics/Adventure/cinematics/audio/State Machine parity is "
            "outside this gate."
        ),
    }


def validate_godot_pin() -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", GODOT_HEADLESS_LINUX_X86_64_SHA256):
        raise ValueError("Pinned Godot Linux SHA-256 is malformed")
    if not GODOT_HEADLESS_LINUX_X86_64_URL.startswith(
        f"https://github.com/godotengine/godot/releases/download/{GODOT_HEADLESS_VERSION}/"
    ):
        raise ValueError("Pinned Godot validation binary must come from the official release path")


__all__ = [
    "GODOT_HEADLESS_VERSION",
    "GODOT_HEADLESS_LINUX_X86_64_FILENAME",
    "GODOT_HEADLESS_LINUX_X86_64_BINARY",
    "GODOT_HEADLESS_LINUX_X86_64_SHA256",
    "GODOT_HEADLESS_LINUX_X86_64_URL",
    "godot_headless_validation_contract",
    "validate_godot_pin",
]
