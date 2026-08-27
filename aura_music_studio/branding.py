"""Authoritative public branding for the Elevate Souls Productions platform.

Legacy package/module identifiers are intentionally retained for compatibility with
existing projects, databases, sessions and deployment configuration. Public product
copy should use the constants in this module.
"""

PRODUCT_NAME = "Elevate Souls Productions Content Creation Command Center"
PRODUCT_FULL_NAME = "Elevate Souls Productions Content Creation Command Center"
PRODUCT_SHORT_NAME = "Content Creation Command Center"
TAGLINE = "Elevate Your Soul Through Purposeful Media"
ENDORSEMENT = "Powered by Aura AI"
PLATFORM_DESCRIPTOR = "Music, Video, Image, Voice, Creator Intelligence, Commerce & Security"
AI_PRODUCER_NAME = "Aura"
AI_SYSTEM_NAME = "Aura AI"
COMPANY_NAME = "Elevate Souls Productions"

# The supplied purple/gold artwork is the canonical full brand visual across the site.
# The scalable SVG remains available as a compact icon/fallback, but is not the master artwork.
BRAND_LOGO_PATH = "/brand/elevate-souls-productions-content-creation-command-center.webp"
BRAND_ICON_PATH = "/static/elevate-souls-command-center-logo.svg"
BRAND_ARTWORK_FILENAME = "elevate-souls-productions-content-creation-command-center.webp"

# Historical public names remain valid only as compatibility aliases. They must not be
# presented as the current product identity in new UI or documentation.
LEGACY_PRODUCT_NAMES = (
    "Pulsar-Frequency House",
    "Pulsar-Frequency",
    "4Infinity Creative Studios",
    "Cosmic Creative Studios",
    "Cosmic Creation Studios",
    "The Live Sound Studio",
    "Live Sound Studio",
)


def product_header() -> str:
    return f"{PRODUCT_FULL_NAME} — {ENDORSEMENT}"
