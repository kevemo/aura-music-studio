from pathlib import Path

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.credit_wallet import CreditWalletStore


def _user(db_path: Path) -> str:
    accounts = AccountStore(db_path)
    signup = accounts.signup("credits@example.com", "Credit User", "strongpassword1", "free")
    return signup.user_id


def test_credit_wallet_grant_spend_and_history(tmp_path):
    db_path = tmp_path / "credits.sqlite3"
    user_id = _user(db_path)
    wallet = CreditWalletStore(db_path)

    grant = wallet.grant(user_id, 50, reason="Launch bonus", actor="Kev", reference="grant-1")
    assert grant["balance_after"] == 50
    assert wallet.balance(user_id) == 50

    spend = wallet.spend(user_id, 12, reason="Basic premium render", reference="spend-1")
    assert spend["amount"] == -12
    assert spend["balance_after"] == 38
    assert wallet.balance(user_id) == 38

    rows = wallet.transactions(user_id)
    assert [row["reference"] for row in rows] == ["spend-1", "grant-1"]


def test_credit_reference_is_idempotent(tmp_path):
    db_path = tmp_path / "credits.sqlite3"
    user_id = _user(db_path)
    wallet = CreditWalletStore(db_path)

    first = wallet.grant(user_id, 25, reason="Purchase", actor="billing", reference="payment-abc")
    second = wallet.grant(user_id, 25, reason="Purchase retry", actor="billing", reference="payment-abc")

    assert first["id"] == second["id"]
    assert wallet.balance(user_id) == 25


def test_credit_wallet_rejects_overspend(tmp_path):
    db_path = tmp_path / "credits.sqlite3"
    user_id = _user(db_path)
    wallet = CreditWalletStore(db_path)
    wallet.grant(user_id, 5, reason="Starter", actor="Mary")

    with pytest.raises(ValueError, match="Insufficient credits"):
        wallet.spend(user_id, 6, reason="Premium render")

    assert wallet.balance(user_id) == 5


def test_credits_do_not_change_membership_or_esp_roles(tmp_path):
    db_path = tmp_path / "credits.sqlite3"
    user_id = _user(db_path)
    accounts = AccountStore(db_path)
    before = accounts.get_user(user_id)

    wallet = CreditWalletStore(db_path)
    wallet.grant(user_id, 100, reason="Promotional credits", actor="ESP Owner")

    after = accounts.get_user(user_id)
    assert after["status"] == before["status"]
    assert after["plan_id"] == before["plan_id"]
    assert after["requested_plan_id"] == before["requested_plan_id"]
