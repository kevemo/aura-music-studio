import sqlite3

from aura_music_studio.accounts import AccountStore
from aura_music_studio.billing import payment_instructions, payment_option, public_payment_options
from aura_music_studio.brand_migration import rebrand_text
from aura_music_studio.subscriptions import SubscriptionLedger


def test_payment_options_are_currency_explicit_and_keep_legacy_alias():
    member = payment_option("base")
    pro = payment_option("pro")
    assert member is not None and pro is not None
    assert member.currency == "GBP"
    assert member.amount == "4.99"
    assert member.amount_minor == 499
    assert member.amount_usd == "4.99"
    assert pro.currency == "GBP"
    assert pro.amount == "9.99"
    assert pro.amount_minor == 999
    assert pro.amount_usd == "9.99"

    public = public_payment_options()
    assert public[0]["currency"] == "GBP"
    assert public[0]["amount"] == "4.99"
    assert public[0]["amount_minor"] == 499
    assert public[0]["amount_usd"] == "4.99"
    assert public[1]["currency"] == "GBP"
    assert public[1]["amount"] == "9.99"
    assert public[1]["amount_minor"] == 999
    assert public[1]["amount_usd"] == "9.99"

    instructions = payment_instructions("pro")
    assert instructions["currency"] == "GBP"
    assert instructions["amount"] == "9.99"
    assert instructions["amount_minor"] == 999
    assert instructions["display_amount"] == "£9.99/month"
    assert instructions["amount_usd"] == "9.99"


def test_legacy_public_price_symbols_are_migrated_narrowly_to_gbp():
    text = "Basic $4.99 / month · Pro $9.99 / month · unrelated $14.99 value"
    migrated = rebrand_text(text)
    assert "Basic £4.99 / month" in migrated
    assert "Pro £9.99 / month" in migrated
    assert "$14.99" in migrated


def test_subscription_ledger_upgrades_legacy_amount_usd_rows_in_place(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    signup = store.signup("legacy@example.com", "Legacy", "verysecurepassword", "base")

    with sqlite3.connect(store.db_path) as con:
        con.execute(
            """CREATE TABLE subscription_payments (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                payment_reference TEXT NOT NULL UNIQUE,
                amount_usd TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                verified_at TEXT NOT NULL
            )"""
        )
        con.execute(
            """INSERT INTO subscription_payments
               (id,user_id,plan_id,payment_reference,amount_usd,period_start,period_end,verified_at)
               VALUES ('legacy-payment',?,?,?,?,?,?,?)""",
            (
                signup.user_id,
                "base",
                "LEGACY-REF",
                "4.99",
                "2026-08-01T00:00:00+00:00",
                "2026-09-01T00:00:00+00:00",
                "2026-08-01T00:00:00+00:00",
            ),
        )

    SubscriptionLedger(store)
    with sqlite3.connect(store.db_path) as con:
        con.row_factory = sqlite3.Row
        columns = {row[1] for row in con.execute("PRAGMA table_info(subscription_payments)")}
        row = con.execute("SELECT * FROM subscription_payments WHERE id='legacy-payment'").fetchone()

    assert {"amount", "amount_minor", "currency", "amount_usd"}.issubset(columns)
    assert row is not None
    assert row["amount_usd"] == "4.99"
    assert row["amount"] == "4.99"
    assert row["amount_minor"] == 499
    assert row["currency"] == "GBP"


def test_verified_payment_writes_canonical_and_compatibility_amounts(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    signup = store.signup("paid@example.com", "Paid", "verysecurepassword", "pro")
    with sqlite3.connect(store.db_path) as con:
        con.execute(
            "UPDATE users SET status='approved_pending_payment', requested_plan_id='pro' WHERE id=?",
            (signup.user_id,),
        )

    ledger = SubscriptionLedger(store)
    status = ledger.verify_payment(signup.user_id, "pro", "PAYMENT-GBP-999")
    assert status["user"]["plan_id"] == "pro"

    with sqlite3.connect(store.db_path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT amount_usd,amount,amount_minor,currency FROM subscription_payments WHERE payment_reference=?",
            ("PAYMENT-GBP-999",),
        ).fetchone()
    assert row is not None
    assert row["amount_usd"] == "9.99"
    assert row["amount"] == "9.99"
    assert row["amount_minor"] == 999
    assert row["currency"] == "GBP"
