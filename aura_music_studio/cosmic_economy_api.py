from __future__ import annotations

from dataclasses import asdict
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .audit import AuditLedger
from .cosmic_economy import EconomyError
from .cosmic_economy_integrations import economy_service
from .cosmic_payments import coin_payment_providers
from .owner_auth import owner_authorized

router = APIRouter(tags=["Cosmic Creation Coins & Shared Sky LIVE Gifts"])


def _economy():
    return economy_service()


def _member_user_id(request: Request) -> str:
    member = getattr(request.state, "member", None)
    user_id = getattr(member, "user_id", None)
    if not user_id:
        raise HTTPException(401, detail={"code": "UNAUTHENTICATED", "message": "Authenticated member required."})
    return str(user_id)


def _owner(request: Request) -> None:
    if not owner_authorized(request):
        raise HTTPException(403, detail={"code": "OWNER_AUTH_REQUIRED", "message": "Owner authorization required."})


def _raise(exc: EconomyError) -> None:
    correlation_id = uuid4().hex
    detail = exc.as_dict()
    detail["correlation_id"] = correlation_id
    raise HTTPException(exc.status_code, detail=detail) from exc


class PurchaseRequest(BaseModel):
    pack_id: str = Field(min_length=1, max_length=120)
    pack_version: int = Field(gt=0)
    provider: str = Field(min_length=1, max_length=80)


class SendGiftRequest(BaseModel):
    recipient_creator_id: str = Field(min_length=1, max_length=180)
    live_session_id: str = Field(min_length=1, max_length=180)
    gift_id: str = Field(min_length=1, max_length=120)
    gift_version: int = Field(gt=0)
    quantity: int = Field(default=1, ge=1, le=1000)
    battle_id: str | None = Field(default=None, max_length=180)
    battle_round_id: str | None = Field(default=None, max_length=180)


class FeatureFlagRequest(BaseModel):
    enabled: bool
    reason: str = Field(min_length=3, max_length=500)


class FreezeRequest(BaseModel):
    frozen: bool
    reason: str = Field(min_length=3, max_length=500)


class OwnerAdjustmentRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=180)
    coin_delta: int
    reason: str = Field(min_length=3, max_length=500)
    reference: str = Field(min_length=2, max_length=240)
    idempotency_key: str = Field(min_length=1, max_length=200)


class SpendingLimitsRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=180)
    daily_hard_limit: int | None = Field(default=None, ge=0)
    weekly_hard_limit: int | None = Field(default=None, ge=0)
    monthly_hard_limit: int | None = Field(default=None, ge=0)
    daily_warning: int | None = Field(default=None, ge=0)
    weekly_warning: int | None = Field(default=None, ge=0)
    monthly_warning: int | None = Field(default=None, ge=0)
    reason: str = Field(min_length=3, max_length=500)


class PersonalSpendingLimitsRequest(BaseModel):
    daily_hard_limit: int | None = Field(default=None, ge=0)
    weekly_hard_limit: int | None = Field(default=None, ge=0)
    monthly_hard_limit: int | None = Field(default=None, ge=0)


class PublishGiftRequest(BaseModel):
    gift_id: str = Field(min_length=1, max_length=120)
    version: int = Field(gt=0)
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    coin_cost: int = Field(gt=0)
    approval_reference: str = Field(min_length=2, max_length=240)
    active: bool = True
    category: str = Field(default="support", min_length=1, max_length=80)
    asset_ref: str | None = Field(default=None, max_length=500)
    reduced_motion_asset_ref: str | None = Field(default=None, max_length=500)
    sound_ref: str | None = Field(default=None, max_length=500)
    battle_eligible: bool = True
    regions: list[str] = Field(default_factory=list, max_length=100)


