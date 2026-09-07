from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from .tier2_daily_meter import TIER2_PLAN_ID, UNLIMITED_PRO_PLAN_ID, Tier2Admission, Tier2DailyMeter

T = TypeVar("T")


class Tier2ProviderGuard:
    """Bind Tier 2 admission to the actual provider/job execution boundary.

    Validation, authorization, tenant resolution, safety checks and any Free-tier Coin
    entitlement handling must happen before this guard is entered. The guard accepts only
    Tier 2 and Unlimited Pro plan ids so callers cannot accidentally grant Free members the
    paid Tier 2 allowance.

    A Tier 2 reservation is completed only when the provider/job submission returns
    successfully. If provider execution raises, the reservation is released so a definitive
    failed submission does not consume the member's daily allowance. Unlimited Pro remains
    unmetered at this boundary.
    """

    def __init__(self, meter: Tier2DailyMeter | None = None):
        self.meter = meter or Tier2DailyMeter()

    def execute(
        self,
        *,
        user_id: str,
        plan_id: str,
        operation: str,
        request_key: str,
        provider_call: Callable[[], T],
    ) -> tuple[T, Tier2Admission]:
        plan = str(plan_id or "").strip().lower()
        if plan not in {TIER2_PLAN_ID, UNLIMITED_PRO_PLAN_ID}:
            raise PermissionError(
                "Tier 2 provider admission is only valid for Tier 2 or Unlimited Pro; "
                "use the membership's separate entitlement path"
            )

        admission = self.meter.reserve(
            user_id=user_id,
            plan_id=plan,
            operation=operation,
            request_key=request_key,
        )
        try:
            result = provider_call()
        except Exception:
            if admission.reservation_id is not None:
                self.meter.release(user_id, admission.reservation_id)
            raise

        if admission.reservation_id is not None:
            self.meter.complete(user_id, admission.reservation_id)
        return result, admission


__all__ = ["Tier2ProviderGuard"]
