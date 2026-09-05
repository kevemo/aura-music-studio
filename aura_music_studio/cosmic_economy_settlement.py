from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .cosmic_economy import CosmicEconomy, EconomyError


CANONICAL_SETTLEMENT_STATUSES = {
    "pending",
    "confirmed",
    "failed",
    "cancelled",
    "refunded",
    "chargeback",
}


@dataclass(frozen=True)
class VerifiedSettlementState:
    """Provider-neutral, authenticated settlement snapshot for one Coin purchase.

    A provider adapter must translate its native payment/settlement states into Chat 5's
    canonical statuses before returning this object. ``verified`` means the adapter obtained
    this state from an authenticated provider API or equivalently authoritative provider
    channel; browser redirects and client-provided values are never sufficient.
    """

    provider: str
    provider_payment_id: str
    status: str
    fiat_amount_minor: int
    fiat_currency: str
    verified: bool
    purchase_id: str | None = None
    observed_at: str | None = None
    metadata: dict[str, Any] | None = None


class CoinSettlementProvider(Protocol):
    """Optional capability implemented by a real Coin payment provider adapter."""

    name: str

    def fetch_settlement_state(
        self,
        *,
        provider_payment_id: str,
        purchase_id: str,
    ) -> VerifiedSettlementState | None:
        """Return authoritative provider state, or ``None`` if the reference is absent."""
        ...


