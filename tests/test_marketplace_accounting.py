from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio import marketplace_accounting, stripe_billing
from aura_music_studio.accounts import AccountStore
from aura_music_studio.marketplace_accounting import MarketplaceAccountReadStore
from aura_music_studio.marketplace_orders import MarketplaceOrderStore
from aura_music_studio.marketplace_settlement import MarketplaceSettlementStore
from aura_music_studio.stripe_billing_hardening import router as hardened_billing_router
from aura_music_studio.stripe_marketplace_fee_evidence import StripeMarketplaceFeeEvidenceStore
from aura_music_studio.stripe_marketplace_refund_evidence import StripeMarketplaceRefundEvidenceStore


def _active_user(accounts: AccountStore, email: str, name: str) -> tuple[str, str]:
    signup = accounts.signup(email, name, "marketplace-test-password", "free")
    accounts.decide_membership(signup.approval_token, "approve", "test-owner")
    return signup.user_id, accounts.create_session(signup.user_id)


def _verified_sale(
    *,
    orders: MarketplaceOrderStore,
    settlements: MarketplaceSettlementStore,
    fees: StripeMarketplaceFeeEvidenceStore,
    buyer_id: str,
    seller_id: str,
    suffix: str,
    publication_id: str,
    gross_minor: int = 1_000,
    provider_fee_minor: int = 100,
) -> tuple[dict, dict]:
    order = orders.create_order(
        provider="stripe",
        tenant_id="tenant-test",
        buyer_user_id=buyer_id,
        publication_id=publication_id,
        publication_revision=f"rev-{suffix}",
        creator_user_id=seller_id,
        gross_minor=gross_minor,
        currency="GBP",
    )
    checkout_id = f"cs_{suffix}"
    payment_intent_id = f"pi_{suffix}"
    orders.bind_provider_checkout(order_id=order["id"], provider_checkout_reference=checkout_id)
    fees.record(
        event_id=f"evt_{suffix}",
        checkout_session_id=checkout_id,
        order_id=order["id"],
        payment_intent_id=payment_intent_id,
        charge_id=f"ch_{suffix}",
        balance_transaction_id=f"txn_{suffix}",
        gross_minor=gross_minor,
        provider_fee_minor=provider_fee_minor,
        net_minor=gross_minor - provider_fee_minor,
        currency="GBP",
    )
    settlement = settlements.record_verified_order(
        provider="stripe",
        provider_order_reference=payment_intent_id,
        tenant_id="tenant-test",
        publication_id=publication_id,
        creator_user_id=seller_id,
        gross_minor=gross_minor,
        provider_fee_minor=provider_fee_minor,
        currency="GBP",
    )
    return order, settlement


def _fixture(tmp_path):
    db_path = tmp_path / "marketplace.sqlite3"
    accounts = AccountStore(db_path)
    orders = MarketplaceOrderStore(db_path)
    settlements = MarketplaceSettlementStore(db_path)
    fees = StripeMarketplaceFeeEvidenceStore(db_path)
    refunds = StripeMarketplaceRefundEvidenceStore(db_path)
    read_store = MarketplaceAccountReadStore(db_path)

    buyer_a, buyer_a_token = _active_user(accounts, "buyer-a@example.com", "Buyer A")
    buyer_b, _buyer_b_token = _active_user(accounts, "buyer-b@example.com", "Buyer B")
    seller_a, seller_a_token = _active_user(accounts, "seller-a@example.com", "Seller A")
    seller_b, _seller_b_token = _active_user(accounts, "seller-b@example.com", "Seller B")

    order_a, settlement_a = _verified_sale(
        orders=orders,
        settlements=settlements,
        fees=fees,
        buyer_id=buyer_a,
        seller_id=seller_a,
        suffix="paid_a",
        publication_id="publication-a",
    )
    _verified_sale(
        orders=orders,
        settlements=settlements,
        fees=fees,
        buyer_id=buyer_b,
        seller_id=seller_b,
        suffix="paid_b",
        publication_id="publication-b",
        gross_minor=2_000,
        provider_fee_minor=200,
    )

    refund = refunds.record(
        event_id="evt_refund_a",
        refund_id="re_refund_a",
        checkout_session_id="cs_paid_a",
        payment_intent_id="pi_paid_a",
        charge_id="ch_paid_a",
        refund_balance_transaction_id="txn_refund_a",
        customer_refund_minor=500,
        provider_balance_amount_minor=-500,
        provider_balance_fee_minor=0,
        provider_balance_net_minor=-500,
        currency="GBP",
        original_gross_minor=1_000,
        original_net_minor=900,
    )
    settlements.record_verified_reversal(
        provider="stripe",
        provider_reversal_reference="re_refund_a",
        provider_order_reference="pi_paid_a",
        amount_minor=refund["settlement_reversal_minor"],
        currency="GBP",
    )
    refunds.mark_settlement_recorded("re_refund_a")

    return {
        "accounts": accounts,
        "orders": orders,
        "settlements": settlements,
        "fees": fees,
        "read_store": read_store,
        "buyer_a": buyer_a,
        "buyer_a_token": buyer_a_token,
        "buyer_b": buyer_b,
        "seller_a": seller_a,
        "seller_a_token": seller_a_token,
        "order_a": order_a,
        "settlement_a": settlement_a,
    }


