from __future__ import annotations

import json
from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .cosmic_economy import EconomyError
from .cosmic_economy_integrations import economy_service
from .cosmic_economy_shared_sky import chat5_shared_sky_status, configure_chat5_shared_sky
from .cosmic_payments import coin_payment_providers

router = APIRouter(tags=["Cosmic Creation Coin Compatibility"])
# Best-effort early bind for direct package use. A startup retry below runs after the complete
# production import graph has settled, so an import-order race cannot permanently disable LIVE
# recipient validation. The integration itself remains fail-closed when Shared Sky is absent.
_SHARED_SKY_BOOTSTRAP = configure_chat5_shared_sky()


@router.on_event("startup")
def _retry_shared_sky_binding_after_application_composition() -> None:
    configure_chat5_shared_sky()


def _member_user_id(request: Request) -> str:
    member = getattr(request.state, "member", None)
    user_id = getattr(member, "user_id", None)
    if not user_id:
        raise HTTPException(
            401,
            detail={"code": "UNAUTHENTICATED", "message": "Authenticated member required."},
        )
    return str(user_id)


def _raise(exc: EconomyError) -> None:
    raise HTTPException(exc.status_code, detail=exc.as_dict()) from exc


def _catalog_payload(user_id: str) -> dict:
    economy = economy_service()
    try:
        return {
            "user_id": user_id,
            "balance": economy.get_balance(user_id),
            "unit": "COSMIC_CREATION_COIN",
            "packs": economy.list_packs(active_only=True),
            "payment_providers": coin_payment_providers.configured(),
            "storefront": "/economy/coins",
            "purchase_endpoint": "/economy/me/coin-purchases",
            "legacy_creation_coin_credit_wallet_disabled": True,
            "client_can_set_amount_or_coin_quantity": False,
            "credit_source": "verified_provider_event",
            "subscription_effect": "none",
            "esp_role_effect": "none",
        }
    except EconomyError as exc:
        _raise(exc)


@router.get("/economy/payment-providers")
def payment_providers(request: Request):
    _member_user_id(request)
    return {"providers": coin_payment_providers.configured()}


@router.get("/economy/integration-status")
def economy_integration_status(request: Request):
    _member_user_id(request)
    return {
        "shared_sky": chat5_shared_sky_status(),
        "payment_providers": coin_payment_providers.configured(),
    }


