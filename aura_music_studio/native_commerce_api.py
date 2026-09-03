from __future__ import annotations

import json
from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict

from .accounts import AccountStore
from .native_access import NativeAccessResolver
from .native_billing import NativeEntitlementLedger
from .native_paypal import (
    NativePayPalError,
    NativePayPalInvoiceCreator,
    NativePayPalPaymentVerifier,
)
from .native_paypal_lifecycle import NativePayPalLifecycleVerifier
from .native_products import BillingPeriod, get_native_product, public_native_products

router = APIRouter()

_MAX_WEBHOOK_BYTES = 1_000_000
_PAYMENT_EVENT = "INVOICING.INVOICE.PAID"
_LIFECYCLE_EVENTS = {
    "INVOICING.INVOICE.CANCELLED",
    "INVOICING.INVOICE.REFUNDED",
}
_MEMBER_COOKIE = "lss_session"

_store = AccountStore()
native_entitlements = NativeEntitlementLedger(_store.db_path)
native_access = NativeAccessResolver(_store, native_entitlements)


class NativeCheckoutRequest(BaseModel):
    """User-selectable native checkout fields only; money and identity are server-owned."""

    model_config = ConfigDict(extra="forbid")

    product_id: str
    billing_period: BillingPeriod
    founding_offer: bool = False


def _payment_verifier() -> NativePayPalPaymentVerifier:
    # Construct lazily so production fails closed on a missing native metadata secret
    # when the payment boundary is actually invoked, without breaking unrelated startup.
    return NativePayPalPaymentVerifier()


def _lifecycle_verifier() -> NativePayPalLifecycleVerifier:
    return NativePayPalLifecycleVerifier()


def _checkout_creator() -> NativePayPalInvoiceCreator:
    # Checkout configuration is deliberately lazy for the same reason as webhook verification:
    # unrelated site startup remains available, while the native commerce boundary fails closed.
    return NativePayPalInvoiceCreator()


def _session_member(request: Request) -> dict | None:
    authorization = request.headers.get("Authorization", "")
    token = (
        authorization[7:].strip()
        if authorization.lower().startswith("bearer ")
        else request.cookies.get(_MEMBER_COOKIE)
    )
    return _store.resolve_session(token)


def _checkout_member(request: Request) -> dict:
    member = _session_member(request)
    if not member:
        raise HTTPException(401, "Authenticated member session required")
    if str(member.get("status") or "").strip().lower() not in {"active", "owner"}:
        raise HTTPException(403, "An active Command Center account is required for native checkout")
    if not str(member.get("email") or "").strip():
        raise HTTPException(409, "The member account requires a billing email before native checkout")
    return member


def _duplicate_event(exc: ValueError) -> bool:
    message = str(exc).lower()
    return "already been processed" in message


def _native_product_snapshot(member: dict) -> dict:
    access = native_access.resolve(str(member["id"]))
    access_public = access.public_dict()
    active = set(access_public["entitlements"])
    products: list[dict] = []

    for public_product in public_native_products():
        item = dict(public_product)
        entitlements = set(item.get("entitlements") or [])
        overlap = sorted(entitlements & active)
        missing = sorted(entitlements - active)
        founding_available = bool(
            item.get("founding_first_year_price") is not None
            and not native_entitlements.has_product_purchase(str(member["id"]), str(item["id"]))
        )
        item.update(
            {
                "active_entitlements": overlap,
                "missing_entitlements": missing,
                "fully_active": bool(entitlements) and not missing,
                # The normal account UI avoids charging again for an entitlement that is already
                # active. A partially overlapping bundle is also withheld until a real upgrade/
                # credit policy exists, rather than silently charging twice for one component.
                "account_checkout_available": not overlap,
                "founding_offer_available": founding_available and not overlap,
            }
        )
        products.append(item)

    return {
        "account": {
            "user_id": str(member["id"]),
            "email": str(member.get("email") or ""),
            "plan_id": str(member.get("plan_id") or "free"),
        },
        "access": access_public,
        "products": products,
        "payment_model": {
            "provider": "paypal",
            "checkout_type": "invoice",
            "automatic_renewal_enabled": False,
            "entitlement_activates_only_after_verified_provider_webhook": True,
            "native_device_authority_granted_by_payment": False,
        },
    }


@router.get("/pricing/native-products")
def native_products_pricing():
    """Public canonical native-product catalogue with no account or entitlement state."""

    return {
        "currency": "GBP",
        "products": public_native_products(),
        "checkout_type": "paypal_invoice",
        "automatic_renewal_enabled": False,
    }


@router.get("/account/native-products.json")
def native_products_account_json(request: Request):
    member = _checkout_member(request)
    return _native_product_snapshot(member)