class PublishPackRequest(BaseModel):
    pack_id: str = Field(min_length=1, max_length=120)
    version: int = Field(gt=0)
    display_name: str = Field(min_length=1, max_length=120)
    coin_quantity: int = Field(gt=0)
    fiat_amount_minor: int = Field(gt=0)
    fiat_currency: str = Field(min_length=3, max_length=3)
    approval_reference: str = Field(min_length=2, max_length=240)
    active: bool = True
    regions: list[str] = Field(default_factory=list, max_length=100)
    promotion_ref: str | None = Field(default=None, max_length=240)
    sort_order: int = 0


class ReverseGiftRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    reference: str = Field(min_length=2, max_length=240)
    idempotency_key: str = Field(min_length=1, max_length=200)


@router.get("/economy/coin-packs")
def coin_packs(currency: str | None = None):
    try:
        return {"packs": _economy().list_packs(currency=currency)}
    except EconomyError as exc:
        _raise(exc)


@router.get("/economy/gifts")
def gift_catalogue():
    return {"gifts": _economy().list_gifts(active_only=True)}


@router.get("/economy/me/balance")
def my_balance(request: Request):
    try:
        return _economy().get_balance(_member_user_id(request))
    except EconomyError as exc:
        _raise(exc)


@router.get("/economy/me/history")
def my_history(request: Request, limit: int = 100, offset: int = 0):
    try:
        return _economy().transaction_history(_member_user_id(request), limit=limit, offset=offset)
    except EconomyError as exc:
        _raise(exc)


@router.get("/economy/me/spending")
def my_spending(request: Request):
    try:
        return _economy().spending_state(_member_user_id(request))
    except EconomyError as exc:
        _raise(exc)


@router.put("/economy/me/personal-spending-limits")
def my_personal_spending_limits(
    request: Request,
    payload: PersonalSpendingLimitsRequest,
):
    try:
        economy = _economy()
        user_id = _member_user_id(request)
        limits = economy.set_personal_spending_limits(
            user_id,
            daily_hard_limit=payload.daily_hard_limit,
            weekly_hard_limit=payload.weekly_hard_limit,
            monthly_hard_limit=payload.monthly_hard_limit,
        )
        return {"personal_limits": limits, "spending": economy.spending_state(user_id)}
    except EconomyError as exc:
        _raise(exc)


@router.post("/economy/me/coin-purchases")
def create_coin_purchase(
    request: Request,
    payload: PurchaseRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
):
    """Create/reuse a provider checkout from server catalogue data.

    The internal purchase ID is used as the provider idempotency key. Once a provider checkout
    is bound locally, retries return the stored checkout without invoking the provider again.
    Coins are never credited by this route; verified provider events remain authoritative.
    """
    user_id = _member_user_id(request)
    try:
        provider = coin_payment_providers.get(payload.provider)
        economy = _economy()
        purchase = economy.create_purchase(
            user_id=user_id,
            pack_id=payload.pack_id,
            pack_version=payload.pack_version,
            provider=payload.provider.strip().lower(),
            idempotency_key=idempotency_key,
        )
        cached = economy.get_purchase_checkout(purchase["id"])
        if cached:
            return {
                "purchase": purchase,
                "checkout": {
                    "provider": cached["provider"],
                    "provider_payment_id": cached["provider_payment_id"],
                    "checkout_url": cached["checkout_url"],
                    "status": cached["status"],
                },
                "idempotent_replay": True,
                "coins_credited": purchase["status"] == "confirmed",
                "credit_condition": "verified_provider_event",
            }
        checkout = provider.create_checkout(
            purchase_id=purchase["id"],
            user_id=user_id,
            fiat_amount_minor=int(purchase["fiat_amount_minor"]),
            fiat_currency=purchase["fiat_currency"],
            coin_quantity=int(purchase["coin_quantity"]),
            idempotency_key=purchase["id"],
        )
        if checkout.provider.strip().lower() != payload.provider.strip().lower():
            raise EconomyError(
                "PAYMENT_PROVIDER_MISMATCH",
                "Checkout provider does not match requested provider.",
                status_code=502,
            )
        bound = economy.bind_purchase_checkout(
            purchase["id"],
            provider=checkout.provider,
            provider_payment_id=checkout.provider_payment_id,
            checkout_url=checkout.checkout_url,
            status=checkout.status,
        )
        return {
            "purchase": bound["purchase"],
            "checkout": asdict(checkout),
            "idempotent_replay": bool(bound["idempotent_replay"]),
            "coins_credited": bound["purchase"]["status"] == "confirmed",
            "credit_condition": "verified_provider_event",
        }
    except EconomyError as exc:
        _raise(exc)


