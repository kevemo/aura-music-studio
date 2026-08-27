from __future__ import annotations

import sqlite3
from urllib.parse import parse_qs, urlparse

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_shop_automation import (
    ActionDecision,
    ActionPrepare,
    ConnectionCreate,
    SafetyPolicyUpdate,
    ShopAutomationStore,
)
from aura_music_studio.esp_shop_automation_overlay import router
from aura_music_studio.esp_shop_provider_runtime import (
    ProviderRuntimeStore,
    clear_provider_adapters,
    register_provider_adapter,
)


class FakeAdapter:
    provider = "shopify"

    def __init__(self):
        self.last_state = ""
        self.executions = []

    def authorization_url(self, connection: dict, *, state: str, callback_url: str) -> str:
        self.last_state = state
        return f"https://provider.example/oauth?state={state}&redirect_uri={callback_url}"

    def exchange_oauth_code(self, connection: dict, *, code: str, callback_url: str) -> dict:
        if code != "verified-code":
            raise RuntimeError("provider rejected code")
        return {
            "secret_ref": "vault://shopify/test-credential",
            "external_account_ref": "shop-123",
            "scopes": ["orders", "shipping_labels", "inventory", "fulfillment", "unknown"],
        }

    def execute(self, action: dict, connection: dict, *, secret_ref: str) -> dict:
        assert secret_ref == "vault://shopify/test-credential"
        self.executions.append(action["id"])
        return {
            "success": True,
            "execution_ref": f"shopify-exec-{action['id']}",
            "metadata": {
                "label_url": "https://provider.example/labels/123",
                "access_token": "must-never-leak",
                "nested": {"client_secret": "must-never-leak-either", "carrier": "Example Carrier"},
            },
        }

    def diagnostics(self) -> dict:
        return {"configured": True, "access_token": "must-never-leak"}


