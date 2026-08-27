"""Authoritative public branding for the ESP Content Creation Command Center.

Legacy package/module identifiers are intentionally retained for compatibility with
existing projects, databases, sessions and deployment configuration. Public product
copy should use the constants in this module.
"""

PRODUCT_NAME = "Elevate Souls Productions Content Creation Command Center"
PRODUCT_FULL_NAME = "Elevate Souls Productions Content Creation Command Center"
PRODUCT_SHORT_NAME = "ESP Content Creation Command Center"
TAGLINE = "Elevate Your Soul Through Purposeful Media"
ENDORSEMENT = "Powered by Aura AI"
PLATFORM_DESCRIPTOR = "Music, Video, Image, Games, Social & Creator Intelligence"
AI_PRODUCER_NAME = "Aura"
AI_SYSTEM_NAME = "Aura AI"
COMPANY_NAME = "Elevate Souls Productions"
BRAND_ASSET_PATH = "/static/esp-content-creation-command-center-brand.webp"
LEGACY_PRODUCT_NAMES = (
    "Pulsar-Frequency House",
    "Pulsar-Frequency",
    "Cosmic Creative Studios",
    "Cosmic Creation Studios",
    "4Infinity Creative Studios",
    "The Live Sound Studio",
    "Live Sound Studio",
)


def product_header() -> str:
    return f"{PRODUCT_FULL_NAME} — {TAGLINE} — {ENDORSEMENT}"
