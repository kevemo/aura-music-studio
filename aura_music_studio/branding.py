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
BRAND_LOGO_PATH = "/static/elevate-souls-command-center-logo.svg"

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
