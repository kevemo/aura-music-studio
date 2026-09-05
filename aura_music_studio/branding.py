"""Authoritative public branding for the Elevate Souls Productions platform.

Legacy package/module identifiers are intentionally retained for compatibility with
existing projects, databases, sessions and deployment configuration. Public product
copy should use the constants in this module.
"""

PRODUCT_NAME = "Elevate Souls Productions Content Creation Command Center"
PRODUCT_FULL_NAME = "Elevate Souls Productions Content Creation Command Center"
PRODUCT_SHORT_NAME = "Content Creation Command Center"
TAGLINE = "Elevate Your Soul Through Purposeful Media"
ENDORSEMENT = "Powered by Rhiannon Intelligence Systems"
PLATFORM_DESCRIPTOR = "Music, Video, Image, Voice, Creator Intelligence, Commerce & Security"
AI_PRODUCER_NAME = "Rhian"
AI_SYSTEM_NAME = "Rhiannon Intelligence Systems"
COMPANY_NAME = "Elevate Souls Productions"
BRAND_LOGO_PATH = "/static/elevate-souls-command-center-logo.svg"
BRAND_MARK_ROUTE = "/brand/command-center-mark.svg"
BRAND_ART_ROUTE = "/brand/command-center-art.webp"
BRAND_THEME_ROUTE = "/brand/theme.css"
BRAND_FAVICON_ROUTE = "/favicon.webp"

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

# The assistant identity changed publicly on 5 September 2026. Internal module, route,
# database, cookie, environment-variable and provider identifiers may retain Aura-based
# names until a tested compatibility migration is available.
LEGACY_AI_NAMES = (
    "Aura",
    "Aura AI",
    "Aura AI Systems",
)


def product_header() -> str:
    return f"{PRODUCT_FULL_NAME} — {ENDORSEMENT}"
