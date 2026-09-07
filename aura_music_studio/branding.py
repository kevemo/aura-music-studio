"""Authoritative public branding for Shared Skies Media.

Legacy package/module identifiers are intentionally retained for compatibility with
existing projects, databases, sessions and deployment configuration. Public product
copy should use the constants in this module rather than re-declaring names locally.
"""

PRODUCT_NAME = "Shared Skies Media"
PRODUCT_FULL_NAME = "Shared Skies Media"
PRODUCT_SHORT_NAME = "Shared Skies Media"
TAGLINE = "Elevate Your Soul Through Purposeful Media"
ENDORSEMENT = "Powered by Elevate Souls Productions"
PLATFORM_DESCRIPTOR = "Music, Video, Image, Voice, Creator Intelligence, Commerce & Security"
AI_PRODUCER_NAME = "Rhiannon"
AI_SYSTEM_NAME = "Rhiannon Intelligence Systems"
COMPANY_NAME = "Elevate Souls Productions"
RHIANNON_OS_NAME = "Shared Skies Rhiannon OS"
SECURITY_PRODUCT_NAME = "Shared Skies Security — powered by Rhiannon Intelligence"
STREAMING_STUDIOS_NAME = "Shared Skies Streaming Studios"
GAME_FORGE_NAME = "Game Forge Creative Labs"
BRAND_LOGO_PATH = "/static/elevate-souls-command-center-logo.svg"
BRAND_MARK_ROUTE = "/brand/command-center-mark.svg"
BRAND_ART_ROUTE = "/brand/command-center-art.webp"
BRAND_THEME_ROUTE = "/brand/theme.css"
BRAND_FAVICON_ROUTE = "/favicon.webp"

# Historical public names remain valid only as compatibility aliases. They must not be
# presented as the current product identity in new UI or documentation.
LEGACY_PRODUCT_NAMES = (
    "Elevate Souls Productions Content Creation Command Center",
    "Content Creation Command Center",
    "Pulsar-Frequency House",
    "Pulsar-Frequency",
    "4Infinity Creative Studios",
    "Cosmic Creative Studios",
    "Cosmic Creation Studios",
    "The Live Sound Studio",
    "Live Sound Studio",
    "Aura AI",
    "Aura AI Systems",
)


def product_header() -> str:
    return f"{PRODUCT_FULL_NAME} — {ENDORSEMENT}"
