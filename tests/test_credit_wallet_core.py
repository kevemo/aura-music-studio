from pathlib import Path

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.credit_wallet import CreditWalletStore
from aura_music_studio.subscriptions import SubscriptionLedger


def _user(db_path: Path, *, email: str = "credits@example.com", plan: str = "free") -> tuple[AccountStore, str]:
    accounts = AccountStore(db_path)
    signup = accounts.signup(email, "Credit User", "strongpassword1", plan)
    return accounts, signup.user_id


def test_credit_wallet_grant_spend_history_and_integrity(tmp_path):
    db_path = tmp_path / "credits.sqlite3"
    _accounts, user_id = _user(db_path)
    wallet = CreditWalletStore(db_path)

    grant = wallet.grant(user_id, 50, reason="Launch bonus", actor="system", reference="grant-1")
    assert grant["balance_after"] == 50
    spend = wallet.spend(user_id, 12, reason="Premium render", reference="spend-1")
    assert spend["amount"] == -12
    assert spend["balance_after"] == 38
    assert wallet.balance(user_id) == 38
    assert [row["reference"] for row in wallet.transactions(user_id)] == ["spend-1", "grant-1"]

    integrity = wallet.verify_integrity(user_id)
    assert integrity["valid"] is True
    assert integrity["calculated_balance"] == 38
    assert integrity["stored_balance"] == 38
    assert integrity["unit"] == "PULSAR_CREDIT"


def test_credit_reference_is_idempotent_but_cannot_be_repurposed(tmp_path):
    db_path = tmp_path / "credits.sqlite3"
    _accounts, user_id = _user(db_path)
    wallet = CreditWalletStore(db_path)

    first = wallet.grant(user_id, 25, reason="Purchase", actor="billing", reference="payment-abc")
    second = wallet.grant(user_id, 25, reason="Provider retry", actor="billing", reference="payment-abc")
    assert first["id"] == second["id"]
    assert wallet.balance(user_id) == 25

    with pytest.raises(ValueError, match="different transaction"):
        wallet.grant(user_id, 30, reason="Changed amount", actor="billing", reference="payment-abc")
    with pytest.raises(ValueError, match="different transaction"):
        wallet.adjust(
            user_id,
            25,
            kind="refund",
            reason="Changed transaction type",
            actor="billing",
            reference="payment-abc",
        )


def test_credit_wallet_rejects_overspend_and_unknown_members(tmp_path):
    db_path = tmp_path / "credits.sqlite3"
    _accounts, user_id = _user(db_path)
    wallet = CreditWalletStore(db_path)
    wallet.grant(user_id, 5, reason="Starter", actor="system")

    with pytest.raises(ValueError, match="Insufficient credits"):
        wallet.spend(user_id, 6, reason="Premium render")
    assert wallet.balance(user_id) == 5

    with pytest.raises(ValueError, match="Member account not found"):
        wallet.grant("not-a-member", 10, reason="Invalid", actor="system")


def test_credits_never_change_subscription_or_account_plan(tmp_path):
    db_path = tmp_path / "credits.sqlite3"
    accounts, user_id = _user(db_path, plan="pro")
    subscriptions = SubscriptionLedger(accounts)
    before_user = accounts.get_user(user_id)
    before_subscription = subscriptions.get(user_id)

    wallet = CreditWalletStore(db_path)
    wallet.grant(user_id, 100, reason="Promotional credits", actor="ESP Owner")
    wallet.spend(user_id, 7, reason="Optional add-on")

    after_user = accounts.get_user(user_id)
    after_subscription = subscriptions.get(user_id)
    assert after_user is not None and before_user is not None
    assert after_user["status"] == before_user["status"]
    assert after_user["plan_id"] == before_user["plan_id"]
    assert after_user["requested_plan_id"] == before_user["requested_plan_id"]
    assert after_user["billing_status"] == before_user["billing_status"]
    assert after_subscription == before_subscription


def test_integrity_check_detects_tampered_cached_balance(tmp_path):
    db_path = tmp_path / "credits.sqlite3"
    _accounts, user_id = _user(db_path)
    wallet = CreditWalletStore(db_path)
    wallet.grant(user_id, 40, reason="Grant", actor="system")

    with wallet._connect() as con:
        con.execute("UPDATE credit_wallets SET balance=41 WHERE user_id=?", (user_id,))

    integrity = wallet.verify_integrity(user_id)
    assert integrity["valid"] is False
    assert integrity["calculated_balance"] == 40
    assert integrity["stored_balance"] == 41


def test_production_app_mounts_wallet_explicitly_without_usage_import_side_effect():
    app_source = Path("app.py").read_text(encoding="utf-8")
    usage_source = Path("aura_music_studio/usage_tracking.py").read_text(encoding="utf-8")
    assert "from aura_music_studio.credit_wallet import router as credit_wallet_router" in app_source
    assert "app.include_router(credit_wallet_router)" in app_source
    assert "credit_wallet_registration" not in usage_source