class CoinSettlementReconciler:
    """Compare internal Coin-purchase truth with authenticated provider settlement state.

    Reconciliation is deliberately non-mutating. It never credits/debits Coins, changes a
    purchase status, or rewrites provider data. Material mismatches are persisted to the
    existing reconciliation discrepancy queue for explicit Owner/finance review.
    """

    def __init__(self, economy: CosmicEconomy):
        self.economy = economy

    @staticmethod
    def _normalise_provider_name(value: str) -> str:
        return (value or "").strip().lower()

    @staticmethod
    def _validate_snapshot(
        snapshot: VerifiedSettlementState,
        *,
        expected_provider: str,
    ) -> None:
        if not snapshot.verified:
            raise EconomyError(
                "UNVERIFIED_SETTLEMENT_STATE",
                "Provider settlement state was not authenticated and cannot be reconciled.",
                status_code=503,
            )
        provider = CoinSettlementReconciler._normalise_provider_name(snapshot.provider)
        if provider != expected_provider:
            raise EconomyError(
                "SETTLEMENT_PROVIDER_MISMATCH",
                "Settlement adapter returned state for a different provider.",
                status_code=503,
                details={"expected_provider": expected_provider, "actual_provider": provider},
            )
        if snapshot.status not in CANONICAL_SETTLEMENT_STATUSES:
            raise EconomyError(
                "INVALID_SETTLEMENT_STATUS",
                "Settlement adapter returned a non-canonical payment status.",
                status_code=503,
                details={"status": snapshot.status},
            )
        if (
            isinstance(snapshot.fiat_amount_minor, bool)
            or not isinstance(snapshot.fiat_amount_minor, int)
            or snapshot.fiat_amount_minor <= 0
        ):
            raise EconomyError(
                "INVALID_SETTLEMENT_AMOUNT",
                "Settlement amount must be a positive integer minor-unit value.",
                status_code=503,
            )
        currency = (snapshot.fiat_currency or "").strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise EconomyError(
                "INVALID_SETTLEMENT_CURRENCY",
                "Settlement currency must be a three-letter currency code.",
                status_code=503,
            )

    def _record_discrepancy(
        self,
        *,
        kind: str,
        purchase_id: str,
        expected: dict[str, Any],
        actual: dict[str, Any],
    ) -> dict[str, Any]:
        with self.economy._connect() as con:
            self.economy._begin(con)
            item = self.economy._record_discrepancy_locked(
                con,
                kind,
                purchase_id,
                expected,
                actual,
            )
            con.commit()
        return item

    def reconcile_purchase(
        self,
        purchase_id: str,
        *,
        provider: CoinSettlementProvider,
    ) -> dict[str, Any]:
        with self.economy._connect() as con:
            purchase = con.execute(
                "SELECT * FROM coin_purchases WHERE id=?",
                (purchase_id,),
            ).fetchone()
        if not purchase:
            raise EconomyError(
                "PURCHASE_NOT_FOUND",
                "Coin purchase not found.",
                status_code=404,
            )
        item = dict(purchase)
        provider_name = self._normalise_provider_name(item["provider"])
        adapter_name = self._normalise_provider_name(getattr(provider, "name", ""))
        if not adapter_name or adapter_name != provider_name:
            raise EconomyError(
                "SETTLEMENT_PROVIDER_MISMATCH",
                "Settlement provider does not match the Coin purchase provider.",
                status_code=409,
                details={"purchase_provider": provider_name, "adapter_provider": adapter_name or None},
            )
        provider_payment_id = item.get("provider_payment_id")
        if not provider_payment_id:
            discrepancy = self._record_discrepancy(
                kind="PROVIDER_SETTLEMENT_REFERENCE_MISSING",
                purchase_id=item["id"],
                expected={"provider_payment_id": "bound_provider_reference"},
                actual={"provider_payment_id": None},
            )
            return {
                "ok": False,
                "purchase_id": item["id"],
                "provider": provider_name,
                "discrepancies": [discrepancy],
                "provider_state": None,
            }

        fetch = getattr(provider, "fetch_settlement_state", None)
        if not callable(fetch):
            raise EconomyError(
                "SETTLEMENT_RECONCILIATION_UNAVAILABLE",
                "Configured Coin payment provider does not expose authenticated settlement retrieval.",
                status_code=503,
                details={"provider": provider_name},
            )
        try:
            snapshot = fetch(
                provider_payment_id=str(provider_payment_id),
                purchase_id=item["id"],
            )
        except EconomyError:
            raise
        except Exception as exc:
            raise EconomyError(
                "SETTLEMENT_PROVIDER_UNAVAILABLE",
                "Authoritative provider settlement state could not be retrieved.",
                status_code=503,
                details={"provider": provider_name, "purchase_id": item["id"]},
            ) from exc

        if snapshot is None:
            discrepancy = self._record_discrepancy(
                kind="PROVIDER_SETTLEMENT_MISSING",
                purchase_id=item["id"],
                expected={"provider_payment_id": str(provider_payment_id)},
                actual={"provider_record": None},
            )
            return {
                "ok": False,
                "purchase_id": item["id"],
                "provider": provider_name,
                "discrepancies": [discrepancy],
                "provider_state": None,
            }

        self._validate_snapshot(snapshot, expected_provider=provider_name)
        discrepancies: list[dict[str, Any]] = []

        if snapshot.provider_payment_id != provider_payment_id:
            discrepancies.append(
                self._record_discrepancy(
                    kind="PROVIDER_SETTLEMENT_REFERENCE_MISMATCH",
                    purchase_id=item["id"],
                    expected={"provider_payment_id": str(provider_payment_id)},
                    actual={"provider_payment_id": snapshot.provider_payment_id},
                )
            )
        if snapshot.purchase_id is not None and snapshot.purchase_id != item["id"]:
            discrepancies.append(
                self._record_discrepancy(
                    kind="PROVIDER_SETTLEMENT_PURCHASE_MISMATCH",
                    purchase_id=item["id"],
                    expected={"purchase_id": item["id"]},
                    actual={"purchase_id": snapshot.purchase_id},
                )
            )
        if int(snapshot.fiat_amount_minor) != int(item["fiat_amount_minor"]):
            discrepancies.append(
                self._record_discrepancy(
                    kind="PROVIDER_SETTLEMENT_AMOUNT_MISMATCH",
                    purchase_id=item["id"],
                    expected={"fiat_amount_minor": int(item["fiat_amount_minor"])},
                    actual={"fiat_amount_minor": int(snapshot.fiat_amount_minor)},
                )
            )
        if snapshot.fiat_currency.strip().upper() != str(item["fiat_currency"]).upper():
            discrepancies.append(
                self._record_discrepancy(
                    kind="PROVIDER_SETTLEMENT_CURRENCY_MISMATCH",
                    purchase_id=item["id"],
                    expected={"fiat_currency": str(item["fiat_currency"]).upper()},
                    actual={"fiat_currency": snapshot.fiat_currency.strip().upper()},
                )
            )
        if snapshot.status != item["status"]:
            discrepancies.append(
                self._record_discrepancy(
                    kind="PROVIDER_SETTLEMENT_STATUS_MISMATCH",
                    purchase_id=item["id"],
                    expected={"status": item["status"]},
                    actual={"status": snapshot.status},
                )
            )

        return {
            "ok": not discrepancies,
            "purchase_id": item["id"],
            "provider": provider_name,
            "discrepancies": discrepancies,
            "provider_state": {
                "provider": provider_name,
                "provider_payment_id": snapshot.provider_payment_id,
                "purchase_id": snapshot.purchase_id,
                "status": snapshot.status,
                "fiat_amount_minor": int(snapshot.fiat_amount_minor),
                "fiat_currency": snapshot.fiat_currency.strip().upper(),
                "observed_at": snapshot.observed_at,
            },
        }

    def reconcile_provider(
        self,
        *,
        provider: CoinSettlementProvider,
        limit: int = 500,
    ) -> dict[str, Any]:
        provider_name = self._normalise_provider_name(getattr(provider, "name", ""))
        if not provider_name:
            raise EconomyError(
                "SETTLEMENT_PROVIDER_MISMATCH",
                "Settlement provider requires a canonical provider name.",
                status_code=503,
            )
        limit = max(1, min(int(limit), 1000))
        with self.economy._connect() as con:
            purchases = con.execute(
                """SELECT id FROM coin_purchases
                   WHERE provider=? ORDER BY created_at ASC LIMIT ?""",
                (provider_name, limit),
            ).fetchall()

        results: list[dict[str, Any]] = []
        mismatch_count = 0
        for row in purchases:
            result = self.reconcile_purchase(row["id"], provider=provider)
            results.append(result)
            if not result["ok"]:
                mismatch_count += 1
        return {
            "provider": provider_name,
            "checked": len(results),
            "matched": len(results) - mismatch_count,
            "mismatched": mismatch_count,
            "ok": mismatch_count == 0,
            "results": results,
        }
