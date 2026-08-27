"""Authoritative public branding for Elevate Souls Productions Content Creation Command Center.

Legacy repository, package, cookie, route and environment identifiers are intentionally
retained where changing them could break existing projects, sessions, integrations or
deployment configuration. Public product copy should use the constants in this module.
"""

PRODUCT_NAME = "Elevate Souls Productions Content Creation Command Center"
PRODUCT_FULL_NAME = "Elevate Souls Productions Content Creation Command Center"
PRODUCT_SHORT_NAME = "Content Creation Command Center"
TAGLINE = "Elevate Your Soul Through Purposeful Media"
ENDORSEMENT = "Powered by Aura AI"
PLATFORM_DESCRIPTOR = "Music, Video, Image, Game, Voice, Social & Creator Intelligence"
AI_PRODUCER_NAME = "Aura"
AI_SYSTEM_NAME = "Aura AI"
COMPANY_NAME = "Elevate Souls Productions"
BRAND_ASSET_URL = "/brand/elevate-souls-productions-content-creation-command-center.webp"


def product_header() -> str:
    return f"{PRODUCT_FULL_NAME} — {TAGLINE} — {ENDORSEMENT}"
