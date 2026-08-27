from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from decimal import Decimal

_SKU_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True)
class AuraSecSku:
    id: str
    display_name: str
    currency: str
    price_minor: int
    period_days: int
    device_limit: int
    active: bool = True

    def validate(self) -> "AuraSecSku":
        if not _SKU_RE.fullmatch(self.id):
            raise ValueError("Aura Sec SKU id must be a stable lowercase identifier")
        if not self.display_name.strip() or len(self.display_name) > 120:
            raise ValueError("Aura Sec SKU display name is invalid")
        if not _CURRENCY_RE.fullmatch(self.currency):
            raise ValueError("Aura Sec SKU currency must be a three-letter uppercase code")
        if not 1 <= int(self.price_minor) <= 100_000_000:
            raise ValueError("Aura Sec SKU price must be a positive minor-unit amount")
        if not 1 <= int(self.period_days) <= 3660:
            raise ValueError("Aura Sec SKU period must be between 1 and 3660 days")
        if not 1 <= int(self.device_limit) <= 100:
            raise ValueError("Aura Sec SKU device allowance must be between 1 and 100")
        return self

    def public(self) -> dict:
        data = asdict(self)
        # Decimal string avoids binary-float currency presentation.
        data["price_major"] = str((Decimal(self.price_minor) / Decimal(100)).quantize(Decimal("0.00")))
        return data


class AuraSecCatalog:
    """Owner-configured standalone Aura Sec commercial catalogue.

    No security SKU exists by default. Production config can provide `AURA_SEC_SKUS_JSON`
    after commercial/legal/payment configuration has been approved. This intentionally
    does not reuse creative plan ids or amounts.
    """

    def __init__(self, skus: tuple[AuraSecSku, ...] = ()):
        ids: set[str] = set()
        validated = []
        for sku in skus:
            item = sku.validate()
            if item.id in ids:
                raise ValueError("Duplicate Aura Sec SKU id")
            ids.add(item.id)
            validated.append(item)
        self._skus = tuple(validated)

    @classmethod
    def from_environment(cls) -> "AuraSecCatalog":
        raw = (os.getenv("AURA_SEC_SKUS_JSON") or "").strip()
        if not raw:
            return cls()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("AURA_SEC_SKUS_JSON is not valid JSON") from exc
        if not isinstance(payload, list):
            raise ValueError("AURA_SEC_SKUS_JSON must contain a list of SKU objects")
        skus = []
        for item in payload:
            if not isinstance(item, dict):
                raise ValueError("Each Aura Sec SKU must be an object")
            allowed = {"id", "display_name", "currency", "price_minor", "period_days", "device_limit", "active"}
            unexpected = set(item) - allowed
            if unexpected:
                raise ValueError(f"Unexpected Aura Sec SKU fields: {', '.join(sorted(unexpected))}")
            required = {"id", "display_name", "currency", "price_minor", "period_days", "device_limit"}
            missing = required - set(item)
            if missing:
                raise ValueError(f"Aura Sec SKU missing fields: {', '.join(sorted(missing))}")
            skus.append(
                AuraSecSku(
                    id=str(item["id"]),
                    display_name=str(item["display_name"]),
                    currency=str(item["currency"]),
                    price_minor=int(item["price_minor"]),
                    period_days=int(item["period_days"]),
                    device_limit=int(item["device_limit"]),
                    active=bool(item.get("active", True)),
                )
            )
        return cls(tuple(skus))

    def active(self) -> list[dict]:
        return [sku.public() for sku in self._skus if sku.active]

    def get_active(self, sku_id: str) -> AuraSecSku | None:
        return next((sku for sku in self._skus if sku.active and sku.id == sku_id), None)

    def public_state(self) -> dict:
        active = self.active()
        return {
            "sale_configured": bool(active),
            "skus": active,
            "creative_plan_ids_accepted_as_security_sku": False,
            "checkout_enabled": False,
            "truth": (
                "Aura Sec remains unavailable for purchase until an owner-approved security SKU and verified payment checkout are configured."
                if not active
                else "Security SKUs are configured, but checkout remains disabled until the verified payment workflow is explicitly connected."
            ),
        }


__all__ = ["AuraSecCatalog", "AuraSecSku"]
