from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from .cosmic_economy import EconomyError, VerifiedPaymentEvent


@dataclass(frozen=True)
class CoinCheckout:
    provider: str
    provider_payment_id: str
    checkout_url: str
    status: str = "pending"


class CoinPaymentProvider(Protocol):
    """Provider boundary for Coin checkout and verified webhook translation.

    `create_checkout` MUST use the supplied server-generated idempotency key with the
    provider's official idempotency mechanism (or equivalent retrieve-or-create behaviour).
    A retry for the same internal purchase must resolve to the same provider payment/checkout,
    never create a second chargeable intent.
    """

    name: str

    def create_checkout(
        self,
        *,
        purchase_id: str,
        user_id: str,
        fiat_amount_minor: int,
        fiat_currency: str,
        coin_quantity: int,
        idempotency_key: str,
    ) -> CoinCheckout:
        ...

    def verify_webhook(self, *, headers: Mapping[str, str], body: bytes) -> VerifiedPaymentEvent:
        ...


class CoinPaymentProviderRegistry:
    """Explicit registry. Production starts empty and therefore fails closed.

    Chat 5 does not reuse the existing manual subscription PayPal invoice links for Coins:
    those links cannot prove a Coin purchase server-side. A real provider adapter must be
    registered by deployment code with official signature verification and idempotent checkout
    creation.
    """

    def __init__(self):
        self._providers: dict[str, CoinPaymentProvider] = {}

    def register(self, provider: CoinPaymentProvider) -> None:
        name = (provider.name or "").strip().lower()
        if not name:
            raise ValueError("Coin payment provider requires a name")
        self._providers[name] = provider

    def get(self, name: str) -> CoinPaymentProvider:
        key = (name or "").strip().lower()
        provider = self._providers.get(key)
        if provider is None:
            raise EconomyError(
                "PAYMENT_PROVIDER_UNAVAILABLE",
                "A verified Coin payment provider is not configured for this deployment.",
                status_code=503,
                details={"provider": key or None},
            )
        return provider

    def configured(self) -> list[str]:
        return sorted(self._providers)


coin_payment_providers = CoinPaymentProviderRegistry()
