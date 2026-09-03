from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Protocol

from .native_products import BillingPeriod, get_native_product


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Native payment timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Persisted native billing timestamp is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def _add_month(value: datetime) -> datetime:
    import calendar

    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _add_year(value: datetime) -> datetime:
    try:
        return value.replace(year=value.year + 1)
    except ValueError:
        # 29 February renews on the last valid day of February in a non-leap year.
        return value.replace(year=value.year + 1, month=2, day=28)


@dataclass(frozen=True)
class VerifiedNativePayment:
    """Normalized evidence returned only after a provider adapter verifies the event.

    This object is deliberately not accepted directly by the public ledger API. Callers
    must provide a verifier which consumes the provider's raw signed/authenticated event
    and returns this normalized result. A browser redirect or client-supplied purchase
    claim therefore cannot activate an entitlement through this boundary.
    """

    provider: str
    event_id: str
    payment_id: str
    user_id: str
    product_id: str
    billing_period: BillingPeriod | str
    amount_minor: int
    currency: str
    occurred_at: datetime
    founding_offer: bool = False
    verification_id: str = ""


class NativePaymentVerifier(Protocol):
    def verify(self, raw_event: bytes, headers: Mapping[str, str]) -> VerifiedNativePayment:
        """Verify provider authenticity and return normalized completed-payment evidence."""


@dataclass(frozen=True)
class NativeEntitlementReceipt:
    user_id: str
    product_id: str
    entitlements: tuple[str, ...]
    billing_period: str
    period_start: str
    period_end: str
    provider: str
    payment_id: str
    event_id: str


