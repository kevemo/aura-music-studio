from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

BASELINE_PACK_ID = "cosmic-1000-gbp"
BASELINE_PACK_VERSION = 1
BASELINE_COINS = 1000
BASELINE_PRICE_MINOR = 500
BASELINE_CURRENCY = "GBP"

ACCOUNT_STATUSES = {"active", "frozen", "restricted", "closed"}
RECEIPT_STATUSES = {"pending", "held", "cleared", "adjusted", "reversed", "paid"}

PURCHASE_CREDIT = "PURCHASE_CREDIT"
PROMOTIONAL_CREDIT = "PROMOTIONAL_CREDIT"
OWNER_APPROVED_ADJUSTMENT_CREDIT = "OWNER_APPROVED_ADJUSTMENT_CREDIT"
OWNER_APPROVED_ADJUSTMENT_DEBIT = "OWNER_APPROVED_ADJUSTMENT_DEBIT"
GIFT_DEBIT = "GIFT_DEBIT"
PURCHASE_REVERSAL_DEBIT = "PURCHASE_REVERSAL_DEBIT"
REFUND_DEBIT = "REFUND_DEBIT"
CHARGEBACK_DEBIT = "CHARGEBACK_DEBIT"
GIFT_REVERSAL_CREDIT = "GIFT_REVERSAL_CREDIT"

LEDGER_ENTRY_TYPES = {
    PURCHASE_CREDIT,
    PROMOTIONAL_CREDIT,
    OWNER_APPROVED_ADJUSTMENT_CREDIT,
    OWNER_APPROVED_ADJUSTMENT_DEBIT,
    GIFT_DEBIT,
    PURCHASE_REVERSAL_DEBIT,
    REFUND_DEBIT,
    CHARGEBACK_DEBIT,
    GIFT_REVERSAL_CREDIT,
}

FEATURE_FLAGS = {
    "coin_purchases_enabled": True,
    "gift_sends_enabled": True,
    "creator_receiving_enabled": True,
    "creator_payout_enabled": False,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


class EconomyError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **({"details": self.details} if self.details else {})}


@dataclass(frozen=True)
class LiveGiftContext:
    live_session_id: str
    recipient_creator_id: str
    active: bool
    gift_eligible: bool
    recipient_eligible: bool = True
    battle_id: str | None = None
    battle_round_id: str | None = None


class LiveSessionDirectory(Protocol):
    def gift_context(self, *, live_session_id: str, recipient_creator_id: str) -> LiveGiftContext:
        ...


class UnavailableLiveSessionDirectory:
    def gift_context(self, *, live_session_id: str, recipient_creator_id: str) -> LiveGiftContext:
        raise EconomyError(
            "LIVE_VALIDATION_UNAVAILABLE",
            "Authoritative Shared Sky live-session validation is not configured.",
            status_code=503,
        )


@dataclass(frozen=True)
class RiskDecision:
    action: str
    reason: str | None = None
    case_required: bool = False


class GiftRiskEvaluator(Protocol):
    def evaluate(
        self,
        *,
        sender_user_id: str,
        recipient_creator_id: str,
        live_session_id: str,
        total_coin_cost: int,
    ) -> RiskDecision:
        ...


class BaselineGiftRiskEvaluator:
    """Safe deterministic baseline while Chat 10/risk adapters are integrated.

    It blocks only an exact canonical identity self-gift. Richer device/payment/link analysis
    belongs behind this interface and must not be inferred by the client.
    """

    def evaluate(self, *, sender_user_id: str, recipient_creator_id: str, live_session_id: str, total_coin_cost: int) -> RiskDecision:
        if sender_user_id == recipient_creator_id:
            return RiskDecision("block", "SELF_GIFT_CANONICAL_IDENTITY", True)
        return RiskDecision("allow")


@dataclass(frozen=True)
class VerifiedPaymentEvent:
    provider: str
    provider_event_id: str
    provider_payment_id: str
    purchase_id: str
    event_type: str  # confirmed | refunded | chargeback | dispute_won
    verified: bool
    occurred_at: str
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class EligibilityDecision:
    eligible: bool
    code: str = "eligible"
    reason: str | None = None


class EconomyEligibilityDirectory(Protocol):
    def check(
        self,
        *,
        feature: str,
        user_id: str | None = None,
        creator_recipient_id: str | None = None,
        live_session_id: str | None = None,
    ) -> EligibilityDecision:
        ...


class UnavailableEconomyEligibilityDirectory:
    """Fail-closed compatibility adapter until canonical age/region policy lands."""

    def check(
        self,
        *,
        feature: str,
        user_id: str | None = None,
        creator_recipient_id: str | None = None,
        live_session_id: str | None = None,
    ) -> EligibilityDecision:
        return EligibilityDecision(
            False,
            "ELIGIBILITY_POLICY_UNAVAILABLE",
            "Canonical age/region eligibility policy is not configured.",
        )


