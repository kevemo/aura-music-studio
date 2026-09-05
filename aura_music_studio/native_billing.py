from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
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
        return value.replace(year=value.year + 1, month=2, day=28)


@dataclass(frozen=True)
class VerifiedNativePayment:
    """Normalized evidence returned only after a provider adapter verifies the event."""

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


class NativeLifecycleKind(str, Enum):
    CANCEL = "cancel"
    REFUND = "refund"


@dataclass(frozen=True)
class VerifiedNativeLifecycleEvent:
    """Provider-authenticated lifecycle evidence tied to a verified native payment.

    ``payment_id`` is the provider payment whose current term is being cancelled or
    refunded. The ledger never accepts browser/client lifecycle claims directly.
    """

    provider: str
    event_id: str
    payment_id: str
    user_id: str
    product_id: str
    event_type: NativeLifecycleKind | str
    occurred_at: datetime
    verification_id: str = ""


class NativeLifecycleVerifier(Protocol):
    def verify(self, raw_event: bytes, headers: Mapping[str, str]) -> VerifiedNativeLifecycleEvent:
        """Verify provider authenticity and return normalized lifecycle evidence."""


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


@dataclass(frozen=True)
class NativeLifecycleReceipt:
    user_id: str
    product_id: str
    event_type: str
    entitlements: tuple[str, ...]
    provider: str
    payment_id: str
    event_id: str
    effective_at: str