class NativeEntitlementLedger:
    """Server-authoritative Aura OS/Aura Sec purchase and entitlement ledger.

    The ledger never writes to account, ESP membership, owner/admin, creator/agent, or
    creative-plan tables. Its authority is intentionally limited to native-product
    entitlements declared by ``native_products.py``.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS native_payment_events (
                    provider TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    payment_id TEXT NOT NULL,
                    verification_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    billing_period TEXT NOT NULL,
                    founding_offer INTEGER NOT NULL DEFAULT 0,
                    amount_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    PRIMARY KEY(provider, event_id),
                    UNIQUE(provider, payment_id)
                );

                CREATE TABLE IF NOT EXISTS native_entitlements (
                    user_id TEXT NOT NULL,
                    entitlement TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    last_provider TEXT NOT NULL,
                    last_payment_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, entitlement)
                );
                """
            )

    @staticmethod
    def _clean_identifier(value: str, label: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned or len(cleaned) > 200:
            raise ValueError(f"Invalid {label}")
        return cleaned

    def process_verified_event(
        self,
        *,
        raw_event: bytes,
        headers: Mapping[str, str],
        verifier: NativePaymentVerifier,
    ) -> NativeEntitlementReceipt:
        """Verify and atomically activate entitlements for a completed native payment."""
        if not isinstance(raw_event, (bytes, bytearray)) or not raw_event:
            raise ValueError("A raw provider event is required")
        evidence = verifier.verify(bytes(raw_event), headers)
        if not isinstance(evidence, VerifiedNativePayment):
            raise TypeError("Native payment verifier returned unsupported evidence")
        return self._activate(evidence)

    def _activate(self, evidence: VerifiedNativePayment) -> NativeEntitlementReceipt:
        provider = self._clean_identifier(evidence.provider, "payment provider")
        event_id = self._clean_identifier(evidence.event_id, "provider event ID")
        payment_id = self._clean_identifier(evidence.payment_id, "provider payment ID")
        verification_id = self._clean_identifier(evidence.verification_id, "provider verification ID")
        user_id = self._clean_identifier(evidence.user_id, "user ID")
        product = get_native_product(evidence.product_id)
        try:
            period = BillingPeriod(evidence.billing_period)
        except ValueError as exc:
            raise ValueError(f"Unsupported billing period: {evidence.billing_period}") from exc

        currency = (evidence.currency or "").strip().upper()
        if currency != product.currency:
            raise ValueError("Verified payment currency does not match the canonical product currency")
        expected_minor = product.price_minor_for(period, founding_offer=bool(evidence.founding_offer))
        if evidence.amount_minor != expected_minor:
            raise ValueError("Verified payment amount does not match the canonical product price")
        occurred_at = evidence.occurred_at
        if occurred_at.tzinfo is None:
            raise ValueError("Verified payment timestamp must be timezone-aware")
        occurred_at = occurred_at.astimezone(timezone.utc)

        with self._connect() as con:
            # Provider event IDs and captured payment IDs are both replay boundaries.
            if con.execute(
                "SELECT 1 FROM native_payment_events WHERE provider=? AND event_id=?",
                (provider, event_id),
            ).fetchone():
                raise ValueError("Provider event has already been processed")
            if con.execute(
                "SELECT 1 FROM native_payment_events WHERE provider=? AND payment_id=?",
                (provider, payment_id),
            ).fetchone():
                raise ValueError("Provider payment has already been processed")

            if evidence.founding_offer:
                prior = con.execute(
                    "SELECT 1 FROM native_payment_events WHERE user_id=? AND product_id=? LIMIT 1",
                    (user_id, product.id),
                ).fetchone()
                if prior:
                    raise ValueError("Founding pricing is only valid for the first product term")

            current_ends: list[datetime] = []
            for entitlement in product.entitlements:
                row = con.execute(
                    "SELECT period_end FROM native_entitlements WHERE user_id=? AND entitlement=? AND status='active'",
                    (user_id, entitlement),
                ).fetchone()
                end = _parse(row["period_end"]) if row else None
                if end and end > occurred_at:
                    current_ends.append(end)

            start = max(current_ends) if current_ends else occurred_at
            end = _add_month(start) if period is BillingPeriod.MONTHLY else _add_year(start)
            now_iso = _iso(_utcnow())

            con.execute(
                """INSERT INTO native_payment_events
                   (provider,event_id,payment_id,verification_id,user_id,product_id,billing_period,
                    founding_offer,amount_minor,currency,occurred_at,period_start,period_end)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    provider,
                    event_id,
                    payment_id,
                    verification_id,
                    user_id,
                    product.id,
                    period.value,
                    1 if evidence.founding_offer else 0,
                    evidence.amount_minor,
                    currency,
                    _iso(occurred_at),
                    _iso(start),
                    _iso(end),
                ),
            )
            for entitlement in product.entitlements:
                con.execute(
                    """INSERT INTO native_entitlements
                       (user_id,entitlement,product_id,status,period_start,period_end,last_provider,last_payment_id,updated_at)
                       VALUES (?,?,?,'active',?,?,?,?,?)
                       ON CONFLICT(user_id,entitlement) DO UPDATE SET
                         product_id=excluded.product_id,
                         status='active',
                         period_start=excluded.period_start,
                         period_end=excluded.period_end,
                         last_provider=excluded.last_provider,
                         last_payment_id=excluded.last_payment_id,
                         updated_at=excluded.updated_at""",
                    (
                        user_id,
                        entitlement,
                        product.id,
                        _iso(start),
                        _iso(end),
                        provider,
                        payment_id,
                        now_iso,
                    ),
                )

        return NativeEntitlementReceipt(
            user_id=user_id,
            product_id=product.id,
            entitlements=tuple(sorted(product.entitlements)),
            billing_period=period.value,
            period_start=_iso(start),
            period_end=_iso(end),
            provider=provider,
            payment_id=payment_id,
            event_id=event_id,
        )

    def active_entitlements(self, user_id: str, *, at: datetime | None = None) -> frozenset[str]:
        checked_at = (at or _utcnow()).astimezone(timezone.utc)
        with self._connect() as con:
            rows = con.execute(
                "SELECT entitlement,period_end FROM native_entitlements WHERE user_id=? AND status='active'",
                ((user_id or "").strip(),),
            ).fetchall()
        return frozenset(
            row["entitlement"]
            for row in rows
            if (_parse(row["period_end"]) or checked_at) > checked_at
        )

    def require_entitlement(self, user_id: str, entitlement: str, *, at: datetime | None = None) -> None:
        if entitlement not in self.active_entitlements(user_id, at=at):
            raise PermissionError("Active native-product entitlement required")