@router.get("/account/native-products", response_class=HTMLResponse, include_in_schema=False)
def native_products_account_page(request: Request):
    member = _session_member(request)
    if not member:
        return RedirectResponse("/signin", status_code=303)
    if str(member.get("status") or "").strip().lower() not in {"active", "owner"}:
        return RedirectResponse("/dashboard", status_code=303)

    snapshot = _native_product_snapshot(member)
    access = snapshot["access"]

    def product_card(product: dict) -> str:
        product_id = str(product["id"])
        name = escape(str(product["name"]))
        currency = escape(str(product["currency"]))
        monthly = escape(str(product["monthly_price"]))
        annual = escape(str(product["annual_price"]))
        entitlements = ", ".join(str(item).replace("_", " ").title() for item in product["entitlements"])
        if product["fully_active"]:
            action_html = "<div class='active'>Already active on this account</div>"
        elif not product["account_checkout_available"]:
            overlap = ", ".join(str(item).replace("_", " ").title() for item in product["active_entitlements"])
            action_html = (
                "<div class='held'>Part of this bundle is already active ("
                + escape(overlap)
                + "). Choose the missing standalone product instead; bundle upgrade credits are not invented.</div>"
            )
        else:
            buttons = [
                f"<button onclick=\"buy('{escape(product_id, quote=True)}','monthly',false)\">£{monthly}/month</button>",
                f"<button onclick=\"buy('{escape(product_id, quote=True)}','annual',false)\">£{annual}/year</button>",
            ]
            if product.get("founding_offer_available"):
                founding = escape(str(product["founding_first_year_price"]))
                buttons.append(
                    f"<button class='founding' onclick=\"buy('{escape(product_id, quote=True)}','annual',true)\">Founding first year £{founding}</button>"
                )
            action_html = "<div class='actions'>" + "".join(buttons) + "</div>"

        founding_copy = ""
        if product.get("founding_first_year_price") is not None:
            founding_copy = (
                "<p class='small'>Founding first annual term: £"
                + escape(str(product["founding_first_year_price"]))
                + ". A later annual term uses the canonical £"
                + annual
                + " price. Automatic renewal is not claimed by the current invoice flow.</p>"
            )
        return (
            "<article class='product'>"
            f"<div class='eyebrow'>{currency} native product</div><h2>{name}</h2>"
            f"<p>{escape(entitlements)}</p>"
            f"<div class='prices'><b>£{monthly}<small>/month</small></b><b>£{annual}<small>/year</small></b></div>"
            + founding_copy
            + action_html
            + "</article>"
        )

    cards = "".join(product_card(product) for product in snapshot["products"])
    sources = access.get("sources") or {}
    source_rows = "".join(
        f"<li><b>{escape(str(entitlement).replace('_',' ').title())}</b>: {escape(', '.join(values))}</li>"
        for entitlement, values in sorted(sources.items())
    ) or "<li>No active Aura OS/Aura Sec commercial entitlement is recorded.</li>"
    email = escape(str(snapshot["account"]["email"]))

    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'><title>Native Products</title><style>
