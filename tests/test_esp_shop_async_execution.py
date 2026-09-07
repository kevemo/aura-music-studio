from __future__ import annotations

import json

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_shop_async_execution import (
    AsyncProviderExecutionStore,
    EXECUTE_PATH,
    PROVIDER_PENDING,
    RECONCILE_PATH,
)
from aura_music_studio.esp_shop_automation import (
    ActionDecision,
    ActionPrepare,
    ConnectionCreate,
    SafetyPolicyUpdate,
    ShopAutomationStore,
)
from aura_music_studio.esp_shop_automation_overlay import router as shop_router
from aura_music_studio.esp_shop_provider_runtime import (
    ProviderRuntimeStore,
    clear_provider_adapters,
    register_provider_adapter,
)
from aura_music_studio.esp_shopify_provider import InMemoryShopSecretBackend
from aura_music_studio.esp_shopify_async_provider import ShopifyAsyncProviderAdapter


def _user(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup(
        "creator@example.com", "Creator", "a-very-secure-test-password", "base"
    )
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    return accounts, user


class PendingAdapter:
    provider = "shippo"

    def __init__(self, reconcile_results=None):
        self.execute_calls = 0
        self.reconcile_calls = 0
        self.reconcile_results = list(reconcile_results or [])

    def authorization_url(self, connection, *, state, callback_url):
        self.state = state
        return f"https://provider.example/oauth?state={state}"

    def exchange_oauth_code(self, connection, *, code, callback_url):
        return {
            "secret_ref": "vault://shippo/creator-credential",
            "external_account_ref": "shippo-account-1",
            "scopes": ["labels", "tracking"],
        }

    def execute(self, action, connection, *, secret_ref):
        self.execute_calls += 1
        assert secret_ref == "vault://shippo/creator-credential"
        return {
            "pending": True,
            "execution_ref": "shippo-job-123",
            "metadata": {"status": "queued", "access_token": "must-not-leak"},
        }

    def reconcile(self, action, connection, *, secret_ref, execution_ref):
        self.reconcile_calls += 1
        assert execution_ref == "shippo-job-123"
        if not self.reconcile_results:
            return {
                "pending": True,
                "execution_ref": execution_ref,
                "metadata": {"status": "still_processing"},
            }
        value = self.reconcile_results.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def diagnostics(self):
        return {"configured": True}


class ExplicitApprovalAdapter(PendingAdapter):
    @staticmethod
    def requires_explicit_approval(action):
        return True


class BadReferenceAdapter(PendingAdapter):
    def reconcile(self, action, connection, *, secret_ref, execution_ref):
        return {
            "success": True,
            "execution_ref": "different-provider-job",
            "metadata": {},
        }


def _connected_provider(tmp_path, adapter=None):
    clear_provider_adapters()
    accounts, user = _user(tmp_path)
    base = ShopAutomationStore(accounts.db_path)
    runtime = ProviderRuntimeStore(accounts.db_path)
    adapter = adapter or PendingAdapter()
    register_provider_adapter("shippo", adapter)
    connection = base.add_connection(
        user["id"],
        user["id"],
        ConnectionCreate(
            provider="shippo",
            account_label="Shipping",
            external_account_ref="shippo-account-1",
            scopes=["labels", "tracking"],
        ),
    )
    runtime.begin_oauth(
        connection["id"],
        user["id"],
        callback_url="https://pulsar.example/command-center/shop-automation/oauth/shippo/callback",
    )
    runtime.complete_oauth("shippo", state=adapter.state, code="verified-code")
    return accounts, user, base, AsyncProviderExecutionStore(str(accounts.db_path)), adapter


def _approved_label(base, user_id):
    action = base.prepare_action(
        user_id,
        user_id,
        ActionPrepare(
            provider="shippo",
            action_type="buy_label",
            external_object_ref="order-1",
            payload={"quantity": 1},
            estimated_spend_minor=450,
            currency="GBP",
        ),
    )
    assert action["status"] == "awaiting_approval"
    return base.decide_action(
        action["id"], user_id, user_id, ActionDecision(approve=True, note="Approved one label")
    )


def test_pending_provider_action_is_locked_against_double_execution(tmp_path):
    _accounts, user, base, execution, adapter = _connected_provider(tmp_path)
    action = _approved_label(base, user["id"])
    result = execution.execute_action(action["id"], user["id"], actor=user["id"])
    assert result["provider_pending"] is True
    assert result["provider_execution_confirmed"] is False
    assert result["automatic_retry_allowed"] is False
    assert result["action"]["status"] == PROVIDER_PENDING
    assert result["action"]["provider_execution_ref"] == "shippo-job-123"
    assert result["action"]["executed_at"] is None
    assert adapter.execute_calls == 1

    with pytest.raises(ValueError, match="not eligible"):
        execution.execute_action(action["id"], user["id"], actor=user["id"])
    assert adapter.execute_calls == 1


def test_pending_reconciliation_can_remain_pending_then_finish_successfully(tmp_path):
    adapter = PendingAdapter(
        reconcile_results=[
            {
                "pending": True,
                "execution_ref": "shippo-job-123",
                "metadata": {"status": "processing"},
            },
            {
                "success": True,
                "execution_ref": "shippo-job-123",
                "metadata": {"status": "complete", "label_id": "label-1"},
            },
        ]
    )
    _accounts, user, base, execution, _adapter = _connected_provider(tmp_path, adapter)
    action = _approved_label(base, user["id"])
    execution.execute_action(action["id"], user["id"], actor=user["id"])

    first = execution.reconcile_action(action["id"], user["id"], actor=user["id"])
    assert first["action"]["status"] == PROVIDER_PENDING
    assert first["provider_execution_confirmed"] is False

    final = execution.reconcile_action(action["id"], user["id"], actor=user["id"])
    assert final["action"]["status"] == "executed"
    assert final["action"]["executed_at"]
    assert final["provider_execution_confirmed"] is True
    assert adapter.execute_calls == 1
    assert adapter.reconcile_calls == 2


def test_transient_reconciliation_failure_leaves_external_job_pending(tmp_path):
    adapter = PendingAdapter(reconcile_results=[RuntimeError("provider timeout")])
    _accounts, user, base, execution, _adapter = _connected_provider(tmp_path, adapter)
    action = _approved_label(base, user["id"])
    execution.execute_action(action["id"], user["id"], actor=user["id"])

    with pytest.raises(RuntimeError, match="remains pending"):
        execution.reconcile_action(action["id"], user["id"], actor=user["id"])
    current = base.action(action["id"], user["id"])
    assert current["status"] == PROVIDER_PENDING
    assert current["provider_execution_ref"] == "shippo-job-123"
    assert current["executed_at"] is None


def test_confirmed_provider_failure_is_terminal_without_retry(tmp_path):
    adapter = PendingAdapter(
        reconcile_results=[
            {
                "failed": True,
                "execution_ref": "shippo-job-123",
                "metadata": {"status": "failed", "reason": "carrier rejected"},
            }
        ]
    )
    _accounts, user, base, execution, _adapter = _connected_provider(tmp_path, adapter)
    action = _approved_label(base, user["id"])
    execution.execute_action(action["id"], user["id"], actor=user["id"])
    result = execution.reconcile_action(action["id"], user["id"], actor=user["id"])
    assert result["action"]["status"] == "failed"
    assert result["provider_execution_confirmed"] is False
    with pytest.raises(ValueError):
        execution.execute_action(action["id"], user["id"], actor=user["id"])


def test_reconciliation_reference_mismatch_is_rejected_and_action_stays_pending(tmp_path):
    adapter = BadReferenceAdapter()
    _accounts, user, base, execution, _adapter = _connected_provider(tmp_path, adapter)
    action = _approved_label(base, user["id"])
    execution.execute_action(action["id"], user["id"], actor=user["id"])
    with pytest.raises(RuntimeError, match="reference does not match"):
        execution.reconcile_action(action["id"], user["id"], actor=user["id"])
    assert base.action(action["id"], user["id"])["status"] == PROVIDER_PENDING


def test_provider_can_require_explicit_approval_even_inside_preapproved_spend_ceiling(tmp_path):
    adapter = ExplicitApprovalAdapter()
    _accounts, user, base, execution, _adapter = _connected_provider(tmp_path, adapter)
    base.save_policy(
        user["id"],
        user["id"],
        SafetyPolicyUpdate(
            require_purchase_confirmation=False,
            preapproved_spend_minor=1000,
            currency="GBP",
        ),
    )
    action = base.prepare_action(
        user["id"],
        user["id"],
        ActionPrepare(
            provider="shippo",
            action_type="buy_label",
            payload={"quantity": 1},
            estimated_spend_minor=400,
            currency="GBP",
        ),
    )
    assert action["status"] == "prepared"
    with pytest.raises(PermissionError, match="explicit human approval"):
        execution.execute_action(action["id"], user["id"], actor=user["id"])
    assert base.action(action["id"], user["id"])["status"] == "awaiting_approval"
    assert adapter.execute_calls == 0


def test_provider_receipt_metadata_redacts_token_like_values(tmp_path):
    _accounts, user, base, execution, _adapter = _connected_provider(tmp_path)
    action = _approved_label(base, user["id"])
    execution.execute_action(action["id"], user["id"], actor=user["id"])
    with execution.runtime._connect() as con:
        row = con.execute(
            "SELECT metadata_json FROM esp_shop_provider_receipts WHERE action_id=? ORDER BY created_at DESC LIMIT 1",
            (action["id"],),
        ).fetchone()
    metadata = json.loads(row[0])
    assert metadata["access_token"] == "[redacted]"
    assert "must-not-leak" not in str(metadata)


class StubShopifyAsyncAdapter(ShopifyAsyncProviderAdapter):
    def __init__(self):
        super().__init__(
            client_id="client-id",
            client_secret="client-secret",
            secret_backend=InMemoryShopSecretBackend(),
        )
        self.started = []
        self.polls = []
        self.start_result = {
            "execution_ref": "gid://shopify/ShippingLabelPurchaseResult/123",
            "status": "PENDING_PURCHASE",
            "done": False,
            "errors": [],
        }
        self.poll_result = {
            "execution_ref": "gid://shopify/ShippingLabelPurchaseResult/123",
            "status": "PURCHASED",
            "done": True,
            "errors": [],
            "shipping_label_ids": ["gid://shopify/ShippingLabel/456"],
        }

    def start_shipping_label_purchase(self, secret_ref, purchase_input):
        self.started.append((secret_ref, purchase_input))
        return dict(self.start_result)

    def poll_shipping_label_purchase(self, secret_ref, execution_ref):
        self.polls.append((secret_ref, execution_ref))
        return dict(self.poll_result)


def _shopify_action(*, notify=False, preferred=None, accept_default=False, quantity=1, note="Approved"):
    purchase = {
        "fulfillmentOrderId": "gid://shopify/FulfillmentOrder/42",
        "shippingDatetime": "2026-08-28T10:00:00Z",
        "notifyCustomer": notify,
    }
    if preferred is not None:
        purchase["preferredRateSelection"] = preferred
    return {
        "id": "action-1",
        "user_id": "user-1",
        "provider": "shopify",
        "action_type": "purchase_shipping_label",
        "status": "approved",
        "approval_note": note,
        "payload": {
            "quantity": quantity,
            "acceptShopifySelectedRate": accept_default,
            "shippingLabelPurchase": purchase,
        },
    }


def test_shopify_async_capability_is_only_advertised_with_required_write_scopes():
    adapter = StubShopifyAsyncAdapter()
    assert adapter._runtime_capabilities({"write_orders"}) == ["orders"]
    caps = adapter._runtime_capabilities(
        {"write_orders", "write_merchant_managed_fulfillment_orders"}
    )
    assert caps == ["orders", "shipping_labels"]


def test_shopify_label_requires_human_note_and_rate_choice_or_explicit_default_ack():
    adapter = StubShopifyAsyncAdapter()
    policy = {"allow_customer_notifications": False}
    with pytest.raises(PermissionError, match="approval note"):
        adapter.validate_before_execute(_shopify_action(accept_default=True, note=""), policy)
    with pytest.raises(PermissionError, match="provider-selected rate"):
        adapter.validate_before_execute(_shopify_action(), policy)

    adapter.validate_before_execute(_shopify_action(accept_default=True), policy)
    adapter.validate_before_execute(
        _shopify_action(preferred={"carrierCode": "UPS", "serviceCode": "GROUND"}), policy
    )


def test_shopify_customer_notification_and_quantity_follow_local_safety_policy():
    adapter = StubShopifyAsyncAdapter()
    with pytest.raises(PermissionError, match="Customer notifications"):
        adapter.validate_before_execute(
            _shopify_action(notify=True, accept_default=True),
            {"allow_customer_notifications": False},
        )
    adapter.validate_before_execute(
        _shopify_action(notify=True, accept_default=True),
        {"allow_customer_notifications": True},
    )
    with pytest.raises(ValueError, match="exactly one label"):
        adapter.validate_before_execute(
            _shopify_action(quantity=2, accept_default=True),
            {"allow_customer_notifications": False},
        )


def test_shopify_async_adapter_maps_pending_start_and_terminal_poll_receipts():
    adapter = StubShopifyAsyncAdapter()
    action = _shopify_action(accept_default=True)
    started = adapter.execute(
        action,
        {"provider": "shopify"},
        secret_ref="vault://shopify/credential",
    )
    assert started["pending"] is True
    assert started["execution_ref"].endswith("/123")
    assert started["metadata"]["shopify_status"] == "PENDING_PURCHASE"
    reconciled = adapter.reconcile(
        action,
        {"provider": "shopify"},
        secret_ref="vault://shopify/credential",
        execution_ref=started["execution_ref"],
    )
    assert reconciled["success"] is True
    assert reconciled["metadata"]["shopify_status"] == "PURCHASED"
    assert reconciled["metadata"]["shipping_label_ids"] == ["gid://shopify/ShippingLabel/456"]


def test_shop_overlay_replaces_generic_execute_and_adds_reconcile_route():
    execute_routes = [
        route for route in shop_router.routes
        if getattr(route, "path", None) == EXECUTE_PATH
        and "POST" in (getattr(route, "methods", set()) or set())
    ]
    reconcile_routes = [
        route for route in shop_router.routes
        if getattr(route, "path", None) == RECONCILE_PATH
        and "POST" in (getattr(route, "methods", set()) or set())
    ]
    assert len(execute_routes) == 1
    assert getattr(execute_routes[0], "endpoint").__name__ == "execute_async_provider_action_api"
    assert len(reconcile_routes) == 1
    assert getattr(reconcile_routes[0], "endpoint").__name__ == "reconcile_async_provider_action_api"
