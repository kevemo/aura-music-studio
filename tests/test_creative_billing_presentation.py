from __future__ import annotations

import inspect

import pytest

from aura_music_studio.billing import payment_instructions, payment_option, public_payment_options
from aura_music_studio.web_portal import _pricing_cards
import aura_music_studio.web_portal as web_portal


def test_default_basic_and_pro_payment_instructions_use_canonical_gbp_monthly_prices():
    basic = payment_instructions("base")
    pro = payment_instructions("pro")

    assert basic["billing_period"] == "monthly"
    assert basic["amount"] == "4.99"
    assert basic["amount_minor"] == 499
    assert basic["currency"] == "GBP"
    assert basic["display_amount"] == "£4.99/month"

    assert pro["billing_period"] == "monthly"
    assert pro["amount"] == "9.99"
    assert pro["amount_minor"] == 999
    assert pro["currency"] == "GBP"
    assert pro["display_amount"] == "£9.99/month"


def test_annual_unlimited_pro_uses_canonical_price_only_with_dedicated_route(monkeypatch):
    monkeypatch.setenv("LSS_PAYPAL_PRO_ANNUAL_URL", "https://www.paypal.com/invoice/p/#PROANNUALTEST")

    annual = payment_instructions("pro", "annual")

    assert annual["billing_period"] == "annual"
    assert annual["amount"] == "99.00"
    assert annual["amount_minor"] == 9900
    assert annual["currency"] == "GBP"
    assert annual["display_amount"] == "£99.00/year"
    assert annual["url"] == "https://www.paypal.com/invoice/p/#PROANNUALTEST"
    assert annual["automatic_activation"] is False


def test_annual_pro_never_reuses_monthly_paypal_invoice(monkeypatch):
    monkeypatch.delenv("LSS_PAYPAL_PRO_ANNUAL_URL", raising=False)
    monkeypatch.setenv("LSS_PAYPAL_PRO_URL", "https://www.paypal.com/invoice/p/#MONTHLYONLY")

    with pytest.raises(ValueError, match="Annual Unlimited Pro PayPal route is not configured"):
        payment_option("pro", "annual")


def test_basic_annual_price_is_not_invented():
    with pytest.raises(ValueError, match="Annual billing is not available"):
        payment_instructions("base", "annual")


def test_public_manual_payment_options_are_explicitly_monthly_and_gbp():
    options = public_payment_options()

    assert [row["plan_id"] for row in options] == ["base", "pro"]
    assert {row["billing_period"] for row in options} == {"monthly"}
    assert {row["currency"] for row in options} == {"GBP"}
    assert [row["amount_minor"] for row in options] == [499, 999]


def test_public_pricing_cards_consume_canonical_catalogue_and_show_pro_annual_price():
    html = _pricing_cards()

    assert "£4.99/month" in html
    assert "£9.99/month" in html
    assert "£99.00/year" in html
    assert "$4.99" not in html
    assert "$9.99" not in html
    assert "Aura OS + Aura Sec included" in html
    assert "/signup?plan=pro&billing_period=annual" in html
    assert "/signup?plan=base&billing_period=annual" not in html


def test_web_portal_uses_current_basic_naming_and_period_aware_copy():
    source = inspect.getsource(web_portal)

    assert "monthly_price_usd" not in source
    assert "amount_usd" not in source
    assert "Complete the $" not in source
    assert "Member gives one confirmed full track" not in source
    assert "Basic is £4.99/month" in source
    assert "Unlimited Pro is £9.99/month or £99/year" in source
    assert "billing_period" in source
