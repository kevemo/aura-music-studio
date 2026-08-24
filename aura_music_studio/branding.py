"""Authoritative public branding for Pulsar-Frequency House.

Legacy package/module identifiers are intentionally retained for compatibility with
existing projects, databases, sessions and deployment configuration. Public product
copy should use the constants in this module.
"""

PRODUCT_NAME = "Pulsar-Frequency House"
PRODUCT_FULL_NAME = "Pulsar-Frequency House"
PRODUCT_SHORT_NAME = "Pulsar-Frequency"
TAGLINE = "For Professional Creation Beyond The Cosmos"
ENDORSEMENT = "Powered by Elevate Souls Productions & Aura AI Systems"
PLATFORM_DESCRIPTOR = "Music, Video, Image, Voice & Creator Intelligence"
AI_PRODUCER_NAME = "Aura"
AI_SYSTEM_NAME = "Aura AI Systems"
COMPANY_NAME = "Elevate Souls Productions"


def product_header() -> str:
    return f"{PRODUCT_FULL_NAME} — {TAGLINE} — {ENDORSEMENT}"