@router.post("/auth/economy/payment-webhooks/{provider_name}", include_in_schema=False)
async def coin_payment_webhook(provider_name: str, request: Request):
    """Signature verification is delegated to the registered official provider adapter."""
    try:
        provider = coin_payment_providers.get(provider_name)
        raw = await request.body()
        event = provider.verify_webhook(headers=request.headers, body=raw)
        result = _economy().apply_verified_payment_event(event)
        return {"accepted": True, **result}
    except EconomyError as exc:
        _raise(exc)


@router.post("/economy/me/gifts/send")
def send_gift(
    request: Request,
    payload: SendGiftRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
):
    try:
        return _economy().send_gift(
            sender_user_id=_member_user_id(request),
            recipient_creator_id=payload.recipient_creator_id,
            live_session_id=payload.live_session_id,
            gift_id=payload.gift_id,
            gift_version=payload.gift_version,
            quantity=payload.quantity,
            idempotency_key=idempotency_key,
            battle_id=payload.battle_id,
            battle_round_id=payload.battle_round_id,
        )
    except EconomyError as exc:
        _raise(exc)


@router.get("/owner/economy/reconciliation")
def owner_reconciliation(request: Request):
    _owner(request)
    try:
        return _economy().reconcile()
    except EconomyError as exc:
        _raise(exc)


@router.get("/owner/economy/creator-statements/{creator_recipient_id}")
def owner_creator_statement(creator_recipient_id: str, request: Request, start_at: str | None = None, end_at: str | None = None):
    _owner(request)
    try:
        return _economy().creator_statement(creator_recipient_id, start_at=start_at, end_at=end_at)
    except EconomyError as exc:
        _raise(exc)


@router.get("/owner/economy/outbox")
def owner_outbox(request: Request, limit: int = 100):
    _owner(request)
    return {"events": _economy().pending_outbox(limit=limit)}


@router.post("/owner/economy/accounts/{user_id}/freeze")
def owner_freeze_account(user_id: str, payload: FreezeRequest, request: Request):
    _owner(request)
    try:
        result = _economy().freeze_account(
            user_id, frozen=payload.frozen, actor="owner_session", reason=payload.reason
        )
        AuditLedger().append(
            actor="owner_session", action="cosmic_economy.account_freeze_changed",
            subject_user_id=user_id, details={"frozen": payload.frozen, "reason": payload.reason},
        )
        return result
    except EconomyError as exc:
        _raise(exc)


@router.post("/owner/economy/adjustments")
def owner_adjustment(payload: OwnerAdjustmentRequest, request: Request):
    _owner(request)
    try:
        result = _economy().owner_adjustment(
            user_id=payload.user_id,
            coin_delta=payload.coin_delta,
            actor="owner_session",
            reason=payload.reason,
            reference=payload.reference,
            idempotency_key=payload.idempotency_key,
        )
        AuditLedger().append(
            actor="owner_session", action="cosmic_economy.owner_adjustment",
            subject_user_id=payload.user_id,
            details={
                "ledger_entry_id": result["id"], "coin_delta": payload.coin_delta,
                "reason": payload.reason, "reference": payload.reference,
            },
        )
        return result
    except EconomyError as exc:
        _raise(exc)


