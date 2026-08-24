"""Authoritative public branding for 4Infinity Creative Studios.

Legacy package/module identifiers are intentionally retained for compatibility with
existing projects, databases, sessions and deployment configuration. Public product
copy should use the constants in this module.
"""

PRODUCT_NAME = "4Infinity Creative Studios"
PRODUCT_FULL_NAME = "4Infinity Creative Studios"
PRODUCT_SHORT_NAME = "4Infinity"
TAGLINE = "Music, Video, Image & Creator Intelligence"
ENDORSEMENT = "Powered by Elevate Souls Productions and Aura AI Systems"
AI_PRODUCER_NAME = "Aura"
AI_SYSTEM_NAME = "Aura AI Systems"
COMPANY_NAME = "Elevate Souls Productions"


def product_header() -> str:
    return f"{PRODUCT_FULL_NAME} — {TAGLINE} — {ENDORSEMENT}"