:root{{--bg:#060811;--panel:#101522;--line:#ffffff1f;--gold:#f3c770;--cyan:#58e8ff;--good:#79dfa6;--muted:#bec5d7}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 12% 0,#2b1746,transparent 32%),radial-gradient(circle at 88% 0,#123d54,transparent 28%),var(--bg);color:#fff;font-family:Inter,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.wrap{{width:min(1180px,calc(100% - 28px));margin:auto;padding:26px 0 60px}}.top{{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}}.btn,button{{border:1px solid var(--line);border-radius:11px;padding:10px 13px;background:#ffffff0a;color:#fff;font-weight:850;cursor:pointer}}.eyebrow{{color:var(--gold);text-transform:uppercase;letter-spacing:.14em;font-size:.71rem;font-weight:900}}h1{{font-size:clamp(2.7rem,7vw,5.6rem);line-height:.95;margin:.16em 0}}.muted,.product p,.small{{color:var(--muted);line-height:1.55}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:20px}}.product,.status{{border:1px solid var(--line);border-radius:20px;background:#101522e8;padding:19px}}.prices{{display:flex;gap:18px;flex-wrap:wrap;margin:15px 0}}.prices b{{font-size:1.45rem}}.prices small{{font-size:.72rem;color:var(--muted)}}.actions{{display:grid;gap:8px}}.founding{{border-color:#f3c77088;background:#f3c77018}}.active{{padding:11px;border:1px solid #79dfa666;background:#79dfa611;border-radius:10px;color:var(--good);font-weight:850}}.held{{padding:11px;border:1px solid #f3c77066;background:#f3c77010;border-radius:10px;color:#ffe3a6;font-size:.85rem;line-height:1.45}}#result{{margin-top:16px;min-height:22px}}@media(max-width:850px){{.grid{{grid-template-columns:1fr}}}}</style></head><body><main class='wrap'>
<header class='top'><div><div class='eyebrow'>Pulsar-Frequency House · Native products</div><b>Aura OS + Aura Sec</b></div><div><a class='btn' href='/dashboard'>← Dashboard</a> <a class='btn' href='/aura-sec'>Aura Sec</a></div></header>
<section style='padding:46px 0 14px'><div class='eyebrow'>Canonical account commerce</div><h1>Your native access</h1><p class='muted'>Prices below come directly from the platform's canonical native-product catalogue. Unlimited Pro can already include Aura OS and Aura Sec; verified standalone purchases are combined with membership access without granting browser or device authority.</p></section>
<section class='status'><div class='eyebrow'>Current commercial entitlement sources</div><ul>{source_rows}</ul><p class='muted'>Billing email: {email}. Device trust, command signing, heartbeat proof and native privileged actions remain separate security controls.</p></section>
<section class='grid'>{cards}</section><div id='result' class='muted'></div>
<section class='status' style='margin-top:18px'><div class='eyebrow'>Payment truth</div><p class='muted'>The current PayPal integration sends a verified invoice to your billing email. Access activates only after an authenticated PayPal payment webhook is independently reconciled to the canonical invoice. This invoice flow does not claim automatic renewal; recurring collection requires the separate provider subscription layer.</p></section>
<script>
async function buy(product_id,billing_period,founding_offer){{
 const out=document.getElementById('result'); out.textContent='Creating canonical PayPal invoice…';
 try{{
   const response=await fetch('/billing/native/paypal/checkout',{{method:'POST',headers:{{'Content-Type':'application/json'}},credentials:'same-origin',body:JSON.stringify({{product_id,billing_period,founding_offer}})}});
   const data=await response.json();
   if(!response.ok) throw new Error(data.detail||'Checkout could not be created');
   out.textContent='PayPal invoice '+data.invoice_id+' was sent to your billing email. Access remains unchanged until verified payment arrives.';
 }}catch(error){{out.textContent=error.message||'Checkout could not be created';}}
}}
</script></main></body></html>"""
    return HTMLResponse(html)


@router.post("/billing/native/paypal/checkout")
def native_paypal_checkout(payload: NativeCheckoutRequest, request: Request):
    """Create and send a canonical native-product PayPal invoice for the signed-in member.

    Browser input may select only the product, billing period and explicit founding-offer flag.
    User identity and billing email are recovered from the authenticated account. Product name,
    currency and amount are recomputed from ``native_products.py`` inside the invoice creator.
    Payment never activates access here: only the verified PayPal webhook can mutate entitlement.
    """

    member = _checkout_member(request)
    try:
        product = get_native_product(payload.product_id)
        # Validate period/offer compatibility before creating any provider resource.
        product.price_minor_for(payload.billing_period, founding_offer=payload.founding_offer)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if payload.founding_offer and native_entitlements.has_product_purchase(member["id"], product.id):
        raise HTTPException(409, "Founding pricing is only available for the first product term")

    try:
        creator = _checkout_creator()
    except NativePayPalError as exc:
        raise HTTPException(503, "Native PayPal checkout is not configured") from exc

    try:
        invoice = creator.create_and_send(
            user_id=member["id"],
            billing_email=member["email"],
            product_id=product.id,
            billing_period=payload.billing_period,
            founding_offer=payload.founding_offer,
        )
    except NativePayPalError as exc:
        message = str(exc)
        status_code = 502 if "failed" in message.lower() else 400
        raise HTTPException(status_code, message) from exc

    return {
        "created": True,
        "payment_state": "awaiting_provider_payment",
        **invoice,
    }


@router.post("/billing/native/paypal/webhook", include_in_schema=False)
async def native_paypal_webhook(request: Request):
    """Accept only provider-authenticated native-product PayPal lifecycle evidence.

    The route never trusts browser return state, client prices, user IDs or product IDs.
    It inspects the untrusted event type only to select the strict verifier; that verifier
    then authenticates the PayPal transmission and independently re-loads the exact
    authoritative invoice before the entitlement ledger can mutate native access.
    """

    raw = await request.body()
    if not raw:
        raise HTTPException(400, "PayPal webhook body is required")
    if len(raw) > _MAX_WEBHOOK_BYTES:
        raise HTTPException(413, "PayPal webhook body exceeds the accepted size")

    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "PayPal webhook payload is invalid JSON") from exc
    if not isinstance(envelope, dict):
        raise HTTPException(400, "PayPal webhook payload must be an object")

    event_type = str(envelope.get("event_type") or "")
    headers = {str(key): str(value) for key, value in request.headers.items()}

    try:
        if event_type == _PAYMENT_EVENT:
            receipt = native_entitlements.process_verified_event(
                raw_event=raw,
                headers=headers,
                verifier=_payment_verifier(),
            )
            return {
                "accepted": True,
                "kind": "payment",
                "event_id": receipt.event_id,
                "product_id": receipt.product_id,
            }

        if event_type in _LIFECYCLE_EVENTS:
            receipt = native_entitlements.process_verified_lifecycle_event(
                raw_event=raw,
                headers=headers,
                verifier=_lifecycle_verifier(),
            )
            return {
                "accepted": True,
                "kind": receipt.event_type,
                "event_id": receipt.event_id,
                "product_id": receipt.product_id,
            }

        raise HTTPException(400, "Unsupported PayPal native-product event type")
    except HTTPException:
        raise
    except NativePayPalError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        # Provider retries are normal. A previously committed event is acknowledged so
        # PayPal does not repeatedly redeliver it; all other ledger validation failures
        # remain fail-closed and visible to the provider as rejected input.
        if _duplicate_event(exc):
            return {"accepted": True, "duplicate": True}
        raise HTTPException(400, str(exc)) from exc