@router.post("/owner/economy/spending-limits")
def owner_spending_limits(payload: SpendingLimitsRequest, request: Request):
    _owner(request)
    try:
        result = _economy().set_spending_limits(
            payload.user_id,
            actor="owner_session",
            reason=payload.reason,
            daily_hard_limit=payload.daily_hard_limit,
            weekly_hard_limit=payload.weekly_hard_limit,
            monthly_hard_limit=payload.monthly_hard_limit,
            daily_warning=payload.daily_warning,
            weekly_warning=payload.weekly_warning,
            monthly_warning=payload.monthly_warning,
        )
        AuditLedger().append(
            actor="owner_session", action="cosmic_economy.spending_limits_changed",
            subject_user_id=payload.user_id, details=result,
        )
        return result
    except EconomyError as exc:
        _raise(exc)


@router.post("/owner/economy/feature-flags/{flag_name}")
def owner_feature_flag(flag_name: str, payload: FeatureFlagRequest, request: Request):
    _owner(request)
    try:
        result = _economy().set_feature_flag(
            flag_name, payload.enabled, actor="owner_session", reason=payload.reason
        )
        AuditLedger().append(
            actor="owner_session", action="cosmic_economy.feature_flag_changed",
            details={"flag": flag_name, "enabled": payload.enabled, "reason": payload.reason},
        )
        return result
    except EconomyError as exc:
        _raise(exc)


@router.post("/owner/economy/gift-catalogue")
def owner_publish_gift(payload: PublishGiftRequest, request: Request):
    _owner(request)
    try:
        result = _economy().publish_gift_definition(
            gift_id=payload.gift_id,
            version=payload.version,
            display_name=payload.display_name,
            description=payload.description,
            coin_cost=payload.coin_cost,
            actor="owner_session",
            approval_reference=payload.approval_reference,
            active=payload.active,
            category=payload.category,
            asset_ref=payload.asset_ref,
            reduced_motion_asset_ref=payload.reduced_motion_asset_ref,
            sound_ref=payload.sound_ref,
            battle_eligible=payload.battle_eligible,
            regions=payload.regions,
        )
        AuditLedger().append(
            actor="owner_session", action="cosmic_economy.gift_catalogue_version_published",
            details={"gift_id": payload.gift_id, "version": payload.version, "coin_cost": payload.coin_cost,
                     "approval_reference": payload.approval_reference},
        )
        return result
    except EconomyError as exc:
        _raise(exc)


@router.post("/owner/economy/coin-packs")
def owner_publish_pack(payload: PublishPackRequest, request: Request):
    _owner(request)
    try:
        result = _economy().publish_pack(
            pack_id=payload.pack_id,
            version=payload.version,
            display_name=payload.display_name,
            coin_quantity=payload.coin_quantity,
            fiat_amount_minor=payload.fiat_amount_minor,
            fiat_currency=payload.fiat_currency,
            actor="owner_session",
            approval_reference=payload.approval_reference,
            active=payload.active,
            regions=payload.regions,
            promotion_ref=payload.promotion_ref,
            sort_order=payload.sort_order,
        )
        AuditLedger().append(
            actor="owner_session", action="cosmic_economy.coin_pack_version_published",
            details={
                "pack_id": payload.pack_id, "version": payload.version,
                "coin_quantity": payload.coin_quantity, "fiat_amount_minor": payload.fiat_amount_minor,
                "fiat_currency": payload.fiat_currency.upper(), "approval_reference": payload.approval_reference,
            },
        )
        return result
    except EconomyError as exc:
        _raise(exc)


@router.post("/owner/economy/gifts/{gift_transaction_id}/reverse")
def owner_reverse_gift(gift_transaction_id: str, payload: ReverseGiftRequest, request: Request):
    _owner(request)
    try:
        result = _economy().reverse_gift(
            gift_transaction_id,
            actor="owner_session",
            reason=payload.reason,
            reference=payload.reference,
            idempotency_key=payload.idempotency_key,
        )
        AuditLedger().append(
            actor="owner_session", action="cosmic_economy.gift_reversed",
            details={
                "gift_transaction_id": gift_transaction_id,
                "reason": payload.reason, "reference": payload.reference,
            },
        )
        return result
    except EconomyError as exc:
        _raise(exc)
