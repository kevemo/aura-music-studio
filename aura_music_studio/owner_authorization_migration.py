from __future__ import annotations

from .owner_auth import owner_authorized


def install_owner_authorization_migration() -> None:
    """Point remaining legacy owner portal checks at the opaque session authority.

    The owner-user portal is a large mature UI surface. Its business handlers remain in
    place, but its single private `_authorized(request)` seam is replaced at composition
    time with the same server-side owner-session authority used by the newer Mary/Kev
    owner surfaces. No deployment key is copied into request cookies.
    """

    from . import owner_users_portal

    owner_users_portal._authorized = owner_authorized


__all__ = ["install_owner_authorization_migration"]
