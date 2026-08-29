from __future__ import annotations

import json
import os
from dataclasses import dataclass
from uuid import uuid4

from .credit_wallet import CreditWalletStore

_COST_ENV = "LSS_CREATION_COIN_COSTS_JSON"
_IMAGE_POSTER_OVERAGE = "image_poster_overage"
_FREE_VIDEO_RENDER = "free_video_render"
_MAX_COST = 1_000_000


@dataclass(frozen=True)
class CreationCoinCharge:
    cost: int
    transaction: dict
    refund_reference: str


def creation_coin_costs() -> dict[str, int]:
    """Return owner-configured Creation Coin costs without inventing commercial values.

    The deployment supplies a small JSON object, for example
    ``{"image_poster_overage": 25, "free_video_render": 100}``. Unknown keys are ignored so
    future products can be introduced independently, while malformed or unsafe configured
    values fail closed.
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
    for key in (_IMAGE_POSTER_OVERAGE, _FREE_VIDEO_RENDER):
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


def _quote(user_id: str, product_key: str) -> dict:
    cost = creation_coin_costs().get(product_key)
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


def _charge(
    user_id: str,
    *,
    product_key: str,
    project_id: str,
    directive_id: str,
    reason: str,
    charge_reference: str | None = None,
    refund_reference: str | None = None,
) -> CreationCoinCharge:
    cost = creation_coin_costs().get(product_key)
    if cost is None:
        raise RuntimeError(f"Creation Coin {product_key.replace('_', ' ')} is not configured")

    if (charge_reference is None) != (refund_reference is None):
        raise ValueError("Stable Creation Coin charge and refund references must be supplied together")
    if charge_reference is None:
        attempt_id = uuid4().hex
        charge_reference = f"creation:{product_key}:{user_id}:{project_id}:{directive_id}:{attempt_id}"
        refund_reference = f"creation:{product_key}_refund:{user_id}:{project_id}:{directive_id}:{attempt_id}"
    else:
        charge_reference = charge_reference.strip()
        refund_reference = (refund_reference or "").strip()
        if not charge_reference or not refund_reference:
            raise ValueError("Stable Creation Coin references cannot be blank")
        if len(charge_reference) > 180 or len(refund_reference) > 180:
            raise ValueError("Stable Creation Coin references are too long")

    wallet = CreditWalletStore()
    transaction = wallet.spend(
        user_id,
        cost,
        reason=reason,
        reference=charge_reference,
        actor="creative_metering",
    )
    return CreationCoinCharge(cost=cost, transaction=transaction, refund_reference=refund_reference)


def _refund(user_id: str, charge: CreationCoinCharge, *, reason: str) -> dict:
    return CreditWalletStore().adjust(
        user_id,
        charge.cost,
        kind="refund",
        reason=(reason or "Creative renderer submission failed")[:240],
        reference=charge.refund_reference,
        actor="creative_metering",
    )


def image_poster_overage_cost() -> int | None:
    return creation_coin_costs().get(_IMAGE_POSTER_OVERAGE)


def image_poster_overage_quote(user_id: str) -> dict:
    return _quote(user_id, _IMAGE_POSTER_OVERAGE)


def charge_image_poster_overage(
    user_id: str,
    *,
    project_id: str,
    directive_id: str,
    charge_reference: str | None = None,
    refund_reference: str | None = None,
) -> CreationCoinCharge:
    return _charge(
        user_id,
        product_key=_IMAGE_POSTER_OVERAGE,
        project_id=project_id,
        directive_id=directive_id,
        reason="Image/poster generation beyond the included daily allowance",
        charge_reference=charge_reference,
        refund_reference=refund_reference,
    )


def refund_image_poster_overage(user_id: str, charge: CreationCoinCharge, *, reason: str) -> dict:
    """Return a pre-submission image charge when the renderer does not accept the job."""

    return _refund(user_id, charge, reason=reason)


def free_video_render_cost() -> int | None:
    return creation_coin_costs().get(_FREE_VIDEO_RENDER)


def free_video_render_quote(user_id: str) -> dict:
    return _quote(user_id, _FREE_VIDEO_RENDER)


def charge_free_video_render(
    user_id: str,
    *,
    project_id: str,
    directive_id: str,
    charge_reference: str | None = None,
    refund_reference: str | None = None,
) -> CreationCoinCharge:
    return _charge(
        user_id,
        product_key=_FREE_VIDEO_RENDER,
        project_id=project_id,
        directive_id=directive_id,
        reason="Free-tier video render purchased with Creation Coins",
        charge_reference=charge_reference,
        refund_reference=refund_reference,
    )


def refund_free_video_render(user_id: str, charge: CreationCoinCharge, *, reason: str) -> dict:
    """Return a prepaid Free video charge when the renderer does not accept the job."""

    return _refund(user_id, charge, reason=reason)


__all__ = [
    "CreationCoinCharge",
    "charge_free_video_render",
    "charge_image_poster_overage",
    "creation_coin_costs",
    "free_video_render_cost",
    "free_video_render_quote",
    "image_poster_overage_cost",
    "image_poster_overage_quote",
    "refund_free_video_render",
    "refund_image_poster_overage",
]
