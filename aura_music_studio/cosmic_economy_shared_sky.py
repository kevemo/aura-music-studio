from __future__ import annotations

from typing import Any

from .cosmic_economy import LiveGiftContext
from .cosmic_economy_integrations import (
    UnavailableLiveSessionDirectory,
    configure_economy_integrations,
    runtime_integrations,
)


_INTEGRATION_STATUS: dict[str, Any] = {
    "state": "pending",
    "reason": "shared_sky_live_adapter_not_available",
}


def configure_chat5_shared_sky() -> dict[str, Any]:
    """Bind Chat 5 to Chat 4's authoritative live-session adapter when merged.

    The adapter owns only broadcast/live-recipient truth. Age/region eligibility, Coin pricing,
    spending, risk, payout and Battle scoring remain separate controls. Missing or broken Shared
    Sky modules leave the existing fail-closed live-session directory in place.
    """

    global _INTEGRATION_STATUS
    try:
        from . import shared_sky_live_community as live
        from .shared_sky_live_integrations import SharedSkyGiftLiveSessionDirectory

        adapter = SharedSkyGiftLiveSessionDirectory(LiveGiftContext, live.community)
        configure_economy_integrations(live_sessions=adapter)
        _INTEGRATION_STATUS = {
            "state": "registered",
            "source": "aura_music_studio.shared_sky_live_community.community",
            "adapter": "SharedSkyGiftLiveSessionDirectory",
        }
    except (ImportError, ModuleNotFoundError):
        if not isinstance(runtime_integrations.live_sessions, UnavailableLiveSessionDirectory):
            _INTEGRATION_STATUS = {
                "state": "registered",
                "source": type(runtime_integrations.live_sessions).__name__,
                "adapter": type(runtime_integrations.live_sessions).__name__,
            }
        else:
            _INTEGRATION_STATUS = {
                "state": "pending",
                "reason": "shared_sky_live_adapter_not_available",
            }
    except Exception as exc:
        _INTEGRATION_STATUS = {
            "state": "degraded",
            "reason": str(getattr(exc, "code", type(exc).__name__))[:120],
        }
    return dict(_INTEGRATION_STATUS)


def chat5_shared_sky_status() -> dict[str, Any]:
    return dict(_INTEGRATION_STATUS)