def test_buyer_history_is_account_scoped_and_uses_customer_refund_evidence(tmp_path):
    data = _fixture(tmp_path)

    purchases = data["read_store"].purchases_for_buyer(data["buyer_a"])

    assert len(purchases) == 1
    assert purchases[0]["marketplace_order_id"] == data["order_a"]["id"]
    assert purchases[0]["publication_id"] == "publication-a"
    assert purchases[0]["status"] == "partially_refunded"
    assert purchases[0]["gross_minor"] == 1_000
    assert purchases[0]["customer_refund_minor"] == 500
    assert purchases[0]["customer_paid_minor"] == 500
    assert "creator_user_id" not in purchases[0]
    assert "payment_intent_id" not in purchases[0]


def test_seller_statement_is_account_scoped_and_refunds_reduce_net_proceeds(tmp_path):
    data = _fixture(tmp_path)

    statement = data["read_store"].sales_for_seller(data["seller_a"])

    assert len(statement["sales"]) == 1
    sale = statement["sales"][0]
    assert sale["publication_id"] == "publication-a"
    assert sale["seller_earned_minor"] == 450
    assert sale["seller_reversed_minor"] == 225
    assert sale["seller_net_minor"] == 225
    assert sale["status"] == "partially_refunded"
    assert statement["totals_by_currency"] == [
        {
            "currency": "GBP",
            "sales_count": 1,
            "seller_earned_minor": 450,
            "seller_reversed_minor": 225,
            "seller_net_minor": 225,
        }
    ]


def test_seller_totals_are_all_time_even_when_activity_rows_are_limited(tmp_path):
    data = _fixture(tmp_path)
    _verified_sale(
        orders=data["orders"],
        settlements=data["settlements"],
        fees=data["fees"],
        buyer_id=data["buyer_b"],
        seller_id=data["seller_a"],
        suffix="paid_a_second",
        publication_id="publication-a-second",
    )

    statement = data["read_store"].sales_for_seller(data["seller_a"], limit=1)

    assert len(statement["sales"]) == 1
    assert statement["totals_by_currency"] == [
        {
            "currency": "GBP",
            "sales_count": 2,
            "seller_earned_minor": 900,
            "seller_reversed_minor": 225,
            "seller_net_minor": 675,
        }
    ]


def test_marketplace_account_api_requires_session_and_ignores_cross_account_user_parameters(tmp_path, monkeypatch):
    data = _fixture(tmp_path)
    monkeypatch.setattr(stripe_billing, "accounts", data["accounts"])
    monkeypatch.setattr(marketplace_accounting, "marketplace_account_store", data["read_store"])

    app = FastAPI()
    app.include_router(marketplace_accounting.router)
    client = TestClient(app)

    assert client.get("/api/marketplace/account/purchases").status_code == 401

    response = client.get(
        f"/api/marketplace/account/purchases?user_id={data['seller_a']}",
        headers={"Authorization": f"Bearer {data['buyer_a_token']}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["account_scope"] == "authenticated_user_only"
    assert body["count"] == 1
    assert body["purchases"][0]["publication_id"] == "publication-a"

    seller_response = client.get(
        "/api/marketplace/account/seller",
        headers={"Authorization": f"Bearer {data['seller_a_token']}"},
    )
    assert seller_response.status_code == 200
    seller_body = seller_response.json()
    assert len(seller_body["sales"]) == 1
    assert seller_body["sales"][0]["publication_id"] == "publication-a"
    assert seller_body["payout_initiated_by_this_endpoint"] is False

    assert client.get(
        "/api/marketplace/account/purchases?limit=101",
        headers={"Authorization": f"Bearer {data['buyer_a_token']}"},
    ).status_code == 422


def test_hardened_finance_router_mounts_marketplace_account_routes():
    app = FastAPI()
    app.include_router(hardened_billing_router)
    client = TestClient(app)

    openapi_paths = set(client.get("/openapi.json").json()["paths"])
    assert "/api/marketplace/account/purchases" in openapi_paths
    assert "/api/marketplace/account/seller" in openapi_paths

    page = client.get("/marketplace/account", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/signin"
