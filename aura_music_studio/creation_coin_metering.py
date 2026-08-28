from __future__ import annotations

import json
import os
from dataclasses import dataclass
from uuid import uuid4

from .credit_wallet import CreditWalletStore

_COST_ENV = "LSS_CREATION_COIN_COSTS_JSON"
_IMAGE_POSTER_OVERAGE = "image_poster_overage"
_MAX_COST = 1_000_000


@dataclass(frozen=True)
class CreationCoinCharge:
    cost: int
    transaction: dict
    refund_reference: str


def creation_coin_costs() -> dict[str, int]:
    """Return owner-configured Creation Coin costs without inventing commercial values.

    The deployment supplies a small JSON object, for example
    ``{"image_poster_overage": 25}``. Unknown keys are ignored so future products can be
    introduced independently, while malformed or unsafe configured values fail closed.
    """

    raw = (os.getenv(_COST_ENV) or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{_COST_ENV} is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{_COST_ENV} must be a JSON object")

    result: dict[str, int] = {}
    for key in (_IMAGE_POSTER_OVERAGE,):
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, bool):
            raise ValueError(f"Creation Coin cost for {key} must be a positive integer")
        try:
            amount = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Creation Coin cost for {key} must be a positive integer") from exc
        if amount <= 0 or amount > _MAX_COST or amount != value:
            raise ValueError(f"Creation Coin cost for {key} must be a positive integer up to {_MAX_COST}")
        result[key] = amount
    return result


def image_poster_overage_cost() -> int | None:
    return creation_coin_costs().get(_IMAGE_POSTER_OVERAGE)


def image_poster_overage_quote(user_id: str) -> dict:
    cost = image_poster_overage_cost()
    wallet = CreditWalletStore()
    balance = wallet.balance(user_id)
    return {
        "enabled": cost is not None,
        "cost": cost,
        "balance": balance,
        "affordable": bool(cost is not None and balance >= cost),
        "unit": "CREATION_COIN",
        "membership_effect": "none",
        "esp_role_effect": "none",
    }


def charge_image_poster_overage(
    user_id: str,
    *,
    project_id: str,
    directive_id: str,
) -> CreationCoinCharge:
    cost = image_poster_overage_cost()
    if cost is None:
        raise RuntimeError("Creation Coin image/poster overage is not configured")

    attempt_id = uuid4().hex
    charge_reference = f"creation:image_poster:{user_id}:{project_id}:{directive_id}:{attempt_id}"
    refund_reference = f"creation:image_poster_refund:{user_id}:{project_id}:{directive_id}:{attempt_id}"
    wallet = CreditWalletStore()
    transaction = wallet.spend(
        user_id,
        cost,
        reason="Image/poster generation beyond the included daily allowance",
        reference=charge_reference,
        actor="creative_metering",
    )
    return CreationCoinCharge(cost=cost, transaction=transaction, refund_reference=refund_reference)


def refund_image_poster_overage(user_id: str, charge: CreationCoinCharge, *, reason: str) -> dict:
    """Return a pre-submission charge when the renderer does not accept the job."""

    return CreditWalletStore().adjust(
        user_id,
        charge.cost,
        kind="refund",
        reason=(reason or "Creative renderer submission failed")[:240],
        reference=charge.refund_reference,
        actor="creative_metering",
    )


__all__ = [
    "CreationCoinCharge",
    "charge_image_poster_overage",
    "creation_coin_costs",
    "image_poster_overage_cost",
    "image_poster_overage_quote",
    "refund_image_poster_overage",
]