class NativeEntitlementLedger:
    """Server-authoritative Aura OS/Aura Sec purchase and entitlement ledger.

    This ledger is intentionally isolated from account roles, ESP roles, creative
    memberships and Protected Data Authority. Provider evidence can mutate only the
    native entitlements declared by ``native_products.py``.
    """

    ACTIVE_STATUSES = ("active", "cancel_at_period_end")

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

                CREATE TABLE IF NOT EXISTS native_lifecycle_events (
                    provider TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    payment_id TEXT NOT NULL,
                    verification_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    affected_entitlements TEXT NOT NULL,
                    PRIMARY KEY(provider, event_id),
                    UNIQUE(provider, event_type, payment_id)
                );
                """
            )

    @staticmethod
    def _clean_identifier(value: str, label: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned or len(cleaned) > 200:
            raise ValueError(f"Invalid {label}")
        return cleaned

    @staticmethod
    def _require_raw_event(raw_event: bytes) -> bytes:
        if not isinstance(raw_event, (bytes, bytearray)) or not raw_event:
            raise ValueError("A raw provider event is required")
        return bytes(raw_event)

    def process_verified_event(
        self,
        *,
        raw_event: bytes,
        headers: Mapping[str, str],
        verifier: NativePaymentVerifier,
    ) -> NativeEntitlementReceipt:
        evidence = verifier.verify(self._require_raw_event(raw_event), headers)
        if not isinstance(evidence, VerifiedNativePayment):
            raise TypeError("Native payment verifier returned unsupported evidence")
        return self._activate(evidence)

    def process_verified_lifecycle_event(
        self,
        *,
        raw_event: bytes,
        headers: Mapping[str, str],
        verifier: NativeLifecycleVerifier,
    ) -> NativeLifecycleReceipt:
        evidence = verifier.verify(self._require_raw_event(raw_event), headers)
        if not isinstance(evidence, VerifiedNativeLifecycleEvent):
            raise TypeError("Native lifecycle verifier returned unsupported evidence")
        return self._apply_lifecycle(evidence)

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
                    "SELECT period_end FROM native_entitlements WHERE user_id=? AND entitlement=? AND status IN ('active','cancel_at_period_end')",
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

    def _apply_lifecycle(self, evidence: VerifiedNativeLifecycleEvent) -> NativeLifecycleReceipt:
        provider = self._clean_identifier(evidence.provider, "payment provider")
        event_id = self._clean_identifier(evidence.event_id, "provider event ID")
        payment_id = self._clean_identifier(evidence.payment_id, "provider payment ID")
        verification_id = self._clean_identifier(evidence.verification_id, "provider verification ID")
        user_id = self._clean_identifier(evidence.user_id, "user ID")
        product = get_native_product(evidence.product_id)
        try:
            event_type = NativeLifecycleKind(evidence.event_type)
        except ValueError as exc:
            raise ValueError(f"Unsupported native lifecycle event: {evidence.event_type}") from exc
        occurred_at = evidence.occurred_at
        if occurred_at.tzinfo is None:
            raise ValueError("Verified lifecycle timestamp must be timezone-aware")
        occurred_at = occurred_at.astimezone(timezone.utc)

        with self._connect() as con:
            payment = con.execute(
                """SELECT user_id,product_id,period_end FROM native_payment_events
                   WHERE provider=? AND payment_id=?""",
                (provider, payment_id),
            ).fetchone()
            if not payment:
                raise ValueError("Lifecycle event does not reference a verified native payment")
            if payment["user_id"] != user_id or payment["product_id"] != product.id:
                raise ValueError("Lifecycle event identity does not match the verified native payment")
            if con.execute(
                "SELECT 1 FROM native_lifecycle_events WHERE provider=? AND event_id=?",
                (provider, event_id),
            ).fetchone():
                raise ValueError("Provider lifecycle event has already been processed")
            if con.execute(
                "SELECT 1 FROM native_lifecycle_events WHERE provider=? AND event_type=? AND payment_id=?",
                (provider, event_type.value, payment_id),
            ).fetchone():
                raise ValueError("Provider lifecycle transition has already been processed")

            matching = con.execute(
                """SELECT entitlement FROM native_entitlements
                   WHERE user_id=? AND product_id=? AND last_provider=? AND last_payment_id=?
                     AND status IN ('active','cancel_at_period_end')""",
                (user_id, product.id, provider, payment_id),
            ).fetchall()
            affected = tuple(sorted(row["entitlement"] for row in matching))
            now_iso = _iso(_utcnow())

            if event_type is NativeLifecycleKind.CANCEL:
                con.execute(
                    """UPDATE native_entitlements SET status='cancel_at_period_end', updated_at=?
                       WHERE user_id=? AND product_id=? AND last_provider=? AND last_payment_id=?
                         AND status='active'""",
                    (now_iso, user_id, product.id, provider, payment_id),
                )
                effective_at = payment["period_end"]
            else:
                con.execute(
                    """UPDATE native_entitlements
                       SET status='refunded', period_end=?, updated_at=?
                       WHERE user_id=? AND product_id=? AND last_provider=? AND last_payment_id=?
                         AND status IN ('active','cancel_at_period_end')""",
                    (_iso(occurred_at), now_iso, user_id, product.id, provider, payment_id),
                )
                effective_at = _iso(occurred_at)

            con.execute(
                """INSERT INTO native_lifecycle_events
                   (provider,event_id,payment_id,verification_id,user_id,product_id,event_type,
                    occurred_at,affected_entitlements)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    provider,
                    event_id,
                    payment_id,
                    verification_id,
                    user_id,
                    product.id,
                    event_type.value,
                    _iso(occurred_at),
                    ",".join(affected),
                ),
            )

        return NativeLifecycleReceipt(
            user_id=user_id,
            product_id=product.id,
            event_type=event_type.value,
            entitlements=affected,
            provider=provider,
            payment_id=payment_id,
            event_id=event_id,
            effective_at=effective_at,
        )

    def has_product_purchase(self, user_id: str, product_id: str) -> bool:
        """Return whether verified payment history exists for this user/product pair.

        This is intentionally history-based rather than active-entitlement based so a refunded,
        cancelled or expired first term can never make a founding offer available again.
        """

        cleaned_user_id = self._clean_identifier(user_id, "user ID")
        product = get_native_product(product_id)
        with self._connect() as con:
            row = con.execute(
                "SELECT 1 FROM native_payment_events WHERE user_id=? AND product_id=? LIMIT 1",
                (cleaned_user_id, product.id),
            ).fetchone()
        return row is not None

    def active_entitlements(self, user_id: str, *, at: datetime | None = None) -> frozenset[str]:
        checked_at = (at or _utcnow()).astimezone(timezone.utc)
        with self._connect() as con:
            rows = con.execute(
                """SELECT entitlement,period_end FROM native_entitlements
                   WHERE user_id=? AND status IN ('active','cancel_at_period_end')""",
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
