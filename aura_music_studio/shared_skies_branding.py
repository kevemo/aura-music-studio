from __future__ import annotations

from typing import Any


PRODUCT_NAME = "Shared Skies Streaming Studios"
PRODUCT_ENDORSEMENT = "Elevate Souls Productions"

_LEGACY_TAG_MAP = {
    "Shared Sky Streaming Studios": PRODUCT_NAME,
    "Shared Sky Owner Operations": "Shared Skies Owner Operations",
}


def install_shared_skies_branding(app: Any) -> None:
    """Normalize legacy presentation strings without renaming canonical route/module IDs.

    The repository has a large established ``shared_sky_*`` internal lineage. Product branding is
    now locked to Shared Skies Streaming Studios, but changing route paths, table names and module
    identifiers would create unnecessary compatibility risk. This installer therefore updates the
    user-facing product constant and route documentation tags only; internal authorities and
    persistent identifiers remain untouched.
    """

    from . import shared_sky_streaming_studios as studios

    studios.PRODUCT_NAME = PRODUCT_NAME
    studios.PRODUCT_ENDORSEMENT = PRODUCT_ENDORSEMENT

    for route in getattr(getattr(app, "router", None), "routes", []) or []:
        tags = list(getattr(route, "tags", []) or [])
        if not tags:
            continue
        normalized = [_LEGACY_TAG_MAP.get(str(tag), str(tag)) for tag in tags]
        if normalized != tags:
            route.tags = normalized


__all__ = [
    "PRODUCT_NAME",
    "PRODUCT_ENDORSEMENT",
    "install_shared_skies_branding",
]
