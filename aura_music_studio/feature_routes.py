from __future__ import annotations

from enum import Enum
from typing import Iterable

from pydantic import Field

from .org_authority import OrgAction
from .shared_contracts import ContractModel, NonEmptyId


class RouteImplementationState(str, Enum):
    READY = "ready"
    INTEGRATION_PENDING = "integration_pending"
    DISABLED = "disabled"


class FeatureRoute(ContractModel):
    key: NonEmptyId
    path: str = Field(pattern=r"^/")
    title: str = Field(min_length=1, max_length=120)
    capability_key: NonEmptyId | None = None
    required_feature: NonEmptyId | None = None
    org_action: OrgAction | None = None
    lazy_target: str = Field(min_length=1, max_length=255)
    implementation_state: RouteImplementationState = RouteImplementationState.INTEGRATION_PENDING
    unavailable_message: str = Field(
        default="This area is not available in the current build.",
        min_length=1,
        max_length=500,
    )


class RouteRegistry:
    def __init__(self, routes: Iterable[FeatureRoute] = ()) -> None:
        self._routes: dict[str, FeatureRoute] = {}
        self._paths: dict[str, str] = {}
        for route in routes:
            self.register(route)

    def register(self, route: FeatureRoute) -> None:
        if route.key in self._routes:
            raise ValueError(f"duplicate route key {route.key!r}")
        if route.path in self._paths:
            raise ValueError(f"duplicate route path {route.path!r}")
        self._routes[route.key] = route
        self._paths[route.path] = route.key

    def get(self, key: str) -> FeatureRoute:
        try:
            return self._routes[key]
        except KeyError as exc:
            raise KeyError(f"unknown route {key!r}") from exc

    def all(self) -> tuple[FeatureRoute, ...]:
        return tuple(self._routes.values())


SHARED_DISCOVERY_ROUTES = (
    FeatureRoute(
        key="shared_sky",
        path="/shared-sky",
        title="Shared Sky",
        capability_key="shared_sky",
        lazy_target="shared_sky",
    ),
    FeatureRoute(
        key="live_now",
        path="/live-now",
        title="Live Now",
        capability_key="shared_sky.live_now",
        lazy_target="aura_music_studio.shared_sky_live_community:router",
        implementation_state=RouteImplementationState.READY,
        unavailable_message=(
            "Live Now is wired, but individual sessions remain unavailable unless canonical "
            "LIVE and playback-readiness checks pass."
        ),
    ),
    FeatureRoute(
        key="battles",
        path="/shared-sky/battles",
        title="Battles",
        capability_key="shared_sky.battles",
        lazy_target="shared_sky.battles",
    ),
    FeatureRoute(
        key="gifts_cosmic_coins",
        path="/cosmic-coins",
        title="Gifts & Cosmic Coins",
        capability_key="economy.cosmic_coins",
        lazy_target="economy.cosmic_coins",
    ),
    FeatureRoute(
        key="go_live_create",
        path="/go-live-create",
        title="Go Live & Create",
        capability_key="shared_sky.go_live_create",
        lazy_target="shared_sky.go_live_create",
    ),
)

ROUTES = RouteRegistry(SHARED_DISCOVERY_ROUTES)
