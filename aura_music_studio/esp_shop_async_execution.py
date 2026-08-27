from __future__ import annotations

import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from . import esp_shop_automation as base
from . import esp_shop_provider_runtime as runtime

router = APIRouter(tags=["ESP Shop Async Provider Execution"])
EXECUTE_PATH = "/command-center/api/shop-automation/actions/{action_id}/execute"
RECONCILE_PATH = "/command-center/api/shop-automation/actions/{action_id}/reconcile"
PROVIDER_PENDING = "provider_pending"


class AsyncProviderExecutionStore:
    """Provider execution orchestration with explicit asynchronous reconciliation.

    A provider can return a terminal successful receipt, a terminal failure, or a pending
    external reference. Pending actions are locked against re-execution and can only be
    reconciled by reading that exact external provider reference.
    """

    def __init__(self, db_path: str):
        self.runtime = runtime.ProviderRuntimeStore(str(db_path))
        self.base = self.runtime.base
        self.db_path = str(db_path)

    def _audit(self, con, user_id: str, actor: str, action: str, action_id: str, metadata=None):
        self.base._audit(
            con,
            user_id,
            actor,
            action,
            "action",
            action_id,
            metadata or {},
        )

    def _insert_receipt(
        self,
        con,
        *,
        action: dict,
        execution_ref: str,
        status: str,
        metadata: dict | None = None,
    ) -> None:
        con.execute(
            """INSERT INTO esp_shop_provider_receipts
               (id,action_id,user_id,provider,execution_ref,status,metadata_json,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                uuid4().hex,
                action["id"],
                action["user_id"],
                action["provider"],
                str(execution_ref or "")[:500],
                status[:40],
                json.dumps(runtime._redact(metadata or {}), sort_keys=True),
                runtime._now(),
            ),
        )

    def _provider_context(self, action: dict):
        connection, credential = self.runtime._connected_provider(
            action["user_id"], action["provider"]
        )
        capability = self.runtime._required_capability(action)
        if capability and capability not in set(connection.get("scopes") or []):
            raise PermissionError(
                f"Connected provider account is missing the required {capability} capability"
            )
        adapter = self.runtime._adapter(action["provider"])
        return connection, credential, adapter

    def _require_provider_approval(self, action: dict, adapter) -> None:
        checker = getattr(adapter, "requires_explicit_approval", None)
        if not callable(checker) or not bool(checker(action)):
            return
        if action["status"] == "approved":
            return
        now = runtime._now()
        with self.runtime._connect() as con:
            con.execute(
                """UPDATE esp_shop_action_queue SET status='awaiting_approval',updated_at=?
                   WHERE id=? AND user_id=? AND status='prepared'""",
                (now, action["id"], action["user_id"]),
            )
            self._audit(
                con,
                action["user_id"],
                "provider_runtime",
                "provider_explicit_approval_required",
                action["id"],
                {"provider": action["provider"]},
            )
        raise PermissionError("This provider action requires explicit human approval")

    def _validate_provider_action(self, action: dict, adapter) -> None:
        validator = getattr(adapter, "validate_before_execute", None)
        if not callable(validator):
            return
        policy = self.base.policy(action["user_id"])
        validator(action, policy)

    def _mark_terminal(
        self,
        action: dict,
        *,
        actor: str,
        status: str,
        execution_ref: str,
        metadata: dict | None = None,
        audit_action: str,
    ) -> dict:
        if status not in {"executed", "failed"}:
            raise ValueError("Unsupported terminal provider status")
        now = runtime._now()
        with self.runtime._connect() as con:
            con.execute(
                """UPDATE esp_shop_action_queue
                   SET status=?,provider_execution_ref=?,executed_at=?,updated_at=?
                   WHERE id=? AND user_id=?""",
                (
                    status,
                    execution_ref[:500],
                    now if status == "executed" else None,
                    now,
                    action["id"],
                    action["user_id"],
                ),
            )
            self._insert_receipt(
                con,
                action=action,
                execution_ref=execution_ref,
                status=status,
                metadata=metadata,
            )
            self._audit(
                con,
                action["user_id"],
                actor,
                audit_action,
                action["id"],
                {"provider": action["provider"], "execution_ref": execution_ref[:500]},
            )
        return self.base.action(action["id"], action["user_id"])

    def _mark_pending(
        self,
        action: dict,
        *,
        actor: str,
        execution_ref: str,
        metadata: dict | None = None,
        first_observation: bool,
    ) -> dict:
        now = runtime._now()
        with self.runtime._connect() as con:
            con.execute(
                """UPDATE esp_shop_action_queue
                   SET status=?,provider_execution_ref=?,executed_at=NULL,updated_at=?
                   WHERE id=? AND user_id=?""",
                (
                    PROVIDER_PENDING,
                    execution_ref[:500],
                    now,
                    action["id"],
                    action["user_id"],
                ),
            )
            self._insert_receipt(
                con,
                action=action,
                execution_ref=execution_ref,
                status=PROVIDER_PENDING,
                metadata=metadata,
            )
            self._audit(
                con,
                action["user_id"],
                actor,
                "provider_execution_pending" if first_observation else "provider_reconciliation_pending",
                action["id"],
                {"provider": action["provider"], "execution_ref": execution_ref[:500]},
            )
        return self.base.action(action["id"], action["user_id"])

    @staticmethod
    def _receipt_state(receipt: dict) -> str:
        pending = receipt.get("pending") is True
        success = receipt.get("success") is True
        failed = receipt.get("failed") is True
        if sum((pending, success, failed)) != 1:
            raise RuntimeError("Provider adapter returned an ambiguous execution receipt")
        return "pending" if pending else "success" if success else "failed"

    def execute_action(self, action_id: str, user_id: str, *, actor: str) -> dict:
        action = self.base.action(action_id, user_id)
        if action["status"] == "awaiting_approval":
            raise PermissionError("Approve this Shop action before provider execution")
        if action["status"] not in {"prepared", "approved"}:
            raise ValueError("This Shop action is not eligible for provider execution")

        # Re-check all generic spend/write safety immediately before any external side effect.
        self.runtime._recheck_policy(action)
        connection, credential, adapter = self._provider_context(action)
        self._require_provider_approval(action, adapter)
        self._validate_provider_action(action, adapter)

        try:
            receipt = adapter.execute(
                action,
                connection,
                secret_ref=credential["secret_ref"],
            )
        except (PermissionError, ValueError):
            # Provider-specific validation is expected to run before network I/O and should not
            # mutate the action to failed; the creator can correct the action/approval instead.
            raise
        except Exception as exc:
            now = runtime._now()
            with self.runtime._connect() as con:
                con.execute(
                    """UPDATE esp_shop_action_queue SET status='failed',updated_at=?
                       WHERE id=? AND user_id=?""",
                    (now, action_id, user_id),
                )
                self._insert_receipt(
                    con,
                    action=action,
                    execution_ref="",
                    status="failed",
                    metadata={"error": str(exc)[:500], "external_outcome_confirmed": False},
                )
                self._audit(
                    con,
                    user_id,
                    actor,
                    "provider_execution_failed",
                    action_id,
                    {"provider": action["provider"], "external_outcome_confirmed": False},
                )
            raise RuntimeError(
                "Provider execution failed or its external outcome could not be verified; automatic retry is blocked"
            ) from exc

        if not isinstance(receipt, dict):
            raise RuntimeError("Provider adapter returned an invalid execution receipt")
        state = self._receipt_state(receipt)
        execution_ref = str(receipt.get("execution_ref") or "").strip()
        if not execution_ref:
            raise RuntimeError("Provider execution receipt is missing its external execution reference")
        metadata = runtime._redact(receipt.get("metadata") or {})

        if state == "pending":
            updated = self._mark_pending(
                action,
                actor=actor,
                execution_ref=execution_ref,
                metadata=metadata,
                first_observation=True,
            )
            return {
                "action": updated,
                "receipt": {
                    "execution_ref": execution_ref,
                    "status": PROVIDER_PENDING,
                    "metadata": metadata,
                },
                "provider_execution_confirmed": False,
                "provider_pending": True,
                "automatic_retry_allowed": False,
            }
        if state == "failed":
            updated = self._mark_terminal(
                action,
                actor=actor,
                status="failed",
                execution_ref=execution_ref,
                metadata=metadata,
                audit_action="provider_execution_failed_confirmed",
            )
            return {
                "action": updated,
                "receipt": {"execution_ref": execution_ref, "status": "failed", "metadata": metadata},
                "provider_execution_confirmed": False,
                "provider_pending": False,
            }
        updated = self._mark_terminal(
            action,
            actor=actor,
            status="executed",
            execution_ref=execution_ref,
            metadata=metadata,
            audit_action="provider_execution_confirmed",
        )
        return {
            "action": updated,
            "receipt": {"execution_ref": execution_ref, "status": "executed", "metadata": metadata},
            "provider_execution_confirmed": True,
            "provider_pending": False,
        }

    def reconcile_action(self, action_id: str, user_id: str, *, actor: str) -> dict:
        action = self.base.action(action_id, user_id)
        if action["status"] != PROVIDER_PENDING:
            raise ValueError("Only provider-pending Shop actions can be reconciled")
        execution_ref = str(action.get("provider_execution_ref") or "").strip()
        if not execution_ref:
            raise RuntimeError("Pending Shop action has no provider execution reference")
        connection, credential, adapter = self._provider_context(action)
        reconciler = getattr(adapter, "reconcile", None)
        if not callable(reconciler):
            raise RuntimeError("Provider adapter does not support asynchronous reconciliation")

        # Reconciliation is a read of an already-started provider operation. It deliberately
        # does not re-run the purchase or re-apply changed spend policy as a new side effect.
        try:
            receipt = reconciler(
                action,
                connection,
                secret_ref=credential["secret_ref"],
                execution_ref=execution_ref,
            )
        except Exception as exc:
            # A transient polling failure must never convert an unknown external job to failed.
            raise RuntimeError("Provider reconciliation is temporarily unavailable; action remains pending") from exc

        if not isinstance(receipt, dict):
            raise RuntimeError("Provider adapter returned an invalid reconciliation receipt")
        state = self._receipt_state(receipt)
        returned_ref = str(receipt.get("execution_ref") or "").strip()
        if returned_ref != execution_ref:
            raise RuntimeError("Provider reconciliation reference does not match the pending action")
        metadata = runtime._redact(receipt.get("metadata") or {})

        if state == "pending":
            updated = self._mark_pending(
                action,
                actor=actor,
                execution_ref=execution_ref,
                metadata=metadata,
                first_observation=False,
            )
            return {
                "action": updated,
                "provider_pending": True,
                "provider_execution_confirmed": False,
                "automatic_retry_allowed": False,
            }
        if state == "failed":
            updated = self._mark_terminal(
                action,
                actor=actor,
                status="failed",
                execution_ref=execution_ref,
                metadata=metadata,
                audit_action="provider_reconciliation_failed_confirmed",
            )
            return {
                "action": updated,
                "provider_pending": False,
                "provider_execution_confirmed": False,
            }
        updated = self._mark_terminal(
            action,
            actor=actor,
            status="executed",
            execution_ref=execution_ref,
            metadata=metadata,
            audit_action="provider_reconciliation_confirmed",
        )
        return {
            "action": updated,
            "provider_pending": False,
            "provider_execution_confirmed": True,
        }


def _authenticated(request: Request):
    store, member, _plan_id, tier = base._store(request)
    base._require_automation(tier)
    return AsyncProviderExecutionStore(store.db_path), member


@router.post(EXECUTE_PATH)
def execute_async_provider_action_api(action_id: str, request: Request):
    store, member = _authenticated(request)
    try:
        return store.execute_action(action_id, member.user_id, actor=member.user_id)
    except KeyError as exc:
        raise HTTPException(404, "Shop action not found") from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post(RECONCILE_PATH)
def reconcile_async_provider_action_api(action_id: str, request: Request):
    store, member = _authenticated(request)
    try:
        return store.reconcile_action(action_id, member.user_id, actor=member.user_id)
    except KeyError as exc:
        raise HTTPException(404, "Shop action not found") from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


__all__ = [
    "router",
    "AsyncProviderExecutionStore",
    "EXECUTE_PATH",
    "RECONCILE_PATH",
    "PROVIDER_PENDING",
]
