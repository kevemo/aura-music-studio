from __future__ import annotations

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_shop_automation import (
    ActionDecision,
    ActionPrepare,
    ConnectionCreate,
    SHOP_TIERS,
    SafetyPolicyUpdate,
    ShopAutomationStore,
    WorkflowCreate,
)
from aura_music_studio.esp_shop_automation_overlay import router


def _user(tmp_path, email="creator@example.com"):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup(email, "Shop Creator", "a-very-secure-test-password", "base")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    return accounts, user


def test_shop_tiers_map_existing_paid_memberships_without_granting_esp_role():
    assert SHOP_TIERS["free"] == {"id": "training_only", "automation": False, "unlimited": False}
    assert SHOP_TIERS["base"] == {"id": "basic", "automation": True, "unlimited": False}
    assert SHOP_TIERS["pro"] == {"id": "unlimited", "automation": True, "unlimited": True}
    assert "esp_role" not in SHOP_TIERS["base"]
    assert "esp_role" not in SHOP_TIERS["pro"]


def test_connection_is_pending_and_never_returns_raw_token(tmp_path):
    accounts, user = _user(tmp_path)
    store = ShopAutomationStore(accounts.db_path)
    connection = store.add_connection(
        user["id"], user["id"],
        ConnectionCreate(
            provider="shopify",
            account_label="Main Shop",
            external_account_ref="shop-001",
            scopes=["catalog", "orders", "shipping_labels", "unknown_scope"],
        ),
    )
    assert connection["status"] == "pending_oauth"
    assert connection["raw_token_stored"] is False
    assert connection["scopes"] == ["catalog", "orders", "shipping_labels"]
    assert "token" not in connection


def test_sensitive_shipping_purchase_is_approval_gated_by_default(tmp_path):
    accounts, user = _user(tmp_path)
    store = ShopAutomationStore(accounts.db_path)
    action = store.prepare_action(
        user["id"], user["id"],
        ActionPrepare(
            provider="shopify",
            action_type="purchase_shipping_label",
            external_object_ref="fulfillment-42",
            payload={"rate_id": "rate-1"},
            estimated_spend_minor=525,
            currency="GBP",
        ),
    )
    assert action["status"] == "awaiting_approval"
    assert action["provider_execution_confirmed"] is False
    approved = store.decide_action(
        action["id"], user["id"], user["id"],
        ActionDecision(approve=True, note="Approve this label purchase"),
    )
    assert approved["status"] == "approved"
    assert approved["provider_execution_confirmed"] is False
    assert approved["executed_at"] is None


def test_preapproved_ceiling_still_gates_spend_above_limit(tmp_path):
    accounts, user = _user(tmp_path)
    store = ShopAutomationStore(accounts.db_path)
    store.save_policy(
        user["id"], user["id"],
        SafetyPolicyUpdate(
            require_purchase_confirmation=False,
            preapproved_spend_minor=500,
            currency="GBP",
            allow_bulk_labels=True,
        ),
    )
    within = store.prepare_action(
        user["id"], user["id"],
        ActionPrepare(
            provider="shippo", action_type="purchase_shipping_label",
            external_object_ref="order-1", estimated_spend_minor=450,
        ),
    )
    above = store.prepare_action(
        user["id"], user["id"],
        ActionPrepare(
            provider="shippo", action_type="purchase_shipping_label",
            external_object_ref="order-2", estimated_spend_minor=700,
        ),
    )
    assert within["status"] == "prepared"
    assert above["status"] == "awaiting_approval"


def test_workflow_is_persisted_as_intent_not_external_execution(tmp_path):
    accounts, user = _user(tmp_path)
    store = ShopAutomationStore(accounts.db_path)
    workflow = store.create_workflow(
        user["id"], user["id"],
        WorkflowCreate(
            name="Low inventory warning",
            trigger_type="inventory_below_threshold",
            conditions=[{"field": "available", "op": "lt", "value": 5}],
            actions=[{"type": "notify_creator"}],
            status="active",
        ),
    )
    assert workflow["status"] == "active"
    assert workflow["actions"] == [{"type": "notify_creator"}]
    dashboard = store.dashboard(user["id"], "pro")
    assert dashboard["external_execution_adapter_configured"] is False
    assert dashboard["raw_oauth_tokens_returned"] is False
    assert dashboard["esp_creator_role_required"] is True


def test_member_router_does_not_allow_self_asserted_provider_connection_status():
    route_methods = {
        (getattr(route, "path", None), frozenset(getattr(route, "methods", set()) or set()))
        for route in router.routes
    }
    assert any(path == "/command-center/api/shop-automation/connections" and "POST" in methods for path, methods in route_methods)
    assert not any(
        path == "/command-center/api/shop-automation/connections/{connection_id}" and "PATCH" in methods
        for path, methods in route_methods
    )
    assert any(path == "/command-center/api/shop-automation/actions/{action_id}/decision" and "POST" in methods for path, methods in route_methods)
