from __future__ import annotations

import json

import pytest

from aura_music_studio.aura_sec_catalog import AuraSecCatalog, AuraSecSku


def test_catalog_is_not_for_sale_without_owner_configuration(monkeypatch):
    monkeypatch.delenv("AURA_SEC_SKUS_JSON", raising=False)
    state = AuraSecCatalog.from_environment().public_state()
    assert state["sale_configured"] is False
    assert state["checkout_enabled"] is False
    assert state["skus"] == []
    assert state["creative_plan_ids_accepted_as_security_sku"] is False


def test_configured_security_sku_stays_separate_from_creative_plans(monkeypatch):
    monkeypatch.setenv(
        "AURA_SEC_SKUS_JSON",
        json.dumps(
            [
                {
                    "id": "aura-sec-personal-test",
                    "display_name": "Aura Sec Personal Test",
                    "currency": "GBP",
                    "price_minor": 1299,
                    "period_days": 31,
                    "device_limit": 5,
                }
            ]
        ),
    )
    state = AuraSecCatalog.from_environment().public_state()
    assert state["sale_configured"] is True
    assert state["checkout_enabled"] is False
    assert state["creative_plan_ids_accepted_as_security_sku"] is False
    sku = state["skus"][0]
    assert sku["id"] == "aura-sec-personal-test"
    assert sku["price_major"] == "12.99"
    assert sku["currency"] == "GBP"
    assert sku["device_limit"] == 5


def test_invalid_commercial_configuration_fails_validation(monkeypatch):
    monkeypatch.setenv(
        "AURA_SEC_SKUS_JSON",
        json.dumps(
            [
                {
                    "id": "pro",
                    "display_name": "Invalid",
                    "currency": "gbp",
                    "price_minor": 0,
                    "period_days": 31,
                    "device_limit": 5,
                }
            ]
        ),
    )
    with pytest.raises(ValueError):
        AuraSecCatalog.from_environment()


def test_duplicate_sku_ids_are_rejected():
    sku = AuraSecSku(
        id="aura-sec-test",
        display_name="Aura Sec Test",
        currency="USD",
        price_minor=1000,
        period_days=30,
        device_limit=3,
    )
    with pytest.raises(ValueError, match="Duplicate Aura Sec SKU"):
        AuraSecCatalog((sku, sku))
