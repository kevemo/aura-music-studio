from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_brand_revenue_os import (
    BrandRevenueStore,
    Qualification,
    RevenueAccountCreate,
    RevenueAccountUpdate,
    RevenueActivityCreate,
)
from aura_music_studio.esp_command_center import EspStore


def _stores(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    return accounts, BrandRevenueStore(esp)


def _user(accounts: AccountStore, email: str) -> dict:
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    return accounts.decide_membership(signup.approval_token, "approve", "Owner")


def _qualification() -> Qualification:
    return Qualification(
        need="Launch a creator campaign",
        authority="Marketing director approves",
        budget="Budget confirmed",
        timing="Q4 launch",
        fit="Music creators in UK+",
        risk="Standard brand-safety review required",
    )


def test_active_account_requires_one_dated_next_action(tmp_path):
    accounts, store = _stores(tmp_path)
    owner = _user(accounts, "owner@example.com")
    with pytest.raises(ValueError, match="dated next action"):
        store.create(
            owner["id"],
            RevenueAccountCreate(brand_name="Example", website="https://example.com"),
        )


def test_qualified_stage_requires_complete_qualification_and_enables_forecast(tmp_path):
    accounts, store = _stores(tmp_path)
    owner = _user(accounts, "owner@example.com")
    with pytest.raises(ValueError, match="Need, Authority, Budget, Timing, Fit and Risk"):
        store.create(
            owner["id"],
            RevenueAccountCreate(
                brand_name="Example",
                stage="qualified",
                next_action="Book discovery follow-up",
                next_action_at="2026-09-10",
                forecast="pipeline",
            ),
        )

    account = store.create(
        owner["id"],
        RevenueAccountCreate(
            brand_name="Example",
            stage="qualified",
            next_action="Send solution outline",
            next_action_at="2026-09-10",
            qualification=_qualification(),
            forecast="pipeline",
            expected_value=10000,
            probability=.35,
        ),
    )
    assert account["crm"]["canonical_stage"] == "qualified"
    assert account["crm"]["forecast_category"] == "pipeline"


def test_commit_forecast_requires_contracted_or_delivery_stage(tmp_path):
    accounts, store = _stores(tmp_path)
    owner = _user(accounts, "owner@example.com")
    with pytest.raises(ValueError, match="Commit forecast"):
        store.create(
            owner["id"],
            RevenueAccountCreate(
                brand_name="Example",
                stage="proposal_sent",
                next_action="Review proposal",
                next_action_at="2026-09-10",
                qualification=_qualification(),
                forecast="commit",
            ),
        )


def test_do_not_contact_blocks_outbound_activity_but_allows_internal_note(tmp_path):
    accounts, store = _stores(tmp_path)
    owner = _user(accounts, "owner@example.com")
    account = store.create(
        owner["id"],
        RevenueAccountCreate(
            brand_name="Do Not Contact Brand",
            stage="researched",
            next_action="Internal review",
            next_action_at="2026-09-08",
            do_not_contact=True,
        ),
    )
    lead_id = account["lead"]["id"]
    with pytest.raises(PermissionError, match="do-not-contact"):
        store.add_activity(
            lead_id,
            owner["id"],
            RevenueActivityCreate(activity_type="outreach", summary="Email sent"),
            owner=False,
        )
    updated = store.add_activity(
        lead_id,
        owner["id"],
        RevenueActivityCreate(activity_type="note", summary="Retain account for historic context only"),
        owner=False,
    )
    assert updated["activities"][0]["activity_type"] == "note"


def test_agent_cannot_read_or_modify_another_agents_account(tmp_path):
    accounts, store = _stores(tmp_path)
    first = _user(accounts, "first@example.com")
    second = _user(accounts, "second@example.com")
    account = store.create(
        first["id"],
        RevenueAccountCreate(brand_name="Private Account", stage="researched", next_action="Research", next_action_at="2026-09-05"),
    )
    with pytest.raises(PermissionError):
        store.get(account["lead"]["id"], actor=second["id"], owner=False)
    assert store.get(account["lead"]["id"], actor=second["id"], owner=True)["lead"]["brand_name"] == "Private Account"


def test_closed_lost_requires_standard_reason(tmp_path):
    accounts, store = _stores(tmp_path)
    owner = _user(accounts, "owner@example.com")
    account = store.create(
        owner["id"],
        RevenueAccountCreate(brand_name="Example", stage="researched", next_action="Research", next_action_at="2026-09-05"),
    )
    with pytest.raises(ValueError, match="lost-deal reason"):
        store.update(
            account["lead"]["id"],
            owner["id"],
            RevenueAccountUpdate(stage="closed_lost", next_action="", next_action_at="", qualification=Qualification()),
            owner=False,
        )


def test_metrics_do_not_count_unqualified_outreach_as_forecast(tmp_path):
    accounts, store = _stores(tmp_path)
    owner = _user(accounts, "owner@example.com")
    store.create(
        owner["id"],
        RevenueAccountCreate(
            brand_name="Research Only",
            stage="researched",
            next_action="Research decision maker",
            next_action_at="2026-09-06",
            expected_value=50000,
        ),
    )
    store.create(
        owner["id"],
        RevenueAccountCreate(
            brand_name="Qualified",
            stage="qualified",
            next_action="Send solution",
            next_action_at="2026-09-06",
            expected_value=10000,
            qualification=_qualification(),
            forecast="pipeline",
        ),
    )
    metrics = store.metrics(owner["id"], owner=False)
    assert metrics["expected_value"] == 60000
    assert metrics["forecast_value"]["pipeline"] == 10000
    assert metrics["unqualified_outreach_counted_as_forecast"] is False
