from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .cosmic_economy import EconomyError
from .cosmic_economy_integrations import economy_service
from .cosmic_economy_owner_ops import EconomyOwnerOperations
from .owner_auth import owner_authorized

router = APIRouter(prefix="/owner/economy", tags=["Cosmic Economy Owner Operations"])


def _owner(request: Request) -> None:
    if not owner_authorized(request):
        raise HTTPException(
            403,
            detail={"code": "OWNER_AUTH_REQUIRED", "message": "Owner authorization required."},
        )


def _raise(exc: EconomyError) -> None:
    raise HTTPException(exc.status_code, detail=exc.as_dict()) from exc


def _ops() -> EconomyOwnerOperations:
    return EconomyOwnerOperations(economy_service())


class RiskReviewRequest(BaseModel):
    decision: str = Field(min_length=1, max_length=30)
    reason: str = Field(min_length=3, max_length=1000)
    appeal_status: str | None = Field(default=None, max_length=120)


class ReceiptHoldRequest(BaseModel):
    held: bool
    reason: str = Field(min_length=3, max_length=500)
    reference: str = Field(min_length=2, max_length=240)


@router.get("/risk-cases")
def risk_cases(request: Request, status: str | None = "open", limit: int = 100):
    _owner(request)
    return {"cases": _ops().list_risk_cases(status=status, limit=limit)}


@router.post("/risk-cases/{case_id}/review")
def review_risk_case(case_id: str, payload: RiskReviewRequest, request: Request):
    _owner(request)
    try:
        return _ops().review_risk_case(
            case_id,
            reviewer="owner_session",
            decision=payload.decision,
            reason=payload.reason,
            appeal_status=payload.appeal_status,
        )
    except EconomyError as exc:
        _raise(exc)


@router.post("/creator-receipts/{receipt_id}/hold")
def creator_receipt_hold(receipt_id: str, payload: ReceiptHoldRequest, request: Request):
    _owner(request)
    try:
        return _ops().set_creator_receipt_hold(
            receipt_id,
            held=payload.held,
            actor="owner_session",
            reason=payload.reason,
            reference=payload.reference,
        )
    except EconomyError as exc:
        _raise(exc)


@router.get("/discrepancies")
def reconciliation_discrepancies(request: Request, status: str = "open", limit: int = 100):
    _owner(request)
    return {"discrepancies": _ops().list_reconciliation_discrepancies(status=status, limit=limit)}


@router.get("/finance-snapshot")
def finance_snapshot(request: Request):
    _owner(request)
    return _ops().finance_snapshot()
