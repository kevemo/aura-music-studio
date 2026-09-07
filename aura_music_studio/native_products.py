from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum


class BillingPeriod(StrEnum):
    MONTHLY = "monthly"
    ANNUAL = "annual"


# Compatibility-stable persisted entitlement identifiers. Public display naming is SLS.
AURA_OS_ENTITLEMENT = "aura_os"
AURA_SEC_ENTITLEMENT = "aura_sec"


@dataclass(frozen=True)
class NativeProduct:
    id: str
    name: str
    currency: str
    monthly_price: Decimal
    annual_price: Decimal
    entitlements: frozenset[str]
    founding_first_year_price: Decimal | None = None

    def price_for(self, period: BillingPeriod | str, *, founding_offer: bool = False) -> Decimal:
        try:
            billing_period = BillingPeriod(period)
        except ValueError as exc:
            raise ValueError(f"Unsupported billing period: {period}") from exc

        if founding_offer:
            if billing_period is not BillingPeriod.ANNUAL or self.founding_first_year_price is None:
                raise ValueError("Founding pricing is not available for this product and billing period")
            return self.founding_first_year_price

        if billing_period is BillingPeriod.MONTHLY:
            return self.monthly_price
        return self.annual_price

    def renewal_price_for(self, period: BillingPeriod | str) -> Decimal:
        """Return the canonical recurring renewal price, never a temporary acquisition offer."""
        return self.price_for(period, founding_offer=False)

    def price_minor_for(self, period: BillingPeriod | str, *, founding_offer: bool = False) -> int:
        return int((self.price_for(period, founding_offer=founding_offer) * Decimal("100")).to_integral_value())

    def public_dict(self) -> dict:
        data = asdict(self)
        data["monthly_price"] = str(self.monthly_price)
        data["annual_price"] = str(self.annual_price)
        data["monthly_price_minor"] = self.price_minor_for(BillingPeriod.MONTHLY)
        data["annual_price_minor"] = self.price_minor_for(BillingPeriod.ANNUAL)
        data["entitlements"] = sorted(self.entitlements)
        if self.founding_first_year_price is not None:
            data["founding_first_year_price"] = str(self.founding_first_year_price)
            data["founding_first_year_price_minor"] = self.price_minor_for(
                BillingPeriod.ANNUAL,
                founding_offer=True,
            )
            data["founding_offer_renews_at_annual_price"] = True
        return data


NATIVE_PRODUCTS: dict[str, NativeProduct] = {
    "aura_os": NativeProduct(
        id="aura_os",
        name="Aura OS",
        currency="GBP",
        monthly_price=Decimal("4.99"),
        annual_price=Decimal("49.99"),
        entitlements=frozenset({AURA_OS_ENTITLEMENT}),
    ),
    "aura_sec": NativeProduct(
        id="aura_sec",
        name="Elevate Souls Productions Secure Lattice System (SLS)",
        currency="GBP",
        monthly_price=Decimal("4.99"),
        annual_price=Decimal("34.99"),
        founding_first_year_price=Decimal("24.99"),
        entitlements=frozenset({AURA_SEC_ENTITLEMENT}),
    ),
    "aura_os_sec_bundle": NativeProduct(
        id="aura_os_sec_bundle",
        name="Aura OS + Elevate Souls Productions Secure Lattice System (SLS)",
        currency="GBP",
        monthly_price=Decimal("7.99"),
        annual_price=Decimal("69.99"),
        entitlements=frozenset({AURA_OS_ENTITLEMENT, AURA_SEC_ENTITLEMENT}),
    ),
}


def get_native_product(product_id: str) -> NativeProduct:
    key = (product_id or "").strip().lower()
    if key not in NATIVE_PRODUCTS:
        raise ValueError(f"Unknown native product: {product_id}")
    return NATIVE_PRODUCTS[key]


def require_native_entitlement(product_id: str, entitlement: str) -> None:
    product = get_native_product(product_id)
    if entitlement not in product.entitlements:
        raise PermissionError(f"{product_id} does not grant the {entitlement} entitlement")


def public_native_products() -> list[dict]:
    return [NATIVE_PRODUCTS[key].public_dict() for key in ("aura_os", "aura_sec", "aura_os_sec_bundle")]
