from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .accounts import AccountStore
from .audit import AuditLedger
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


class PromotionalGrantRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=180)
    coin_quantity: int = Field(gt=0)
    campaign_ref: str = Field(min_length=2, max_length=240)
    idempotency_key: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=3, max_length=500)


class AvailabilityRequest(BaseModel):
    active: bool
    reason: str = Field(min_length=3, max_length=500)


class CreatorGiftReceivingRequest(BaseModel):
    enabled: bool
    reason: str = Field(min_length=3, max_length=500)


class DiscrepancyResolutionRequest(BaseModel):
    resolution_note: str = Field(min_length=3, max_length=2000)


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


@router.post("/creators/{creator_recipient_id}/gift-receiving")
def creator_gift_receiving(
    creator_recipient_id: str,
    payload: CreatorGiftReceivingRequest,
    request: Request,
):
    _owner(request)
    try:
        economy = economy_service()
        result = economy.set_creator_gift_receiving(
            creator_recipient_id,
            enabled=payload.enabled,
            actor="owner_session",
            reason=payload.reason,
        )
        AuditLedger(AccountStore(economy.db_path)).append(
            actor="owner_session",
            action="cosmic_economy.creator_gift_receiving_changed",
            details={
                "creator_recipient_id": creator_recipient_id,
                "receiving_enabled": payload.enabled,
                "reason": payload.reason,
            },
        )
        return result
    except EconomyError as exc:
        _raise(exc)


@router.post("/promotional-credits")
def grant_promotional_coins(payload: PromotionalGrantRequest, request: Request):
    _owner(request)
    try:
        return _ops().grant_promotional_coins(
            user_id=payload.user_id,
            coin_quantity=payload.coin_quantity,
            campaign_ref=payload.campaign_ref,
            idempotency_key=payload.idempotency_key,
            actor="owner_session",
            reason=payload.reason,
        )
    except EconomyError as exc:
        _raise(exc)


@router.post("/coin-packs/{pack_id}/versions/{version}/availability")
def coin_pack_availability(
    pack_id: str,
    version: int,
    payload: AvailabilityRequest,
    request: Request,
):
    _owner(request)
    try:
        return _ops().set_coin_pack_active(
            pack_id,
            version,
            active=payload.active,
            actor="owner_session",
            reason=payload.reason,
        )
    except EconomyError as exc:
        _raise(exc)


@router.post("/gift-catalogue/{gift_id}/versions/{version}/availability")
def gift_availability(
    gift_id: str,
    version: int,
    payload: AvailabilityRequest,
    request: Request,
):
    _owner(request)
    try:
        return _ops().set_gift_active(
            gift_id,
            version,
            active=payload.active,
            actor="owner_session",
            reason=payload.reason,
        )
    except EconomyError as exc:
        _raise(exc)


@router.get("/operational-events")
def operational_events(
    request: Request,
    event_type: str | None = None,
    user_id: str | None = None,
    limit: int = 100,
):
    _owner(request)
    return {
        "events": economy_service().operational_events(
            event_type=event_type,
            user_id=user_id,
            limit=limit,
        )
    }


@router.get("/discrepancies")
def reconciliation_discrepancies(request: Request, status: str = "open", limit: int = 100):
    _owner(request)
    return {"discrepancies": _ops().list_reconciliation_discrepancies(status=status, limit=limit)}


@router.post("/discrepancies/{discrepancy_id}/resolve")
def resolve_reconciliation_discrepancy(
    discrepancy_id: str,
    payload: DiscrepancyResolutionRequest,
    request: Request,
):
    _owner(request)
    try:
        return _ops().resolve_reconciliation_discrepancy(
            discrepancy_id,
            actor="owner_session",
            resolution_note=payload.resolution_note,
        )
    except EconomyError as exc:
        _raise(exc)


@router.get("/finance-snapshot")
def finance_snapshot(request: Request):
    _owner(request)
    return _ops().finance_snapshot()