class CosmicEconomy:
    """Server-authoritative Cosmic Creation Coin and Shared Sky LIVE Gift economy.

    The append-only coin ledger is financial truth. `coin_accounts.available_balance` and
    `recovery_debt` are transactional materialisations: available - recovery_debt must
    reconcile to the ledger sum. Ordinary spending never creates a negative available
    balance; verified refund/chargeback recovery may create explicit recovery debt.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        live_sessions: LiveSessionDirectory | None = None,
        risk: GiftRiskEvaluator | None = None,
        eligibility: EconomyEligibilityDirectory | None = None,
    ):
        configured = db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3"
        self.db_path = Path(configured)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.live_sessions = live_sessions or UnavailableLiveSessionDirectory()
        self.risk = risk or BaselineGiftRiskEvaluator()
        self.eligibility = eligibility or UnavailableEconomyEligibilityDirectory()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS coin_accounts (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN ('active','frozen','restricted','closed')),
                    available_balance INTEGER NOT NULL DEFAULT 0 CHECK(available_balance >= 0),
                    held_balance INTEGER NOT NULL DEFAULT 0 CHECK(held_balance >= 0),
                    recovery_debt INTEGER NOT NULL DEFAULT 0 CHECK(recovery_debt >= 0),
                    ledger_version INTEGER NOT NULL DEFAULT 0 CHECK(ledger_version >= 0),
                    region TEXT,
                    purchase_currency TEXT,
                    risk_status TEXT NOT NULL DEFAULT 'normal',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS coin_ledger_entries (
                    id TEXT PRIMARY KEY,
                    coin_account_id TEXT NOT NULL,
                    entry_type TEXT NOT NULL,
                    coin_delta INTEGER NOT NULL CHECK(coin_delta != 0),
                    source TEXT NOT NULL,
                    purchase_id TEXT,
                    gift_transaction_id TEXT,
                    reversal_id TEXT,
                    promotion_id TEXT,
                    live_session_id TEXT,
                    creator_recipient_id TEXT,
                    idempotency_key TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    resulting_available_balance INTEGER NOT NULL CHECK(resulting_available_balance >= 0),
                    resulting_recovery_debt INTEGER NOT NULL CHECK(resulting_recovery_debt >= 0),
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(coin_account_id) REFERENCES coin_accounts(id),
                    UNIQUE(coin_account_id, idempotency_key, entry_type)
                );
                CREATE INDEX IF NOT EXISTS idx_coin_ledger_account_time
                    ON coin_ledger_entries(coin_account_id, recorded_at DESC);
                CREATE INDEX IF NOT EXISTS idx_coin_ledger_purchase
                    ON coin_ledger_entries(purchase_id) WHERE purchase_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_coin_ledger_gift
                    ON coin_ledger_entries(gift_transaction_id) WHERE gift_transaction_id IS NOT NULL;

                CREATE TRIGGER IF NOT EXISTS coin_ledger_entries_no_update
                BEFORE UPDATE ON coin_ledger_entries
                BEGIN
                    SELECT RAISE(ABORT, 'coin ledger is append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS coin_ledger_entries_no_delete
                BEFORE DELETE ON coin_ledger_entries
                BEGIN
                    SELECT RAISE(ABORT, 'coin ledger is append-only');
                END;

                CREATE TABLE IF NOT EXISTS coin_packs (
                    pack_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version > 0),
                    display_name TEXT NOT NULL,
                    coin_quantity INTEGER NOT NULL CHECK(coin_quantity > 0),
                    fiat_amount_minor INTEGER NOT NULL CHECK(fiat_amount_minor > 0),
                    fiat_currency TEXT NOT NULL,
                    regions_json TEXT NOT NULL DEFAULT '[]',
                    eligibility_json TEXT NOT NULL DEFAULT '{}',
                    promotion_ref TEXT,
                    starts_at TEXT,
                    ends_at TEXT,
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    is_baseline INTEGER NOT NULL DEFAULT 0 CHECK(is_baseline IN (0,1)),
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    approved_by TEXT,
                    approval_reference TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(pack_id, version)
                );

                CREATE TABLE IF NOT EXISTS coin_purchases (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    coin_account_id TEXT NOT NULL,
                    pack_id TEXT NOT NULL,
                    pack_version INTEGER NOT NULL,
                    coin_quantity INTEGER NOT NULL CHECK(coin_quantity > 0),
                    fiat_amount_minor INTEGER NOT NULL CHECK(fiat_amount_minor > 0),
                    fiat_currency TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_payment_id TEXT,
                    status TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    confirmed_at TEXT,
                    reversed_at TEXT,
                    ledger_credit_id TEXT,
                    reversal_ledger_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(coin_account_id) REFERENCES coin_accounts(id),
                    UNIQUE(user_id, idempotency_key),
                    UNIQUE(provider, provider_payment_id)
                );
                CREATE INDEX IF NOT EXISTS idx_coin_purchase_account_time
                    ON coin_purchases(coin_account_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS payment_webhook_events (
                    provider TEXT NOT NULL,
                    provider_event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    purchase_id TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    PRIMARY KEY(provider, provider_event_id)
                );

                CREATE TABLE IF NOT EXISTS gift_definitions (
                    gift_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version > 0),
                    display_name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    coin_cost INTEGER NOT NULL CHECK(coin_cost > 0),
                    asset_ref TEXT,
                    reduced_motion_asset_ref TEXT,
                    sound_ref TEXT,
                    category TEXT NOT NULL DEFAULT 'support',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    starts_at TEXT,
                    ends_at TEXT,
                    regions_json TEXT NOT NULL DEFAULT '[]',
                    eligibility_json TEXT NOT NULL DEFAULT '{}',
                    battle_eligible INTEGER NOT NULL DEFAULT 1 CHECK(battle_eligible IN (0,1)),
                    moderation_json TEXT NOT NULL DEFAULT '{}',
                    approved_by TEXT,
                    approval_reference TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(gift_id, version)
                );

                CREATE TABLE IF NOT EXISTS gift_transactions (
                    id TEXT PRIMARY KEY,
                    sender_user_id TEXT NOT NULL,
                    sender_coin_account_id TEXT NOT NULL,
                    recipient_creator_id TEXT NOT NULL,
                    live_session_id TEXT NOT NULL,
                    battle_id TEXT,
                    battle_round_id TEXT,
                    gift_id TEXT NOT NULL,
                    gift_version INTEGER NOT NULL,
                    unit_coin_cost INTEGER NOT NULL CHECK(unit_coin_cost > 0),
                    quantity INTEGER NOT NULL CHECK(quantity > 0),
                    total_coin_cost INTEGER NOT NULL CHECK(total_coin_cost > 0),
                    status TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    ledger_debit_id TEXT NOT NULL,
                    creator_receipt_id TEXT NOT NULL,
                    risk_state TEXT NOT NULL DEFAULT 'allowed',
                    correlation_id TEXT NOT NULL,
                    committed_at TEXT NOT NULL,
                    reversed_at TEXT,
                    reversal_ledger_id TEXT,
                    reversal_reason TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(sender_coin_account_id) REFERENCES coin_accounts(id),
                    UNIQUE(sender_coin_account_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_gift_sender_time
                    ON gift_transactions(sender_coin_account_id, committed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_gift_creator_time
                    ON gift_transactions(recipient_creator_id, committed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_gift_live_time
                    ON gift_transactions(live_session_id, committed_at DESC);

                CREATE TABLE IF NOT EXISTS creator_gift_receipts (
                    id TEXT PRIMARY KEY,
                    creator_recipient_id TEXT NOT NULL,
                    gift_transaction_id TEXT NOT NULL UNIQUE,
                    live_session_id TEXT NOT NULL,
                    gift_id TEXT NOT NULL,
                    gift_version INTEGER NOT NULL,
                    coin_value INTEGER NOT NULL CHECK(coin_value > 0),
                    status TEXT NOT NULL,
                    hold_reason TEXT,
                    payout_policy_id TEXT,
                    payout_policy_version INTEGER,
                    payable_amount_minor INTEGER,
                    payable_currency TEXT,
                    statement_period TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    adjustment_reference TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_creator_receipts_creator_time
                    ON creator_gift_receipts(creator_recipient_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS gift_payout_policies (
                    policy_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)),
                    effective_at TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    approved_by TEXT NOT NULL,
                    approval_reference TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(policy_id, version)
                );

                CREATE TABLE IF NOT EXISTS account_spending_limits (
                    user_id TEXT PRIMARY KEY,
                    daily_hard_limit INTEGER,
                    weekly_hard_limit INTEGER,
                    monthly_hard_limit INTEGER,
                    daily_warning INTEGER,
                    weekly_warning INTEGER,
                    monthly_warning INTEGER,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    reason TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_feature_flags (
                    name TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    reason TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_outbox (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    aggregate_type TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL,
                    live_session_id TEXT,
                    payload_json TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    published_at TEXT,
                    publish_attempts INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_economy_outbox_pending
                    ON economy_outbox(published_at, created_at);

                CREATE TABLE IF NOT EXISTS economy_risk_cases (
                    id TEXT PRIMARY KEY,
                    subject_user_id TEXT,
                    creator_recipient_id TEXT,
                    purchase_id TEXT,
                    gift_transaction_id TEXT,
                    live_session_id TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    signal_code TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    reviewer TEXT,
                    decision TEXT,
                    decision_reason TEXT,
                    appeal_status TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_reconciliation_discrepancies (
                    id TEXT PRIMARY KEY,
                    discrepancy_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    expected_json TEXT NOT NULL,
                    actual_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    resolution_note TEXT,
                    UNIQUE(discrepancy_type, subject_id, status)
                );
                """
            )
            now = _iso()
            con.execute(
                """INSERT OR IGNORE INTO coin_packs
                   (pack_id,version,display_name,coin_quantity,fiat_amount_minor,fiat_currency,
                    active,is_baseline,sort_order,approved_by,approval_reference,created_at)
                   VALUES (?,?,?,?,?,?,1,1,10,'authoritative_spec','economy-master-spec',?)""",
                (BASELINE_PACK_ID, BASELINE_PACK_VERSION, "1,000 Cosmic Creation Coins",
                 BASELINE_COINS, BASELINE_PRICE_MINOR, BASELINE_CURRENCY, now),
            )
            # Original first-party seed gift; presentation assets may be attached/versioned later.
            con.execute(
                """INSERT OR IGNORE INTO gift_definitions
                   (gift_id,version,display_name,description,coin_cost,category,active,battle_eligible,
                    approved_by,approval_reference,created_at)
                   VALUES ('starlight-spark',1,'Starlight Spark',
                           'An original Shared Sky burst of support.',10,'support',1,1,
                           'authoritative_spec','chat5-original-catalogue',?)""",
                (now,),
            )
            for name, enabled in FEATURE_FLAGS.items():
                con.execute(
                    """INSERT OR IGNORE INTO economy_feature_flags(name,enabled,updated_at,updated_by,reason)
                       VALUES (?,?,?,?,?)""",
                    (name, int(enabled), now, "system", "Chat 5 safe default"),
                )

    def _require_eligible(
        self,
        *,
        feature: str,
        user_id: str | None = None,
        creator_recipient_id: str | None = None,
        live_session_id: str | None = None,
    ) -> None:
        decision = self.eligibility.check(
            feature=feature,
            user_id=user_id,
            creator_recipient_id=creator_recipient_id,
            live_session_id=live_session_id,
        )
        if decision.eligible:
            return
        raise EconomyError(
            decision.code or "ECONOMY_INELIGIBLE",
            decision.reason or "This account is not eligible for this economy action.",
            status_code=503 if decision.code == "ELIGIBILITY_POLICY_UNAVAILABLE" else 403,
        )

    def _begin(self, con: sqlite3.Connection) -> None:
        con.execute("BEGIN IMMEDIATE")

    def _flag_locked(self, con: sqlite3.Connection, name: str) -> bool:
        row = con.execute("SELECT enabled FROM economy_feature_flags WHERE name=?", (name,)).fetchone()
        return bool(row and row["enabled"])

    def set_feature_flag(self, name: str, enabled: bool, *, actor: str, reason: str) -> dict:
        if not name or len(reason.strip()) < 3:
            raise EconomyError("INVALID_OWNER_ACTION", "A feature name and reason are required.")
        with self._connect() as con:
            self._begin(con)
            con.execute(
                """INSERT INTO economy_feature_flags(name,enabled,updated_at,updated_by,reason)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(name) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at,
                   updated_by=excluded.updated_by,reason=excluded.reason""",
                (name, int(enabled), _iso(), actor[:160], reason.strip()[:500]),
            )
            con.commit()
        return {"name": name, "enabled": enabled}

    def ensure_account(self, user_id: str, *, region: str | None = None, currency: str | None = None) -> dict:
        user_id = (user_id or "").strip()
        if not user_id:
            raise EconomyError("INVALID_USER", "A canonical user ID is required.")
        now = _iso()
        with self._connect() as con:
            self._begin(con)
            row = con.execute("SELECT * FROM coin_accounts WHERE user_id=?", (user_id,)).fetchone()
            if not row:
                account_id = uuid4().hex
                con.execute(
                    """INSERT INTO coin_accounts
                       (id,user_id,status,available_balance,held_balance,recovery_debt,ledger_version,
                        region,purchase_currency,risk_status,created_at,updated_at)
                       VALUES (?,?,'active',0,0,0,0,?,?, 'normal',?,?)""",
                    (account_id, user_id, region, (currency or BASELINE_CURRENCY).upper(), now, now),
                )
                row = con.execute("SELECT * FROM coin_accounts WHERE id=?", (account_id,)).fetchone()
            con.commit()
        return dict(row)

    def get_account(self, user_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM coin_accounts WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_balance(self, user_id: str) -> dict:
        account = self.ensure_account(user_id)
        return {
            "account_id": account["id"],
            "available_coins": int(account["available_balance"]),
            "held_coins": int(account["held_balance"]),
            "recovery_debt_coins": int(account["recovery_debt"]),
            "ledger_version": int(account["ledger_version"]),
            "status": account["status"],
        }

    def _get_account_locked(self, con: sqlite3.Connection, user_id: str) -> sqlite3.Row:
        row = con.execute("SELECT * FROM coin_accounts WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            now = _iso()
            account_id = uuid4().hex
            con.execute(
                """INSERT INTO coin_accounts
                   (id,user_id,status,available_balance,held_balance,recovery_debt,ledger_version,
                    purchase_currency,risk_status,created_at,updated_at)
                   VALUES (?,?,'active',0,0,0,0,?,'normal',?,?)""",
                (account_id, user_id, BASELINE_CURRENCY, now, now),
            )
            row = con.execute("SELECT * FROM coin_accounts WHERE id=?", (account_id,)).fetchone()
        assert row is not None
        return row

    def _append_ledger_locked(
        self,
        con: sqlite3.Connection,
        *,
        account: sqlite3.Row,
        entry_type: str,
        coin_delta: int,
        source: str,
        idempotency_key: str,
        correlation_id: str,
        actor_id: str,
        purchase_id: str | None = None,
        gift_transaction_id: str | None = None,
        reversal_id: str | None = None,
        promotion_id: str | None = None,
        live_session_id: str | None = None,
        creator_recipient_id: str | None = None,
        metadata: dict | None = None,
        allow_recovery_debt: bool = False,
    ) -> dict:
        if entry_type not in LEDGER_ENTRY_TYPES:
            raise EconomyError("INVALID_LEDGER_ENTRY_TYPE", "Unsupported ledger entry type.")
        if not isinstance(coin_delta, int) or isinstance(coin_delta, bool) or coin_delta == 0:
            raise EconomyError("INVALID_COIN_AMOUNT", "Coin ledger quantities must be non-zero integers.")

        duplicate = con.execute(
            """SELECT * FROM coin_ledger_entries
               WHERE coin_account_id=? AND idempotency_key=? AND entry_type=?""",
            (account["id"], idempotency_key, entry_type),
        ).fetchone()
        if duplicate:
            return dict(duplicate)

        available = int(account["available_balance"])
        debt = int(account["recovery_debt"])
        if coin_delta > 0:
            debt_payment = min(debt, coin_delta)
            debt -= debt_payment
            available += coin_delta - debt_payment
        else:
            debit = -coin_delta
            if debit > available and not allow_recovery_debt:
                raise EconomyError(
                    "INSUFFICIENT_COIN_BALANCE",
                    "Insufficient Cosmic Creation Coin balance.",
                    status_code=409,
                    details={"available_coins": available, "required_coins": debit},
                )
            consumed = min(available, debit)
            available -= consumed
            shortfall = debit - consumed
            if shortfall:
                debt += shortfall

        ledger_id = uuid4().hex
        now = _iso()
        con.execute(
            """INSERT INTO coin_ledger_entries
               (id,coin_account_id,entry_type,coin_delta,source,purchase_id,gift_transaction_id,
                reversal_id,promotion_id,live_session_id,creator_recipient_id,idempotency_key,
                correlation_id,actor_id,occurred_at,recorded_at,resulting_available_balance,
                resulting_recovery_debt,metadata_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ledger_id, account["id"], entry_type, coin_delta, source, purchase_id,
                gift_transaction_id, reversal_id, promotion_id, live_session_id,
                creator_recipient_id, idempotency_key, correlation_id, actor_id, now, now,
                available, debt, _json(metadata),
            ),
        )
        con.execute(
            """UPDATE coin_accounts
               SET available_balance=?,recovery_debt=?,ledger_version=ledger_version+1,updated_at=?
               WHERE id=?""",
            (available, debt, now, account["id"]),
        )
        row = con.execute("SELECT * FROM coin_ledger_entries WHERE id=?", (ledger_id,)).fetchone()
        assert row is not None
        return dict(row)

    def owner_adjustment(
        self,
        *,
        user_id: str,
        coin_delta: int,
        actor: str,
        reason: str,
        reference: str,
        idempotency_key: str,
    ) -> dict:
        if not isinstance(coin_delta, int) or isinstance(coin_delta, bool) or coin_delta == 0:
            raise EconomyError("INVALID_COIN_AMOUNT", "Adjustment must be a non-zero integer Coin quantity.")
        if len((reason or "").strip()) < 3 or len((reference or "").strip()) < 2:
            raise EconomyError("INVALID_OWNER_ACTION", "Owner adjustment requires a reason and reference.")
        correlation_id = uuid4().hex
        with self._connect() as con:
            self._begin(con)
            account = self._get_account_locked(con, user_id)
            entry = self._append_ledger_locked(
                con,
                account=account,
                entry_type=OWNER_APPROVED_ADJUSTMENT_CREDIT if coin_delta > 0 else OWNER_APPROVED_ADJUSTMENT_DEBIT,
                coin_delta=coin_delta,
                source="owner_adjustment",
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
                actor_id=actor,
                metadata={"reason": reason, "reference": reference},
                allow_recovery_debt=coin_delta < 0,
            )
            self._enqueue_locked(
                con, "economy.owner_adjustment", "coin_account", account["id"],
                {"ledger_entry_id": entry["id"], "coin_delta": coin_delta, "reason": reason, "reference": reference},
                correlation_id=correlation_id,
            )
            con.commit()
        return entry

    def promotional_credit(
        self,
        *,
        user_id: str,
        coin_quantity: int,
        campaign_ref: str,
        idempotency_key: str,
        actor: str = "system",
    ) -> dict:
        if not isinstance(coin_quantity, int) or isinstance(coin_quantity, bool) or coin_quantity <= 0:
            raise EconomyError("INVALID_COIN_AMOUNT", "Promotional credit must be a positive integer Coin quantity.")
        correlation_id = uuid4().hex
        with self._connect() as con:
            self._begin(con)
            account = self._get_account_locked(con, user_id)
            entry = self._append_ledger_locked(
                con, account=account, entry_type=PROMOTIONAL_CREDIT, coin_delta=coin_quantity,
                source="promotion", idempotency_key=idempotency_key, correlation_id=correlation_id,
                actor_id=actor, promotion_id=campaign_ref, metadata={"campaign_ref": campaign_ref},
            )
            con.commit()
        return entry

    def list_packs(self, *, currency: str | None = None, active_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM coin_packs WHERE 1=1"
        params: list[Any] = []
        if active_only:
            sql += " AND active=1"
        if currency:
            sql += " AND fiat_currency=?"
            params.append(currency.upper())
        sql += " ORDER BY sort_order, coin_quantity, version"
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["effective_minor_per_1000_coins"] = (item["fiat_amount_minor"] * 1000) // item["coin_quantity"]
            result.append(item)
        return result

    def publish_pack(
        self,
        *,
        pack_id: str,
        version: int,
        display_name: str,
        coin_quantity: int,
        fiat_amount_minor: int,
        fiat_currency: str,
        actor: str,
        approval_reference: str,
        active: bool = True,
        regions: list[str] | None = None,
        promotion_ref: str | None = None,
        sort_order: int = 0,
    ) -> dict:
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (version, coin_quantity, fiat_amount_minor)):
            raise EconomyError("INVALID_FINANCIAL_VALUE", "Pack version, Coins, and minor-unit price must be integers.")
        if version <= 0 or coin_quantity <= 0 or fiat_amount_minor <= 0:
            raise EconomyError("INVALID_FINANCIAL_VALUE", "Pack version, Coins, and price must be positive.")
        now = _iso()
        with self._connect() as con:
            self._begin(con)
            try:
                con.execute(
                    """INSERT INTO coin_packs
                       (pack_id,version,display_name,coin_quantity,fiat_amount_minor,fiat_currency,
                        regions_json,promotion_ref,active,is_baseline,sort_order,approved_by,approval_reference,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,0,?,?,?,?)""",
                    (pack_id, version, display_name, coin_quantity, fiat_amount_minor, fiat_currency.upper(),
                     _json(regions or []), promotion_ref, int(active), sort_order, actor, approval_reference, now),
                )
            except sqlite3.IntegrityError as exc:
                raise EconomyError("PACK_VERSION_EXISTS", "This Coin pack version already exists.", status_code=409) from exc
            con.commit()
        return self.get_pack(pack_id, version)

    def get_pack(self, pack_id: str, version: int) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM coin_packs WHERE pack_id=? AND version=?", (pack_id, version)).fetchone()
        if not row:
            raise EconomyError("COIN_PACK_UNAVAILABLE", "Coin pack is unavailable.", status_code=404)
        return dict(row)

    def create_purchase(
        self,
        *,
        user_id: str,
        pack_id: str,
        pack_version: int,
        provider: str,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> dict:
        """Create a pending purchase from server catalogue data.

        This does not credit Coins. A verified provider event must call
        `apply_verified_payment_event(... event_type="confirmed")`.
        """
        if not idempotency_key or len(idempotency_key) > 200:
            raise EconomyError("INVALID_IDEMPOTENCY_KEY", "A bounded idempotency key is required.")
        self._require_eligible(feature="coin_purchase", user_id=user_id)
        correlation_id = correlation_id or uuid4().hex
        request = {"pack_id": pack_id, "pack_version": pack_version, "provider": provider}
        fp = _fingerprint(request)
        with self._connect() as con:
            self._begin(con)
            if not self._flag_locked(con, "coin_purchases_enabled"):
                raise EconomyError("COIN_PURCHASES_DISABLED", "Cosmic Creation Coin purchases are temporarily unavailable.", status_code=503)
            account = self._get_account_locked(con, user_id)
            if account["status"] != "active":
                raise EconomyError("COIN_ACCOUNT_RESTRICTED", "Coin account is not eligible for purchases.", status_code=403)
            existing = con.execute(
                "SELECT * FROM coin_purchases WHERE user_id=? AND idempotency_key=?",
                (user_id, idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_fingerprint"] != fp:
                    raise EconomyError("IDEMPOTENCY_KEY_REUSED", "Idempotency key was already used for a different purchase.", status_code=409)
                con.commit()
                return dict(existing)

            pack = con.execute(
                "SELECT * FROM coin_packs WHERE pack_id=? AND version=? AND active=1",
                (pack_id, pack_version),
            ).fetchone()
            if not pack:
                raise EconomyError("COIN_PACK_UNAVAILABLE", "Coin pack is unavailable.", status_code=404)
            now = _iso()
            purchase_id = uuid4().hex
            con.execute(
                """INSERT INTO coin_purchases
                   (id,user_id,coin_account_id,pack_id,pack_version,coin_quantity,fiat_amount_minor,
                    fiat_currency,provider,status,idempotency_key,request_fingerprint,correlation_id,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?,?,?)""",
                (purchase_id, user_id, account["id"], pack["pack_id"], pack["version"],
                 pack["coin_quantity"], pack["fiat_amount_minor"], pack["fiat_currency"], provider,
                 idempotency_key, fp, correlation_id, now),
            )
            row = con.execute("SELECT * FROM coin_purchases WHERE id=?", (purchase_id,)).fetchone()
            con.commit()
        return dict(row)

    def bind_provider_payment(self, purchase_id: str, *, provider_payment_id: str) -> dict:
        """Bind the provider's checkout/payment identifier before any credit can happen."""
        with self._connect() as con:
            self._begin(con)
            row = con.execute("SELECT * FROM coin_purchases WHERE id=?", (purchase_id,)).fetchone()
            if not row:
                raise EconomyError("PURCHASE_NOT_FOUND", "Coin purchase not found.", status_code=404)
            if row["provider_payment_id"] and row["provider_payment_id"] != provider_payment_id:
                raise EconomyError("PAYMENT_REFERENCE_CONFLICT", "Purchase is already bound to another provider payment.", status_code=409)
            try:
                con.execute("UPDATE coin_purchases SET provider_payment_id=? WHERE id=?", (provider_payment_id, purchase_id))
            except sqlite3.IntegrityError as exc:
                raise EconomyError("PAYMENT_REFERENCE_REUSED", "Provider payment reference is already in use.", status_code=409) from exc
            row = con.execute("SELECT * FROM coin_purchases WHERE id=?", (purchase_id,)).fetchone()
            con.commit()
        return dict(row)

    def apply_verified_payment_event(self, event: VerifiedPaymentEvent) -> dict:
        if not event.verified:
            raise EconomyError("INVALID_PAYMENT_WEBHOOK", "Payment event authenticity could not be verified.", status_code=401)
        if event.event_type not in {"confirmed", "refunded", "chargeback", "dispute_won"}:
            raise EconomyError("UNSUPPORTED_PAYMENT_EVENT", "Unsupported payment event type.")
        payload_hash = _fingerprint({
            "provider": event.provider, "provider_event_id": event.provider_event_id,
            "provider_payment_id": event.provider_payment_id, "purchase_id": event.purchase_id,
            "event_type": event.event_type, "occurred_at": event.occurred_at,
        })
        with self._connect() as con:
            self._begin(con)
            seen = con.execute(
                "SELECT * FROM payment_webhook_events WHERE provider=? AND provider_event_id=?",
                (event.provider, event.provider_event_id),
            ).fetchone()
            if seen:
                purchase = con.execute("SELECT * FROM coin_purchases WHERE id=?", (seen["purchase_id"],)).fetchone()
                con.commit()
                return {"idempotent_replay": True, "purchase": dict(purchase) if purchase else None}

            purchase = con.execute("SELECT * FROM coin_purchases WHERE id=?", (event.purchase_id,)).fetchone()
            if not purchase:
                raise EconomyError("PURCHASE_NOT_FOUND", "Coin purchase not found.", status_code=404)
            if purchase["provider"] != event.provider:
                raise EconomyError("PAYMENT_PROVIDER_MISMATCH", "Payment provider does not match purchase.", status_code=409)
            if purchase["provider_payment_id"] and purchase["provider_payment_id"] != event.provider_payment_id:
                raise EconomyError("PAYMENT_REFERENCE_CONFLICT", "Payment reference does not match purchase.", status_code=409)
            if not purchase["provider_payment_id"]:
                con.execute("UPDATE coin_purchases SET provider_payment_id=? WHERE id=?", (event.provider_payment_id, purchase["id"]))

            con.execute(
                """INSERT INTO payment_webhook_events
                   (provider,provider_event_id,event_type,purchase_id,received_at,payload_hash)
                   VALUES (?,?,?,?,?,?)""",
                (event.provider, event.provider_event_id, event.event_type, purchase["id"], _iso(), payload_hash),
            )
            account = con.execute("SELECT * FROM coin_accounts WHERE id=?", (purchase["coin_account_id"],)).fetchone()
            assert account is not None

            if event.event_type == "confirmed":
                if purchase["status"] == "confirmed":
                    con.commit()
                    return {"idempotent_replay": True, "purchase": dict(purchase)}
                if purchase["status"] in {"refunded", "chargeback"}:
                    raise EconomyError("PURCHASE_ALREADY_REVERSED", "Reversed purchase cannot be confirmed again.", status_code=409)
                entry = self._append_ledger_locked(
                    con, account=account, entry_type=PURCHASE_CREDIT, coin_delta=int(purchase["coin_quantity"]),
                    source="verified_payment", purchase_id=purchase["id"],
                    idempotency_key=f"purchase-credit:{purchase['id']}", correlation_id=purchase["correlation_id"],
                    actor_id=f"payment_provider:{event.provider}",
                    metadata={"provider_payment_id": event.provider_payment_id},
                )
                con.execute(
                    """UPDATE coin_purchases SET status='confirmed',confirmed_at=?,ledger_credit_id=?,
                       provider_payment_id=? WHERE id=?""",
                    (event.occurred_at or _iso(), entry["id"], event.provider_payment_id, purchase["id"]),
                )
                self._enqueue_locked(
                    con, "coin.purchase_confirmed", "coin_purchase", purchase["id"],
                    {"purchase_id": purchase["id"], "coin_quantity": purchase["coin_quantity"],
                     "fiat_amount_minor": purchase["fiat_amount_minor"], "fiat_currency": purchase["fiat_currency"]},
                    correlation_id=purchase["correlation_id"],
                )

            elif event.event_type in {"refunded", "chargeback"}:
                if purchase["status"] not in {"confirmed", "refunded", "chargeback"}:
                    raise EconomyError("PURCHASE_NOT_CONFIRMED", "Only a confirmed purchase can be reversed.", status_code=409)
                target = "refunded" if event.event_type == "refunded" else "chargeback"
                entry_type = REFUND_DEBIT if target == "refunded" else CHARGEBACK_DEBIT
                if purchase["status"] == target:
                    con.commit()
                    return {"idempotent_replay": True, "purchase": dict(purchase)}
                if purchase["status"] in {"refunded", "chargeback"} and purchase["status"] != target:
                    raise EconomyError("PURCHASE_REVERSAL_CONFLICT", "Purchase already has a different final reversal state.", status_code=409)
                entry = self._append_ledger_locked(
                    con, account=account, entry_type=entry_type, coin_delta=-int(purchase["coin_quantity"]),
                    source=f"verified_payment_{target}", purchase_id=purchase["id"],
                    reversal_id=event.provider_event_id,
                    idempotency_key=f"{target}:{purchase['id']}", correlation_id=purchase["correlation_id"],
                    actor_id=f"payment_provider:{event.provider}", allow_recovery_debt=True,
                    metadata={"provider_payment_id": event.provider_payment_id},
                )
                con.execute(
                    "UPDATE coin_purchases SET status=?,reversed_at=?,reversal_ledger_id=? WHERE id=?",
                    (target, event.occurred_at or _iso(), entry["id"], purchase["id"]),
                )
                self._enqueue_locked(
                    con, f"coin.purchase_{target}", "coin_purchase", purchase["id"],
                    {"purchase_id": purchase["id"], "ledger_entry_id": entry["id"]},
                    correlation_id=purchase["correlation_id"],
                )
            elif event.event_type == "dispute_won":
                # Recovery of a chargeback is represented by a new purchase credit; history remains intact.
                if purchase["status"] != "chargeback":
                    raise EconomyError("DISPUTE_STATE_CONFLICT", "Dispute can only be restored from chargeback state.", status_code=409)
                entry = self._append_ledger_locked(
                    con, account=account, entry_type=PURCHASE_CREDIT, coin_delta=int(purchase["coin_quantity"]),
                    source="verified_dispute_won", purchase_id=purchase["id"],
                    idempotency_key=f"dispute-won:{purchase['id']}", correlation_id=purchase["correlation_id"],
                    actor_id=f"payment_provider:{event.provider}",
                    metadata={"restores_chargeback_event": event.provider_event_id},
                )
                con.execute(
                    "UPDATE coin_purchases SET status='confirmed',reversed_at=NULL,reversal_ledger_id=NULL WHERE id=?",
                    (purchase["id"],),
                )
                self._enqueue_locked(
                    con, "coin.dispute_won", "coin_purchase", purchase["id"],
                    {"purchase_id": purchase["id"], "ledger_entry_id": entry["id"]},
                    correlation_id=purchase["correlation_id"],
                )

            purchase = con.execute("SELECT * FROM coin_purchases WHERE id=?", (purchase["id"],)).fetchone()
            con.commit()
        return {"idempotent_replay": False, "purchase": dict(purchase)}

    def publish_gift_definition(
        self,
        *,
        gift_id: str,
        version: int,
        display_name: str,
        description: str,
        coin_cost: int,
        actor: str,
        approval_reference: str,
        active: bool = True,
        category: str = "support",
        asset_ref: str | None = None,
        reduced_motion_asset_ref: str | None = None,
        sound_ref: str | None = None,
        battle_eligible: bool = True,
        regions: list[str] | None = None,
    ) -> dict:
        if isinstance(version, bool) or isinstance(coin_cost, bool) or not isinstance(version, int) or not isinstance(coin_cost, int):
            raise EconomyError("INVALID_FINANCIAL_VALUE", "Gift version and Coin cost must be integers.")
        if version <= 0 or coin_cost <= 0:
            raise EconomyError("INVALID_FINANCIAL_VALUE", "Gift version and Coin cost must be positive.")
        now = _iso()
        with self._connect() as con:
            self._begin(con)
            try:
                con.execute(
                    """INSERT INTO gift_definitions
                       (gift_id,version,display_name,description,coin_cost,asset_ref,reduced_motion_asset_ref,
                        sound_ref,category,active,regions_json,battle_eligible,approved_by,approval_reference,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (gift_id, version, display_name, description, coin_cost, asset_ref, reduced_motion_asset_ref,
                     sound_ref, category, int(active), _json(regions or []), int(battle_eligible),
                     actor, approval_reference, now),
                )
            except sqlite3.IntegrityError as exc:
                raise EconomyError("GIFT_VERSION_EXISTS", "This Gift version already exists.", status_code=409) from exc
            con.commit()
        return self.get_gift(gift_id, version)

    def get_gift(self, gift_id: str, version: int) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM gift_definitions WHERE gift_id=? AND version=?", (gift_id, version)).fetchone()
        if not row:
            raise EconomyError("GIFT_UNAVAILABLE", "Gift is unavailable.", status_code=404)
        return dict(row)

    def list_gifts(self, *, active_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM gift_definitions" + (" WHERE active=1" if active_only else "") + " ORDER BY coin_cost,display_name,version"
        with self._connect() as con:
            rows = con.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def set_spending_limits(
        self,
        user_id: str,
        *,
        actor: str,
        reason: str,
        daily_hard_limit: int | None = None,
        weekly_hard_limit: int | None = None,
        monthly_hard_limit: int | None = None,
        daily_warning: int | None = None,
        weekly_warning: int | None = None,
        monthly_warning: int | None = None,
    ) -> dict:
        values = [daily_hard_limit, weekly_hard_limit, monthly_hard_limit, daily_warning, weekly_warning, monthly_warning]
        if any(v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0) for v in values):
            raise EconomyError("INVALID_SPENDING_LIMIT", "Spending limits must be non-negative integer Coin quantities.")
        with self._connect() as con:
            self._begin(con)
            con.execute(
                """INSERT INTO account_spending_limits
                   (user_id,daily_hard_limit,weekly_hard_limit,monthly_hard_limit,daily_warning,
                    weekly_warning,monthly_warning,updated_at,updated_by,reason)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                    daily_hard_limit=excluded.daily_hard_limit,weekly_hard_limit=excluded.weekly_hard_limit,
                    monthly_hard_limit=excluded.monthly_hard_limit,daily_warning=excluded.daily_warning,
                    weekly_warning=excluded.weekly_warning,monthly_warning=excluded.monthly_warning,
                    updated_at=excluded.updated_at,updated_by=excluded.updated_by,reason=excluded.reason""",
                (user_id, daily_hard_limit, weekly_hard_limit, monthly_hard_limit, daily_warning,
                 weekly_warning, monthly_warning, _iso(), actor, reason),
            )
            row = con.execute("SELECT * FROM account_spending_limits WHERE user_id=?", (user_id,)).fetchone()
            con.commit()
        return dict(row)

    def _spend_totals_locked(self, con: sqlite3.Connection, account_id: str, now: datetime) -> dict[str, int]:
        day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        week_start = day_start - timedelta(days=day_start.weekday())
        month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        result = {}
        for key, start in (("daily", day_start), ("weekly", week_start), ("monthly", month_start)):
            row = con.execute(
                """SELECT COALESCE(SUM(total_coin_cost),0) AS n FROM gift_transactions
                   WHERE sender_coin_account_id=? AND status='committed' AND committed_at>=?""",
                (account_id, _iso(start)),
            ).fetchone()
            result[key] = int(row["n"] if row else 0)
        return result

    def spending_state(self, user_id: str) -> dict:
        account = self.ensure_account(user_id)
        now = _utcnow()
        with self._connect() as con:
            totals = self._spend_totals_locked(con, account["id"], now)
            limits = con.execute("SELECT * FROM account_spending_limits WHERE user_id=?", (user_id,)).fetchone()
        out = {"spent": totals, "limits": dict(limits) if limits else None, "warnings": []}
        if limits:
            for period in ("daily", "weekly", "monthly"):
                warning = limits[f"{period}_warning"]
                if warning is not None and totals[period] >= int(warning):
                    out["warnings"].append({"period": period, "threshold": int(warning), "spent": totals[period]})
        return out

    def _check_spending_locked(self, con: sqlite3.Connection, user_id: str, account_id: str, new_cost: int) -> None:
        limits = con.execute("SELECT * FROM account_spending_limits WHERE user_id=?", (user_id,)).fetchone()
        if not limits:
            return
        totals = self._spend_totals_locked(con, account_id, _utcnow())
        for period in ("daily", "weekly", "monthly"):
            hard = limits[f"{period}_hard_limit"]
            if hard is not None and totals[period] + new_cost > int(hard):
                raise EconomyError(
                    "SPENDING_LIMIT_EXCEEDED",
                    f"{period.capitalize()} Cosmic Creation Coin spending limit would be exceeded.",
                    status_code=403,
                    details={"period": period, "limit_coins": int(hard), "spent_coins": totals[period], "attempted_coins": new_cost},
                )

    def _open_risk_case_locked(
        self,
        con: sqlite3.Connection,
        *,
        signal_code: str,
        subject_user_id: str | None = None,
        creator_recipient_id: str | None = None,
        gift_transaction_id: str | None = None,
        live_session_id: str | None = None,
        evidence: dict | None = None,
    ) -> str:
        case_id = uuid4().hex
        now = _iso()
        con.execute(
            """INSERT INTO economy_risk_cases
               (id,subject_user_id,creator_recipient_id,gift_transaction_id,live_session_id,status,
                signal_code,evidence_json,created_at,updated_at)
               VALUES (?,?,?,?,?,'open',?,?,?,?)""",
            (case_id, subject_user_id, creator_recipient_id, gift_transaction_id, live_session_id,
             signal_code, _json(evidence), now, now),
        )
        return case_id

    def send_gift(
        self,
        *,
        sender_user_id: str,
        recipient_creator_id: str,
        live_session_id: str,
        gift_id: str,
        gift_version: int,
        quantity: int,
        idempotency_key: str,
        battle_id: str | None = None,
        battle_round_id: str | None = None,
    ) -> dict:
        if not idempotency_key or len(idempotency_key) > 200:
            raise EconomyError("INVALID_IDEMPOTENCY_KEY", "A bounded idempotency key is required.")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0 or quantity > 1000:
            raise EconomyError("INVALID_GIFT_QUANTITY", "Gift quantity must be an integer from 1 to 1000.")
        if isinstance(gift_version, bool) or not isinstance(gift_version, int) or gift_version <= 0:
            raise EconomyError("INVALID_GIFT_VERSION", "Gift version must be a positive integer.")

        request_payload = {
            "recipient_creator_id": recipient_creator_id,
            "live_session_id": live_session_id,
            "gift_id": gift_id,
            "gift_version": gift_version,
            "quantity": quantity,
            "battle_id": battle_id,
            "battle_round_id": battle_round_id,
        }
        fp = _fingerprint(request_payload)
        correlation_id = uuid4().hex

        self._require_eligible(
            feature="gift_send", user_id=sender_user_id,
            creator_recipient_id=recipient_creator_id, live_session_id=live_session_id,
        )
        self._require_eligible(
            feature="gift_receive", creator_recipient_id=recipient_creator_id,
            live_session_id=live_session_id,
        )

        # Authoritative live state is checked before DB commit and must itself come from Chat 2/shared state.
        live = self.live_sessions.gift_context(
            live_session_id=live_session_id, recipient_creator_id=recipient_creator_id
        )
        if not live.active or not live.gift_eligible:
            raise EconomyError("LIVE_SESSION_NOT_GIFT_ELIGIBLE", "The Shared Sky LIVE session is not Gift-eligible.", status_code=409)
        if not live.recipient_eligible or live.recipient_creator_id != recipient_creator_id:
            raise EconomyError("CREATOR_NOT_GIFT_ELIGIBLE", "The selected creator is not eligible to receive this Gift in the LIVE session.", status_code=403)
        if battle_id and live.battle_id and battle_id != live.battle_id:
            raise EconomyError("BATTLE_REFERENCE_MISMATCH", "Battle reference does not match the authoritative LIVE context.", status_code=409)
        if battle_round_id and live.battle_round_id and battle_round_id != live.battle_round_id:
            raise EconomyError("BATTLE_REFERENCE_MISMATCH", "Battle round reference does not match the authoritative LIVE context.", status_code=409)

        with self._connect() as con:
            self._begin(con)
            if not self._flag_locked(con, "gift_sends_enabled"):
                raise EconomyError("GIFT_SENDS_DISABLED", "Shared Sky LIVE Gifts are temporarily unavailable.", status_code=503)
            if not self._flag_locked(con, "creator_receiving_enabled"):
                raise EconomyError("GIFT_RECEIVING_DISABLED", "Creator Gift receiving is temporarily unavailable.", status_code=503)

            account = self._get_account_locked(con, sender_user_id)
            existing = con.execute(
                "SELECT * FROM gift_transactions WHERE sender_coin_account_id=? AND idempotency_key=?",
                (account["id"], idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_fingerprint"] != fp:
                    raise EconomyError("IDEMPOTENCY_KEY_REUSED", "Idempotency key was already used for a different Gift request.", status_code=409)
                receipt = con.execute("SELECT * FROM creator_gift_receipts WHERE id=?", (existing["creator_receipt_id"],)).fetchone()
                con.commit()
                return self._gift_result(dict(existing), dict(receipt) if receipt else None, idempotent=True)

            if account["status"] != "active":
                raise EconomyError("COIN_ACCOUNT_RESTRICTED", "Coin account is frozen or restricted.", status_code=403)
            gift = con.execute(
                """SELECT * FROM gift_definitions
                   WHERE gift_id=? AND version=? AND active=1""",
                (gift_id, gift_version),
            ).fetchone()
            if not gift:
                raise EconomyError("GIFT_UNAVAILABLE", "Gift is unavailable.", status_code=404)

            now = _utcnow()
            if gift["starts_at"] and datetime.fromisoformat(gift["starts_at"]) > now:
                raise EconomyError("GIFT_UNAVAILABLE", "Gift is not yet available.", status_code=409)
            if gift["ends_at"] and datetime.fromisoformat(gift["ends_at"]) <= now:
                raise EconomyError("GIFT_UNAVAILABLE", "Gift is no longer available.", status_code=409)

            total = int(gift["coin_cost"]) * quantity
            self._check_spending_locked(con, sender_user_id, account["id"], total)

            risk = self.risk.evaluate(
                sender_user_id=sender_user_id,
                recipient_creator_id=recipient_creator_id,
                live_session_id=live_session_id,
                total_coin_cost=total,
            )
            if risk.action not in {"allow", "monitor", "hold", "block"}:
                raise EconomyError("RISK_ENGINE_ERROR", "Risk evaluator returned an invalid decision.", status_code=503)
            if risk.action in {"hold", "block"}:
                case_id = self._open_risk_case_locked(
                    con, signal_code=risk.reason or "GIFT_RISK_REVIEW",
                    subject_user_id=sender_user_id, creator_recipient_id=recipient_creator_id,
                    live_session_id=live_session_id, evidence={"requested_coin_cost": total},
                )
                con.commit()
                raise EconomyError(
                    "RISK_REVIEW_REQUIRED" if risk.action == "hold" else "GIFT_BLOCKED",
                    "Gift requires review before it can be accepted." if risk.action == "hold" else "Gift cannot be accepted.",
                    status_code=403, details={"case_id": case_id},
                )

            if int(account["available_balance"]) < total:
                raise EconomyError(
                    "INSUFFICIENT_COIN_BALANCE", "Insufficient Cosmic Creation Coin balance.", status_code=409,
                    details={"available_coins": int(account["available_balance"]), "required_coins": total},
                )

            gift_tx_id = uuid4().hex
            receipt_id = uuid4().hex
            debit = self._append_ledger_locked(
                con, account=account, entry_type=GIFT_DEBIT, coin_delta=-total,
                source="shared_sky_live_gift", gift_transaction_id=gift_tx_id,
                idempotency_key=f"gift-debit:{gift_tx_id}", correlation_id=correlation_id,
                actor_id=sender_user_id, live_session_id=live_session_id,
                creator_recipient_id=recipient_creator_id,
                metadata={"gift_id": gift_id, "gift_version": gift_version, "quantity": quantity},
            )
            committed = _iso(now)
            con.execute(
                """INSERT INTO creator_gift_receipts
                   (id,creator_recipient_id,gift_transaction_id,live_session_id,gift_id,gift_version,
                    coin_value,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,'pending',?,?)""",
                (receipt_id, recipient_creator_id, gift_tx_id, live_session_id, gift_id,
                 gift_version, total, committed, committed),
            )
            con.execute(
                """INSERT INTO gift_transactions
                   (id,sender_user_id,sender_coin_account_id,recipient_creator_id,live_session_id,
                    battle_id,battle_round_id,gift_id,gift_version,unit_coin_cost,quantity,total_coin_cost,
                    status,idempotency_key,request_fingerprint,ledger_debit_id,creator_receipt_id,risk_state,
                    correlation_id,committed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'committed',?,?,?,?,?,?,?)""",
                (gift_tx_id, sender_user_id, account["id"], recipient_creator_id, live_session_id,
                 battle_id or live.battle_id, battle_round_id or live.battle_round_id,
                 gift_id, gift_version, int(gift["coin_cost"]), quantity, total,
                 idempotency_key, fp, debit["id"], receipt_id,
                 "monitored" if risk.action == "monitor" else "allowed", correlation_id, committed),
            )
            safe_event = {
                "event_id": None,
                "gift_transaction_id": gift_tx_id,
                "live_session_id": live_session_id,
                "sender_user_id": sender_user_id,
                "recipient_creator_id": recipient_creator_id,
                "gift": {
                    "id": gift_id, "version": gift_version, "display_name": gift["display_name"],
                    "unit_coin_cost": int(gift["coin_cost"]), "asset_ref": gift["asset_ref"],
                    "reduced_motion_asset_ref": gift["reduced_motion_asset_ref"],
                },
                "quantity": quantity,
                "total_coin_cost": total,
                "battle_id": battle_id or live.battle_id,
                "battle_round_id": battle_round_id or live.battle_round_id,
                "occurred_at": committed,
                "correlation_id": correlation_id,
            }
            outbox_id = self._enqueue_locked(
                con, "shared_sky.gift.committed", "gift_transaction", gift_tx_id,
                safe_event, live_session_id=live_session_id, correlation_id=correlation_id,
            )
            safe_event["event_id"] = outbox_id
            # Persist the exact safe event including its assigned event ID.
            con.execute("UPDATE economy_outbox SET payload_json=? WHERE id=?", (_json(safe_event), outbox_id))
            tx = con.execute("SELECT * FROM gift_transactions WHERE id=?", (gift_tx_id,)).fetchone()
            receipt = con.execute("SELECT * FROM creator_gift_receipts WHERE id=?", (receipt_id,)).fetchone()
            con.commit()
        return self._gift_result(dict(tx), dict(receipt), idempotent=False, event=safe_event)

    def _gift_result(self, tx: dict, receipt: dict | None, *, idempotent: bool, event: dict | None = None) -> dict:
        return {
            "idempotent_replay": idempotent,
            "gift_transaction": tx,
            "creator_receipt": receipt,
            "event": event,
            "payout_formula_configured": self.active_payout_policy() is not None,
        }

    def reverse_gift(
        self,
        gift_transaction_id: str,
        *,
        actor: str,
        reason: str,
        reference: str,
        idempotency_key: str,
    ) -> dict:
        if len((reason or "").strip()) < 3 or len((reference or "").strip()) < 2:
            raise EconomyError("INVALID_REVERSAL", "Gift reversal requires a reason and reference.")
        with self._connect() as con:
            self._begin(con)
            tx = con.execute("SELECT * FROM gift_transactions WHERE id=?", (gift_transaction_id,)).fetchone()
            if not tx:
                raise EconomyError("GIFT_TRANSACTION_NOT_FOUND", "Gift transaction not found.", status_code=404)
            if tx["status"] == "reversed":
                receipt = con.execute("SELECT * FROM creator_gift_receipts WHERE id=?", (tx["creator_receipt_id"],)).fetchone()
                con.commit()
                return {"idempotent_replay": True, "gift_transaction": dict(tx), "creator_receipt": dict(receipt) if receipt else None}
            if tx["status"] != "committed":
                raise EconomyError("GIFT_REVERSAL_CONFLICT", "Only a committed Gift can be reversed.", status_code=409)

            account = con.execute("SELECT * FROM coin_accounts WHERE id=?", (tx["sender_coin_account_id"],)).fetchone()
            assert account is not None
            correlation_id = tx["correlation_id"]
            credit = self._append_ledger_locked(
                con, account=account, entry_type=GIFT_REVERSAL_CREDIT, coin_delta=int(tx["total_coin_cost"]),
                source="gift_reversal", gift_transaction_id=tx["id"], reversal_id=reference,
                idempotency_key=idempotency_key, correlation_id=correlation_id, actor_id=actor,
                live_session_id=tx["live_session_id"], creator_recipient_id=tx["recipient_creator_id"],
                metadata={"reason": reason, "reference": reference},
            )
            now = _iso()
            con.execute(
                """UPDATE gift_transactions SET status='reversed',reversed_at=?,reversal_ledger_id=?,
                   reversal_reason=? WHERE id=?""",
                (now, credit["id"], reason, tx["id"]),
            )
            con.execute(
                """UPDATE creator_gift_receipts SET status='reversed',updated_at=?,adjustment_reference=?
                   WHERE id=?""",
                (now, reference, tx["creator_receipt_id"]),
            )
            event = {
                "gift_transaction_id": tx["id"], "live_session_id": tx["live_session_id"],
                "recipient_creator_id": tx["recipient_creator_id"], "battle_id": tx["battle_id"],
                "battle_round_id": tx["battle_round_id"], "status": "reversed", "occurred_at": now,
                "correlation_id": correlation_id,
            }
            outbox_id = self._enqueue_locked(
                con, "shared_sky.gift.reversed", "gift_transaction", tx["id"], event,
                live_session_id=tx["live_session_id"], correlation_id=correlation_id,
            )
            event["event_id"] = outbox_id
            con.execute("UPDATE economy_outbox SET payload_json=? WHERE id=?", (_json(event), outbox_id))
            tx = con.execute("SELECT * FROM gift_transactions WHERE id=?", (tx["id"],)).fetchone()
            receipt = con.execute("SELECT * FROM creator_gift_receipts WHERE id=?", (tx["creator_receipt_id"],)).fetchone()
            con.commit()
        return {"idempotent_replay": False, "gift_transaction": dict(tx), "creator_receipt": dict(receipt), "event": event}

    def active_payout_policy(self) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                """SELECT * FROM gift_payout_policies WHERE active=1 AND effective_at<=?
                   ORDER BY effective_at DESC,version DESC LIMIT 1""",
                (_iso(),),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["policy"] = json.loads(item.pop("policy_json"))
        return item

    def creator_statement(self, creator_recipient_id: str, *, start_at: str | None = None, end_at: str | None = None) -> dict:
        sql = "SELECT * FROM creator_gift_receipts WHERE creator_recipient_id=?"
        params: list[Any] = [creator_recipient_id]
        if start_at:
            sql += " AND created_at>=?"
            params.append(start_at)
        if end_at:
            sql += " AND created_at<?"
            params.append(end_at)
        sql += " ORDER BY created_at DESC"
        with self._connect() as con:
            rows = [dict(r) for r in con.execute(sql, params).fetchall()]
        totals = {state: 0 for state in RECEIPT_STATUSES}
        coins_by_state = {state: 0 for state in RECEIPT_STATUSES}
        for row in rows:
            state = row["status"]
            totals[state] = totals.get(state, 0) + 1
            coins_by_state[state] = coins_by_state.get(state, 0) + int(row["coin_value"])
        policy = self.active_payout_policy()
        return {
            "creator_recipient_id": creator_recipient_id,
            "gift_count": len(rows),
            "coins_received_total": sum(int(r["coin_value"]) for r in rows),
            "count_by_state": totals,
            "coins_by_state": coins_by_state,
            "payout_formula_configured": bool(policy),
            "payable_fiat_total_minor": (
                sum(int(r["payable_amount_minor"] or 0) for r in rows)
                if policy else None
            ),
            "payable_currency": None if not policy else "policy_defined",
            "entries": rows,
        }

    def transaction_history(self, user_id: str, *, limit: int = 100, offset: int = 0) -> dict:
        account = self.ensure_account(user_id)
        limit = max(1, min(int(limit), 250))
        offset = max(0, int(offset))
        with self._connect() as con:
            rows = [
                dict(r) for r in con.execute(
                    """SELECT id,entry_type,coin_delta,source,purchase_id,gift_transaction_id,
                              reversal_id,promotion_id,live_session_id,creator_recipient_id,
                              occurred_at,resulting_available_balance,resulting_recovery_debt
                       FROM coin_ledger_entries WHERE coin_account_id=?
                       ORDER BY recorded_at DESC LIMIT ? OFFSET ?""",
                    (account["id"], limit, offset),
                ).fetchall()
            ]
        return {"user_id": user_id, "account_id": account["id"], "entries": rows, "balance": self.get_balance(user_id)}

    def freeze_account(self, user_id: str, *, frozen: bool, actor: str, reason: str) -> dict:
        if len((reason or "").strip()) < 3:
            raise EconomyError("INVALID_OWNER_ACTION", "Freeze/unfreeze requires a reason.")
        with self._connect() as con:
            self._begin(con)
            account = self._get_account_locked(con, user_id)
            status = "frozen" if frozen else "active"
            con.execute("UPDATE coin_accounts SET status=?,updated_at=? WHERE id=?", (status, _iso(), account["id"]))
            self._enqueue_locked(
                con, "economy.account_frozen" if frozen else "economy.account_unfrozen",
                "coin_account", account["id"], {"user_id": user_id, "reason": reason, "actor": actor},
                correlation_id=uuid4().hex,
            )
            row = con.execute("SELECT * FROM coin_accounts WHERE id=?", (account["id"],)).fetchone()
            con.commit()
        return dict(row)

    def _enqueue_locked(
        self,
        con: sqlite3.Connection,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict,
        *,
        correlation_id: str,
        live_session_id: str | None = None,
    ) -> str:
        event_id = uuid4().hex
        con.execute(
            """INSERT INTO economy_outbox
               (id,event_type,aggregate_type,aggregate_id,live_session_id,payload_json,correlation_id,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (event_id, event_type, aggregate_type, aggregate_id, live_session_id, _json(payload), correlation_id, _iso()),
        )
        return event_id

    def pending_outbox(self, *, limit: int = 100) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT * FROM economy_outbox WHERE published_at IS NULL
                   ORDER BY created_at ASC LIMIT ?""",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def mark_outbox_published(self, event_id: str) -> None:
        with self._connect() as con:
            self._begin(con)
            con.execute(
                """UPDATE economy_outbox SET published_at=?,publish_attempts=publish_attempts+1
                   WHERE id=? AND published_at IS NULL""",
                (_iso(), event_id),
            )
            con.commit()

    def reconcile(self) -> dict:
        """Detect material financial mismatches without silently repairing them."""
        found: list[dict] = []
        with self._connect() as con:
            self._begin(con)
            accounts = con.execute("SELECT * FROM coin_accounts").fetchall()
            for account in accounts:
                row = con.execute(
                    "SELECT COALESCE(SUM(coin_delta),0) AS total FROM coin_ledger_entries WHERE coin_account_id=?",
                    (account["id"],),
                ).fetchone()
                ledger_net = int(row["total"] if row else 0)
                materialized_net = int(account["available_balance"]) - int(account["recovery_debt"])
                if ledger_net != materialized_net:
                    found.append(self._record_discrepancy_locked(
                        con, "ACCOUNT_LEDGER_MISMATCH", account["id"],
                        {"ledger_net_coins": ledger_net},
                        {"available_coins": int(account["available_balance"]), "recovery_debt_coins": int(account["recovery_debt"]), "net_coins": materialized_net},
                    ))

            gifts = con.execute("SELECT * FROM gift_transactions").fetchall()
            for gift in gifts:
                debit = con.execute(
                    "SELECT * FROM coin_ledger_entries WHERE id=? AND gift_transaction_id=?",
                    (gift["ledger_debit_id"], gift["id"]),
                ).fetchone()
                receipt = con.execute(
                    "SELECT * FROM creator_gift_receipts WHERE id=? AND gift_transaction_id=?",
                    (gift["creator_receipt_id"], gift["id"]),
                ).fetchone()
                if not debit or int(debit["coin_delta"]) != -int(gift["total_coin_cost"]):
                    found.append(self._record_discrepancy_locked(
                        con, "GIFT_LEDGER_MISMATCH", gift["id"],
                        {"coin_delta": -int(gift["total_coin_cost"]), "ledger_debit_id": gift["ledger_debit_id"]},
                        {"coin_delta": int(debit["coin_delta"]) if debit else None, "ledger_debit_id": debit["id"] if debit else None},
                    ))
                if not receipt or int(receipt["coin_value"]) != int(gift["total_coin_cost"]):
                    found.append(self._record_discrepancy_locked(
                        con, "GIFT_RECEIPT_MISMATCH", gift["id"],
                        {"coin_value": int(gift["total_coin_cost"]), "receipt_id": gift["creator_receipt_id"]},
                        {"coin_value": int(receipt["coin_value"]) if receipt else None, "receipt_id": receipt["id"] if receipt else None},
                    ))
            con.commit()
        return {"ok": len(found) == 0, "discrepancies": found}

    def _record_discrepancy_locked(self, con: sqlite3.Connection, kind: str, subject_id: str, expected: dict, actual: dict) -> dict:
        existing = con.execute(
            """SELECT * FROM economy_reconciliation_discrepancies
               WHERE discrepancy_type=? AND subject_id=? AND status='open'""",
            (kind, subject_id),
        ).fetchone()
        if existing:
            return dict(existing)
        item = {
            "id": uuid4().hex, "discrepancy_type": kind, "subject_id": subject_id,
            "expected_json": _json(expected), "actual_json": _json(actual),
            "status": "open", "created_at": _iso(),
        }
        con.execute(
            """INSERT INTO economy_reconciliation_discrepancies
               (id,discrepancy_type,subject_id,expected_json,actual_json,status,created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (item["id"], kind, subject_id, item["expected_json"], item["actual_json"], item["status"], item["created_at"]),
        )
        return item