def _user(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup("creator@example.com", "Shop Creator", "a-very-secure-test-password", "base")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    return accounts, user


def _connected_shopify(tmp_path):
    clear_provider_adapters()
    accounts, user = _user(tmp_path)
    base = ShopAutomationStore(accounts.db_path)
    runtime = ProviderRuntimeStore(accounts.db_path)
    adapter = FakeAdapter()
    register_provider_adapter("shopify", adapter)
    connection = base.add_connection(
        user["id"], user["id"],
        ConnectionCreate(
            provider="shopify",
            account_label="Main Shop",
            external_account_ref="",
            scopes=["orders", "shipping_labels", "inventory", "fulfillment"],
        ),
    )
    started = runtime.begin_oauth(
        connection["id"], user["id"],
        callback_url="https://pulsar.example/command-center/shop-automation/oauth/shopify/callback",
    )
    completed = runtime.complete_oauth("shopify", state=adapter.last_state, code="verified-code")
    return accounts, user, base, runtime, adapter, started, completed


def test_oauth_state_is_one_time_hashed_and_raw_token_is_not_persisted(tmp_path):
    clear_provider_adapters()
    accounts, user = _user(tmp_path)
    base = ShopAutomationStore(accounts.db_path)
    runtime = ProviderRuntimeStore(accounts.db_path)
    adapter = FakeAdapter()
    register_provider_adapter("shopify", adapter)
    connection = base.add_connection(
        user["id"], user["id"],
        ConnectionCreate(provider="shopify", account_label="Main Shop", scopes=["orders", "shipping_labels"]),
    )

    started = runtime.begin_oauth(
        connection["id"], user["id"],
        callback_url="https://pulsar.example/command-center/shop-automation/oauth/shopify/callback",
    )
    assert started["state_stored_as_hash"] is True
    assert started["raw_oauth_token_stored"] is False
    assert adapter.last_state
    query = parse_qs(urlparse(started["authorization_url"]).query)
    assert query["state"] == [adapter.last_state]

    with sqlite3.connect(accounts.db_path) as con:
        row = con.execute(
            "SELECT state_sha256 FROM esp_shop_oauth_states WHERE connection_id=?",
            (connection["id"],),
        ).fetchone()
        assert row is not None
        assert row[0] != adapter.last_state
        assert len(row[0]) == 64
        tables = {name for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "esp_shop_provider_credentials" in tables

    completed = runtime.complete_oauth("shopify", state=adapter.last_state, code="verified-code")
    assert completed["oauth_verified"] is True
    assert completed["connection"]["status"] == "connected"
    assert completed["connection"]["scopes"] == ["fulfillment", "inventory", "orders", "shipping_labels"]
    assert completed["credential"]["present"] is True
    assert completed["credential"]["secret_ref_exposed"] is False
    assert "secret_ref" not in completed["credential"]
    assert "must-never-leak" not in str(completed)

    with pytest.raises(PermissionError, match="invalid or already consumed"):
        runtime.complete_oauth("shopify", state=adapter.last_state, code="verified-code")


def test_shipping_label_execution_requires_approval_and_verified_receipt(tmp_path):
    _accounts, user, base, runtime, adapter, _started, _completed = _connected_shopify(tmp_path)
    action = base.prepare_action(
        user["id"], user["id"],
        ActionPrepare(
            provider="shopify",
            action_type="purchase_shipping_label",
            external_object_ref="fulfillment-42",
            payload={"rate_id": "rate-1", "quantity": 1},
            estimated_spend_minor=525,
            currency="GBP",
        ),
    )
    assert action["status"] == "awaiting_approval"
    with pytest.raises(PermissionError, match="Approve this Shop action"):
        runtime.execute_action(action["id"], user["id"], actor=user["id"])

    approved = base.decide_action(
        action["id"], user["id"], user["id"],
        ActionDecision(approve=True, note="Approved shipping label purchase"),
    )
    assert approved["status"] == "approved"
    result = runtime.execute_action(action["id"], user["id"], actor=user["id"])
    assert result["provider_execution_confirmed"] is True
    assert result["action"]["status"] == "executed"
    assert result["action"]["provider_execution_ref"].startswith("shopify-exec-")
    assert result["receipt"]["metadata"]["access_token"] == "[redacted]"
    assert result["receipt"]["metadata"]["nested"]["client_secret"] == "[redacted]"
    assert result["receipt"]["metadata"]["nested"]["carrier"] == "Example Carrier"
    assert adapter.executions == [action["id"]]


def test_inventory_write_is_rechecked_at_execution_time(tmp_path):
    _accounts, user, base, runtime, _adapter, _started, _completed = _connected_shopify(tmp_path)
    action = base.prepare_action(
        user["id"], user["id"],
        ActionPrepare(
            provider="shopify", action_type="update_inventory",
            external_object_ref="variant-1", payload={"available": 8}, estimated_spend_minor=0,
        ),
    )
    assert action["status"] == "prepared"
    with pytest.raises(PermissionError, match="Inventory writes are disabled"):
        runtime.execute_action(action["id"], user["id"], actor=user["id"])
    assert base.action(action["id"], user["id"])["status"] == "prepared"

    base.save_policy(
        user["id"], user["id"],
        SafetyPolicyUpdate(allow_inventory_writes=True),
    )
    result = runtime.execute_action(action["id"], user["id"], actor=user["id"])
    assert result["action"]["status"] == "executed"


def test_lowered_spend_ceiling_forces_prepared_action_back_to_approval(tmp_path):
    _accounts, user, base, runtime, _adapter, _started, _completed = _connected_shopify(tmp_path)
    base.save_policy(
        user["id"], user["id"],
        SafetyPolicyUpdate(
            require_purchase_confirmation=False,
            preapproved_spend_minor=1000,
            currency="GBP",
        ),
    )
    action = base.prepare_action(
        user["id"], user["id"],
        ActionPrepare(
            provider="shopify", action_type="purchase_shipping_label",
            external_object_ref="fulfillment-99", payload={"quantity": 1}, estimated_spend_minor=700,
        ),
    )
    assert action["status"] == "prepared"

    base.save_policy(
        user["id"], user["id"],
        SafetyPolicyUpdate(
            require_purchase_confirmation=False,
            preapproved_spend_minor=100,
            currency="GBP",
        ),
    )
    with pytest.raises(PermissionError, match="requires explicit approval"):
        runtime.execute_action(action["id"], user["id"], actor=user["id"])
    assert base.action(action["id"], user["id"])["status"] == "awaiting_approval"


def test_unconfigured_provider_fails_truthfully(tmp_path):
    clear_provider_adapters()
    accounts, user = _user(tmp_path)
    base = ShopAutomationStore(accounts.db_path)
    runtime = ProviderRuntimeStore(accounts.db_path)
    connection = base.add_connection(
        user["id"], user["id"],
        ConnectionCreate(provider="shippo", account_label="Shipping", scopes=["labels"]),
    )
    with pytest.raises(RuntimeError, match="provider adapter is not configured"):
        runtime.begin_oauth(
            connection["id"], user["id"],
            callback_url="https://pulsar.example/command-center/shop-automation/oauth/shippo/callback",
        )
    assert connection["status"] == "pending_oauth"


def test_runtime_diagnostics_redact_adapter_secrets(tmp_path):
    _accounts, user, _base, runtime, _adapter, _started, _completed = _connected_shopify(tmp_path)
    diagnostics = runtime.diagnostics(user["id"])
    assert diagnostics["oauth_state_stored_as_hash"] is True
    assert diagnostics["raw_oauth_tokens_in_database"] is False
    assert diagnostics["execution_requires_verified_receipt"] is True
    assert diagnostics["safety_policy_rechecked_at_execution"] is True
    assert diagnostics["providers"]["shopify"]["access_token"] == "[redacted]"
    assert "vault://shopify/test-credential" not in str(diagnostics)
    assert all("secret_ref" not in row["credential"] for row in diagnostics["connections"])
    assert all(row["credential"].get("secret_ref_exposed") is False for row in diagnostics["connections"])


def test_overlay_mounts_runtime_routes_without_restoring_member_status_patch():
    route_methods = {
        (getattr(route, "path", None), frozenset(getattr(route, "methods", set()) or set()))
        for route in router.routes
    }
    assert any(path == "/command-center/api/shop-automation/runtime" and "GET" in methods for path, methods in route_methods)
    assert any(
        path == "/command-center/api/shop-automation/connections/{connection_id}/oauth/start" and "POST" in methods
        for path, methods in route_methods
    )
    assert any(
        path == "/command-center/api/shop-automation/actions/{action_id}/execute" and "POST" in methods
        for path, methods in route_methods
    )
    assert any(
        path == "/command-center/shop-automation/oauth/{provider}/callback" and "GET" in methods
        for path, methods in route_methods
    )
    assert not any(
        path == "/command-center/api/shop-automation/connections/{connection_id}" and "PATCH" in methods
        for path, methods in route_methods
    )
