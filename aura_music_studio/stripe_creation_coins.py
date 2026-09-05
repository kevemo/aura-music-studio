from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .credit_wallet import CreditWalletStore
from .plans import PLANS
from .stripe_billing import _session_user, accounts, credit_packs

router = APIRouter(tags=["Creation Coins"])
_wallet = CreditWalletStore(accounts.db_path)


def _public_pack(pack) -> dict:
    return {
        "id": pack.id,
        "label": pack.label,
        "creation_coins": pack.credits,
        "amount_minor": pack.amount_minor,
        "currency": pack.currency,
    }


def _public_plan(plan) -> dict:
    return {
        "id": plan.id,
        "name": plan.name,
        "monthly_amount_minor": plan.monthly_price_minor,
        "currency": plan.currency,
        "display_price": plan.display_price,
        "stripe_subscription_required": plan.id != "free",
    }


def creation_coin_catalog_payload(request: Request) -> dict:
    user = _session_user(request)
    if str(user.get("status") or "") != "active":
        raise HTTPException(403, "Active account required for Creation Coin purchases")
    packs = credit_packs()
    return {
        "user_id": str(user["id"]),
        "balance": _wallet.balance(str(user["id"])),
        "unit": "CREATION_COIN",
        "packs": [_public_pack(pack) for pack in packs.values()],
        "memberships": [_public_plan(PLANS[key]) for key in ("free", "base", "pro")],
        "eligible_membership_ids_for_coin_purchase": ["free", "base", "pro"],
        "client_can_set_amount_or_coin_quantity": False,
        "checkout_endpoint": "/billing/stripe/checkout/credits",
        "credit_source": "verified_stripe_webhook",
        "subscription_effect": "none",
        "esp_role_effect": "none",
    }


@router.get("/billing/creation-coins/catalog")
def creation_coin_catalog(request: Request):
    return creation_coin_catalog_payload(request)


@router.get("/billing/creation-coins", response_class=HTMLResponse)
def creation_coin_storefront(request: Request):
    payload = creation_coin_catalog_payload(request)
    safe_payload = json.dumps(payload, separators=(",", ":")).replace("<", "\\u003c")
    balance = int(payload["balance"])
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Creation Coins · Elevate Souls Productions</title>"
        "<style>body{margin:0;background:#070512;color:#fff;font-family:Inter,system-ui,sans-serif}main{max-width:980px;margin:auto;padding:36px 20px 64px}.eyebrow{color:#edca72;font-weight:800;letter-spacing:.12em;text-transform:uppercase;font-size:.76rem}.hero{padding:28px;border:1px solid #8e6bff55;border-radius:24px;background:linear-gradient(135deg,#17102b,#0b0916)}h1{margin:.35rem 0;font-size:clamp(2rem,5vw,4rem)}.muted{color:#c8c3d8}.balance{font-size:1.3rem;font-weight:800;color:#edca72}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-top:20px}.card{border:1px solid #ffffff1f;border-radius:18px;padding:18px;background:#0f0c1d}.price{font-size:1.45rem;font-weight:900;margin:.5rem 0}.coin{font-weight:800;color:#edca72}button{width:100%;border:0;border-radius:12px;padding:12px 14px;font-weight:900;background:#edca72;color:#160f22;cursor:pointer}button:disabled{opacity:.55;cursor:not-allowed}.note{margin-top:22px;padding:16px;border-radius:14px;background:#ffffff0b;color:#c8c3d8}.error{color:#ff9f9f;min-height:1.3em}</style></head><body><main>"
        "<section class='hero'><div class='eyebrow'>Elevate Souls Productions Content Creation Command Center · Powered by Aura AI</div>"
        "<h1>Creation Coins</h1><p class='muted'>Top up your creative wallet for additional generation capacity. Free, Basic and Pro members can purchase top-ups.</p>"
        f"<p class='balance'>Current balance: {balance:,} Creation Coins</p></section>"
        "<section><div id='packs' class='grid'></div><p id='error' class='error' role='alert'></p>"
        "<div class='note'>Prices and coin quantities are fixed by the server-side Stripe catalog. Your browser sends only the selected pack ID. Coins are added only after a signed Stripe webhook confirms a paid Checkout Session. Buying coins never changes an ESP role or membership tier.</div></section>"
        f"<script id='coin-data' type='application/json'>{safe_payload}</script>"
        "<script>(()=>{const data=JSON.parse(document.getElementById('coin-data').textContent);const root=document.getElementById('packs');const error=document.getElementById('error');const money=(p)=>new Intl.NumberFormat('en-GB',{style:'currency',currency:p.currency}).format(p.amount_minor/100);for(const p of data.packs){const card=document.createElement('article');card.className='card';const h=document.createElement('h2');h.textContent=p.label;const coins=document.createElement('div');coins.className='coin';coins.textContent=p.creation_coins.toLocaleString()+' Creation Coins';const price=document.createElement('div');price.className='price';price.textContent=money(p);const btn=document.createElement('button');btn.type='button';btn.textContent='Buy with Stripe';btn.addEventListener('click',async()=>{error.textContent='';btn.disabled=true;try{const r=await fetch(data.checkout_endpoint,{method:'POST',headers:{'content-type':'application/json'},credentials:'same-origin',body:JSON.stringify({pack_id:p.id})});const body=await r.json();if(!r.ok)throw new Error(body.detail||'Checkout could not be started');if(typeof body.checkout_url!=='string'||!body.checkout_url.startsWith('https://checkout.stripe.com/'))throw new Error('Stripe returned an untrusted checkout destination');location.assign(body.checkout_url);}catch(e){error.textContent=e instanceof Error?e.message:'Checkout could not be started';btn.disabled=false;}});card.append(h,coins,price,btn);root.append(card);}if(!data.packs.length){const p=document.createElement('p');p.className='muted';p.textContent='Creation Coin packs will appear here when their Stripe Price IDs are configured.';root.append(p);}})();</script>"
        "</main></body></html>",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


__all__ = ["creation_coin_catalog_payload", "creation_coin_catalog", "creation_coin_storefront", "router"]
