from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from .cosmic_economy import (
    GIFT_REVERSAL_CREDIT,
    OWNER_APPROVED_ADJUSTMENT_CREDIT,
    OWNER_APPROVED_ADJUSTMENT_DEBIT,
    PROMOTIONAL_CREDIT,
    EconomyError,
    _iso,
    _json,
)
from .cosmic_economy_integrations import IntegratedCosmicEconomy


def _fingerprint(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CommandBoundCosmicEconomy(IntegratedCosmicEconomy):
    """Binds non-purchase/Gift-send financial command keys to exact request intent."""

    def _init_schema(self) -> None:
        super()._init_schema()
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS economy_command_idempotency (
                    command_type TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    result_ref TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(command_type, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_economy_command_idempotency_subject
                    ON economy_command_idempotency(command_type, subject_id);
                """
            )

    def _binding_locked(self, con, command_type: str, idempotency_key: str, fingerprint: str):
        row = con.execute(
            """SELECT * FROM economy_command_idempotency
               WHERE command_type=? AND idempotency_key=?""",
            (command_type, idempotency_key),
        ).fetchone()
        if row and row["request_fingerprint"] != fingerprint:
            raise EconomyError(
                "IDEMPOTENCY_KEY_REUSED",
                "Idempotency key was already bound to a different financial command.",
                status_code=409,
            )
        return row

    def _bind_locked(
        self,
        con,
        *,
        command_type: str,
        idempotency_key: str,
        fingerprint: str,
        subject_id: str,
        result_ref: str,
    ) -> None:
        con.execute(
            """INSERT INTO economy_command_idempotency
               (command_type,idempotency_key,request_fingerprint,subject_id,result_ref,created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                command_type,
                idempotency_key,
                fingerprint,
                subject_id,
                result_ref,
                _iso(),
            ),
        )

    @staticmethod
    def _metadata(row) -> dict:
        try:
            return json.loads(row["metadata_json"] or "{}")
        except Exception:
            return {}

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
        reason = (reason or "").strip()
        reference = (reference or "").strip()
        if len(reason) < 3 or len(reference) < 2:
            raise EconomyError("INVALID_OWNER_ACTION", "Owner adjustment requires a reason and reference.")
        payload = {
            "user_id": user_id,
            "coin_delta": coin_delta,
            "reason": reason,
            "reference": reference,
        }
        fp = _fingerprint(payload)
        command_type = "owner_adjustment"
        correlation_id = uuid4().hex
        with self._connect() as con:
            self._begin(con)
            binding = self._binding_locked(con, command_type, idempotency_key, fp)
            if binding:
                row = con.execute(
                    "SELECT * FROM coin_ledger_entries WHERE id=?",
                    (binding["result_ref"],),
                ).fetchone()
                if not row:
                    raise EconomyError(
                        "IDEMPOTENCY_BINDING_BROKEN",
                        "Financial idempotency binding is missing its ledger result.",
                        status_code=503,
                    )
                con.commit()
                return dict(row)

            legacy = con.execute(
                """SELECT le.*,ca.user_id AS bound_user_id FROM coin_ledger_entries le
                   JOIN coin_accounts ca ON ca.id=le.coin_account_id
                   WHERE le.idempotency_key=?
                     AND le.entry_type IN (?,?) LIMIT 1""",
                (
                    idempotency_key,
                    OWNER_APPROVED_ADJUSTMENT_CREDIT,
                    OWNER_APPROVED_ADJUSTMENT_DEBIT,
                ),
            ).fetchone()
            if legacy:
                metadata = self._metadata(legacy)
                if (
                    legacy["bound_user_id"] != user_id
                    or int(legacy["coin_delta"]) != coin_delta
                    or metadata.get("reason") != reason
                    or metadata.get("reference") != reference
                ):
                    raise EconomyError(
                        "IDEMPOTENCY_KEY_REUSED",
                        "Idempotency key was already bound to a different Owner adjustment.",
                        status_code=409,
                    )
                self._bind_locked(
                    con,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    fingerprint=fp,
                    subject_id=user_id,
                    result_ref=legacy["id"],
                )
                result = dict(legacy)
                result.pop("bound_user_id", None)
                con.commit()
                return result

            account = self._get_account_locked(con, user_id)
            entry = self._append_ledger_locked(
                con,
                account=account,
                entry_type=(
                    OWNER_APPROVED_ADJUSTMENT_CREDIT
                    if coin_delta > 0
                    else OWNER_APPROVED_ADJUSTMENT_DEBIT
                ),
                coin_delta=coin_delta,
                source="owner_adjustment",
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
                actor_id=actor,
                metadata={"reason": reason, "reference": reference},
                allow_recovery_debt=coin_delta < 0,
            )
            self._enqueue_locked(
                con,
                "economy.owner_adjustment",
                "coin_account",
                account["id"],
                {
                    "ledger_entry_id": entry["id"],
                    "coin_delta": coin_delta,
                    "reason": reason,
                    "reference": reference,
                },
                correlation_id=correlation_id,
            )
            self._bind_locked(
                con,
                command_type=command_type,
                idempotency_key=idempotency_key,
                fingerprint=fp,
                subject_id=user_id,
                result_ref=entry["id"],
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
        campaign_ref = (campaign_ref or "").strip()
        if len(campaign_ref) < 1:
            raise EconomyError("INVALID_PROMOTION", "Promotional credit requires a campaign reference.")
        payload = {
            "user_id": user_id,
            "coin_quantity": coin_quantity,
            "campaign_ref": campaign_ref,
        }
        fp = _fingerprint(payload)
        command_type = "promotional_credit"
        correlation_id = uuid4().hex
        with self._connect() as con:
            self._begin(con)
            binding = self._binding_locked(con, command_type, idempotency_key, fp)
            if binding:
                row = con.execute(
                    "SELECT * FROM coin_ledger_entries WHERE id=?",
                    (binding["result_ref"],),
                ).fetchone()
                if not row:
                    raise EconomyError(
                        "IDEMPOTENCY_BINDING_BROKEN",
                        "Financial idempotency binding is missing its ledger result.",
                        status_code=503,
                    )
                con.commit()
                return dict(row)

            legacy = con.execute(
                """SELECT le.*,ca.user_id AS bound_user_id FROM coin_ledger_entries le
                   JOIN coin_accounts ca ON ca.id=le.coin_account_id
                   WHERE le.idempotency_key=? AND le.entry_type=? LIMIT 1""",
                (idempotency_key, PROMOTIONAL_CREDIT),
            ).fetchone()
            if legacy:
                metadata = self._metadata(legacy)
                if (
                    legacy["bound_user_id"] != user_id
                    or int(legacy["coin_delta"]) != coin_quantity
                    or legacy["promotion_id"] != campaign_ref
                    or metadata.get("campaign_ref") != campaign_ref
                ):
                    raise EconomyError(
                        "IDEMPOTENCY_KEY_REUSED",
                        "Idempotency key was already bound to a different promotional credit.",
                        status_code=409,
                    )
                self._bind_locked(
                    con,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    fingerprint=fp,
                    subject_id=user_id,
                    result_ref=legacy["id"],
                )
                result = dict(legacy)
                result.pop("bound_user_id", None)
                con.commit()
                return result

            account = self._get_account_locked(con, user_id)
            entry = self._append_ledger_locked(
                con,
                account=account,
                entry_type=PROMOTIONAL_CREDIT,
                coin_delta=coin_quantity,
                source="promotion",
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
                actor_id=actor,
                promotion_id=campaign_ref,
                metadata={"campaign_ref": campaign_ref},
            )
            self._enqueue_locked(
                con,
                "economy.promotional_credit",
                "coin_account",
                account["id"],
                {
                    "ledger_entry_id": entry["id"],
                    "coin_quantity": coin_quantity,
                    "campaign_ref": campaign_ref,
                },
                correlation_id=correlation_id,
            )
            self._bind_locked(
                con,
                command_type=command_type,
                idempotency_key=idempotency_key,
                fingerprint=fp,
                subject_id=user_id,
                result_ref=entry["id"],
            )
            con.commit()
        return entry

    def reverse_gift(
        self,
        gift_transaction_id: str,
        *,
        actor: str,
        reason: str,
        reference: str,
        idempotency_key: str,
    ) -> dict:
        reason = (reason or "").strip()
        reference = (reference or "").strip()
        if len(reason) < 3 or len(reference) < 2:
            raise EconomyError("INVALID_REVERSAL", "Gift reversal requires a reason and reference.")
        payload = {
            "gift_transaction_id": gift_transaction_id,
            "reason": reason,
            "reference": reference,
        }
        fp = _fingerprint(payload)
        command_type = "gift_reversal"
        with self._connect() as con:
            self._begin(con)
            binding = self._binding_locked(con, command_type, idempotency_key, fp)
            if binding:
                tx = con.execute(
                    "SELECT * FROM gift_transactions WHERE id=?",
                    (gift_transaction_id,),
                ).fetchone()
                if not tx or binding["subject_id"] != gift_transaction_id:
                    raise EconomyError(
                        "IDEMPOTENCY_BINDING_BROKEN",
                        "Gift reversal binding does not match its transaction.",
                        status_code=503,
                    )
                receipt = con.execute(
                    "SELECT * FROM creator_gift_receipts WHERE id=?",
                    (tx["creator_receipt_id"],),
                ).fetchone()
                con.commit()
                return {
                    "idempotent_replay": True,
                    "gift_transaction": dict(tx),
                    "creator_receipt": dict(receipt) if receipt else None,
                }

            legacy = con.execute(
                """SELECT * FROM coin_ledger_entries
                   WHERE idempotency_key=? AND entry_type=? LIMIT 1""",
                (idempotency_key, GIFT_REVERSAL_CREDIT),
            ).fetchone()
            if legacy:
                metadata = self._metadata(legacy)
                if (
                    legacy["gift_transaction_id"] != gift_transaction_id
                    or metadata.get("reason") != reason
                    or metadata.get("reference") != reference
                ):
                    raise EconomyError(
                        "IDEMPOTENCY_KEY_REUSED",
                        "Idempotency key was already bound to a different Gift reversal.",
                        status_code=409,
                    )
                self._bind_locked(
                    con,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    fingerprint=fp,
                    subject_id=gift_transaction_id,
                    result_ref=legacy["id"],
                )
                tx = con.execute(
                    "SELECT * FROM gift_transactions WHERE id=?",
                    (gift_transaction_id,),
                ).fetchone()
                receipt = (
                    con.execute(
                        "SELECT * FROM creator_gift_receipts WHERE id=?",
                        (tx["creator_receipt_id"],),
                    ).fetchone()
                    if tx
                    else None
                )
                con.commit()
                return {
                    "idempotent_replay": True,
                    "gift_transaction": dict(tx) if tx else None,
                    "creator_receipt": dict(receipt) if receipt else None,
                }

            tx = con.execute(
                "SELECT * FROM gift_transactions WHERE id=?",
                (gift_transaction_id,),
            ).fetchone()
            if not tx:
                raise EconomyError(
                    "GIFT_TRANSACTION_NOT_FOUND",
                    "Gift transaction not found.",
                    status_code=404,
                )
            if tx["status"] == "reversed":
                reversal = (
                    con.execute(
                        "SELECT * FROM coin_ledger_entries WHERE id=?",
                        (tx["reversal_ledger_id"],),
                    ).fetchone()
                    if tx["reversal_ledger_id"]
                    else None
                )
                raise EconomyError(
                    "GIFT_ALREADY_REVERSED",
                    "Gift has already been reversed with a different command key.",
                    status_code=409,
                    details={
                        "gift_transaction_id": gift_transaction_id,
                        "reversal_ledger_id": reversal["id"] if reversal else None,
                    },
                )
            if tx["status"] != "committed":
                raise EconomyError(
                    "GIFT_REVERSAL_CONFLICT",
                    "Only a committed Gift can be reversed.",
                    status_code=409,
                )

            account = con.execute(
                "SELECT * FROM coin_accounts WHERE id=?",
                (tx["sender_coin_account_id"],),
            ).fetchone()
            assert account is not None
            correlation_id = tx["correlation_id"]
            credit = self._append_ledger_locked(
                con,
                account=account,
                entry_type=GIFT_REVERSAL_CREDIT,
                coin_delta=int(tx["total_coin_cost"]),
                source="gift_reversal",
                gift_transaction_id=tx["id"],
                reversal_id=reference,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
                actor_id=actor,
                live_session_id=tx["live_session_id"],
                creator_recipient_id=tx["recipient_creator_id"],
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
                "gift_transaction_id": tx["id"],
                "live_session_id": tx["live_session_id"],
                "recipient_creator_id": tx["recipient_creator_id"],
                "battle_id": tx["battle_id"],
                "battle_round_id": tx["battle_round_id"],
                "status": "reversed",
                "occurred_at": now,
                "correlation_id": correlation_id,
            }
            outbox_id = self._enqueue_locked(
                con,
                "shared_sky.gift.reversed",
                "gift_transaction",
                tx["id"],
                event,
                live_session_id=tx["live_session_id"],
                correlation_id=correlation_id,
            )
            event["event_id"] = outbox_id
            con.execute(
                "UPDATE economy_outbox SET payload_json=? WHERE id=?",
                (_json(event), outbox_id),
            )
            self._bind_locked(
                con,
                command_type=command_type,
                idempotency_key=idempotency_key,
                fingerprint=fp,
                subject_id=gift_transaction_id,
                result_ref=credit["id"],
            )
            tx = con.execute(
                "SELECT * FROM gift_transactions WHERE id=?",
                (tx["id"],),
            ).fetchone()
            receipt = con.execute(
                "SELECT * FROM creator_gift_receipts WHERE id=?",
                (tx["creator_receipt_id"],),
            ).fetchone()
            con.commit()
        return {
            "idempotent_replay": False,
            "gift_transaction": dict(tx),
            "creator_receipt": dict(receipt),
            "event": event,
        }