@router.get("/economy/coins", response_class=HTMLResponse)
def cosmic_coin_storefront(request: Request):
    user_id = _member_user_id(request)
    payload = _catalog_payload(user_id)
    packs = payload["packs"]
    providers = payload["payment_providers"]
    balance = int((payload.get("balance") or {}).get("available_coins") or 0)
    safe_payload = json.dumps(
        {
            "packs": packs,
            "providers": providers,
            "purchase_endpoint": payload["purchase_endpoint"],
        },
        separators=(",", ":"),
    ).replace("<", "\\u003c")
    provider_note = (
        "Verified checkout is available."
        if providers
        else "Coin checkout is not enabled on this deployment yet; no payment will be attempted."
    )
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Cosmic Creation Coins · Elevate Souls Productions</title>"
        "<style>body{margin:0;background:#070512;color:#fff;font-family:Inter,system-ui,sans-serif}main{max-width:980px;margin:auto;padding:36px 20px 64px}.eyebrow{color:#edca72;font-weight:800;letter-spacing:.12em;text-transform:uppercase;font-size:.76rem}.hero{padding:28px;border:1px solid #8e6bff55;border-radius:24px;background:linear-gradient(135deg,#17102b,#0b0916)}h1{margin:.35rem 0;font-size:clamp(2rem,5vw,4rem)}.muted{color:#c8c3d8}.balance{font-size:1.3rem;font-weight:800;color:#edca72}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-top:20px}.card{border:1px solid #ffffff1f;border-radius:18px;padding:18px;background:#0f0c1d}.price{font-size:1.45rem;font-weight:900;margin:.5rem 0}.coin{font-weight:800;color:#edca72}button{width:100%;border:0;border-radius:12px;padding:12px 14px;font-weight:900;background:#edca72;color:#160f22;cursor:pointer}button:disabled{opacity:.55;cursor:not-allowed}.note{margin-top:22px;padding:16px;border-radius:14px;background:#ffffff0b;color:#c8c3d8}.error{color:#ff9f9f;min-height:1.3em}</style></head><body><main>"
        "<section class='hero'><div class='eyebrow'>Elevate Souls Productions Content Creation Command Center · Powered by Aura AI</div>"
        "<h1>Cosmic Creation Coins</h1><p class='muted'>The authoritative Coin wallet used for Shared Sky LIVE Gifts and approved platform Coin features.</p>"
        f"<p class='balance'>Available balance: {balance:,} Cosmic Creation Coins</p>"
        f"<p class='muted'>{escape(provider_note)}</p></section>"
        "<section><div id='packs' class='grid'></div><p id='error' class='error' role='alert'></p>"
        "<div class='note'>Prices and Coin quantities come only from the server catalogue. The browser cannot choose an amount. Coins are credited only after a verified payment-provider event; returning from checkout never credits the wallet.</div></section>"
        f"<script id='coin-data' type='application/json'>{safe_payload}</script>"
        "<script>(()=>{const data=JSON.parse(document.getElementById('coin-data').textContent);const root=document.getElementById('packs');const error=document.getElementById('error');const provider=data.providers[0]||null;const money=(p)=>new Intl.NumberFormat('en-GB',{style:'currency',currency:p.fiat_currency}).format(p.fiat_amount_minor/100);for(const p of data.packs){const card=document.createElement('article');card.className='card';const h=document.createElement('h2');h.textContent=p.display_name;const coins=document.createElement('div');coins.className='coin';coins.textContent=Number(p.coin_quantity).toLocaleString()+' Cosmic Creation Coins';const price=document.createElement('div');price.className='price';price.textContent=money(p);const btn=document.createElement('button');btn.type='button';btn.textContent=provider?'Buy securely':'Checkout unavailable';btn.disabled=!provider;btn.addEventListener('click',async()=>{if(!provider)return;error.textContent='';btn.disabled=true;try{const key=(globalThis.crypto&&crypto.randomUUID)?crypto.randomUUID():String(Date.now())+'-'+Math.random();const r=await fetch(data.purchase_endpoint,{method:'POST',headers:{'content-type':'application/json','Idempotency-Key':key},credentials:'same-origin',body:JSON.stringify({pack_id:p.pack_id,pack_version:p.version,provider})});const body=await r.json();if(!r.ok)throw new Error((body.detail&&body.detail.message)||body.detail||'Checkout could not be started');const url=body.checkout&&body.checkout.checkout_url;if(typeof url!=='string'||!url.startsWith('https://'))throw new Error('Payment provider returned an invalid checkout destination');location.assign(url);}catch(e){error.textContent=e instanceof Error?e.message:'Checkout could not be started';btn.disabled=false;}});card.append(h,coins,price,btn);root.append(card);}if(!data.packs.length){const p=document.createElement('p');p.className='muted';p.textContent='No Coin packs are currently available.';root.append(p);}})();</script>"
        "</main></body></html>",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/billing/creation-coins/catalog")
def legacy_creation_coin_catalog(request: Request):
    user_id = _member_user_id(request)
    payload = _catalog_payload(user_id)
    payload["deprecated_compatibility_path"] = True
    return payload


@router.get("/billing/creation-coins", response_class=RedirectResponse)
def legacy_creation_coin_storefront(request: Request):
    _member_user_id(request)
    return RedirectResponse(url="/economy/coins", status_code=307)


@router.post("/billing/stripe/checkout/credits")
def legacy_creation_coin_checkout_disabled(request: Request):
    _member_user_id(request)
    raise HTTPException(
        409,
        detail={
            "code": "LEGACY_CREATION_COIN_CHECKOUT_DISABLED",
            "message": "Legacy Creation Coin checkout is disabled. Use the canonical Cosmic Creation Coin purchase endpoint.",
            "canonical_endpoint": "/economy/me/coin-purchases",
        },
    )
