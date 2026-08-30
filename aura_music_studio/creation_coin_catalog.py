from __future__ import annotations

from collections.abc import Mapping
from typing import Any


# Authoritative public Creation Coin catalogue. Stripe Price IDs remain deployment secrets,
# but operators and clients must never be able to redefine the amount of money or Coins
# represented by a public pack.
CANONICAL_CREATION_COIN_PACKS: frozenset[tuple[int, int, str]] = frozenset(
    {
        (1_000, 500, "GBP"),
        (2_500, 1_000, "GBP"),
        (6_000, 2_000, "GBP"),
    }
)


def _field(pack: Any, name: str) -> Any:
    if isinstance(pack, Mapping):
        return pack.get(name)
    return getattr(pack, name, None)


def _pack_signature(pack: Any) -> tuple[int, int, str]:
    try:
        credits = int(_field(pack, "credits") if _field(pack, "credits") is not None else _field(pack, "creation_coins"))
        amount_minor = int(_field(pack, "amount_minor"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Creation Coin pack has invalid quantity or amount") from exc
    currency = str(_field(pack, "currency") or "").strip().upper()
    return credits, amount_minor, currency


def validate_creation_coin_packs(packs: Mapping[str, Any]) -> Mapping[str, Any]:
    """Require the complete immutable public Creation Coin catalogue.

    Pack IDs, display labels and Stripe Price IDs can be configured privately, but the public
    value proposition is fixed by the master commercial specification. Requiring the complete
    set also prevents a partial or stale deployment configuration from silently changing the
    member storefront.
    """

    if not isinstance(packs, Mapping):
        raise ValueError("Creation Coin catalogue must be a mapping")

    seen: set[tuple[int, int, str]] = set()
    for pack in packs.values():
        signature = _pack_signature(pack)
        if signature not in CANONICAL_CREATION_COIN_PACKS:
            raise ValueError("Creation Coin pack does not match the authoritative public catalogue")
        if signature in seen:
            raise ValueError("Creation Coin catalogue contains a duplicate authoritative pack")
        seen.add(signature)

    if seen != set(CANONICAL_CREATION_COIN_PACKS):
        raise ValueError("Creation Coin catalogue is incomplete")
    return packs


__all__ = ["CANONICAL_CREATION_COIN_PACKS", "validate_creation_coin_packs"]
