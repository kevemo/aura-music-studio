from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_commercial_growth import (
    ApplicationUpdate,
    BrandLeadCreate,
    BrandLeadUpdate,
    CommercialGrowthStore,
    CommercialProfileUpdate,
    OpportunityCreate,
    _safe_url,
    router,
)


def _user(accounts: AccountStore, email: str) -> dict:
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    return accounts.decide_membership(signup.approval_token, "approve", "Owner")


def _store(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    return accounts, CommercialGrowthStore(accounts.db_path)


def test_commercial_profile_requires_https_media_kit(tmp_path):
    accounts, store = _store(tmp_path)
    creator = _user(accounts, "creator@example.com")

    with pytest.raises(ValueError, match="HTTPS"):
        store.save_profile(
            creator["id"],
            CommercialProfileUpdate(
                shop_opt_in=True,
                disclosure_acknowledged=True,
                media_kit_url="http://example.com/media-kit",
            ),
        )

    profile = store.save_profile(
        creator["id"],
        CommercialProfileUpdate(
            shop_opt_in=True,
            brand_opt_in=True,
            disclosure_acknowledged=True,
            media_kit_url="https://example.com/media-kit",
            niches=["Music", "music", "Gaming"],
        ),
    )
    assert profile["shop_opt_in"] is True
    assert profile["brand_opt_in"] is True
    assert profile["disclosure_acknowledged"] is True
    assert profile["niches"] == ["Music", "Gaming"]


def test_creator_must_opt_in_and_acknowledge_disclosure_before_shop_application(tmp_path):
    accounts, store = _store(tmp_path)
    agent = _user(accounts, "agent@example.com")
    creator = _user(accounts, "creator@example.com")
    opportunity = store.create_shop(
        agent["id"],
        OpportunityCreate(title="Creator sample campaign", status="open", official_url="https://seller-uk.tiktok.com/"),
    )

    with pytest.raises(PermissionError, match="opt-in"):
        store.apply("shop", opportunity["id"], creator["id"], "Interested")

    store.save_profile(creator["id"], CommercialProfileUpdate(shop_opt_in=True, disclosure_acknowledged=False))
    with pytest.raises(PermissionError, match="disclosure"):
        store.apply("shop", opportunity["id"], creator["id"], "Interested")

    store.save_profile(creator["id"], CommercialProfileUpdate(shop_opt_in=True, disclosure_acknowledged=True))
    application = store.apply("shop", opportunity["id"], creator["id"], "Interested")
    assert application["status"] == "applied"
    assert application["user_id"] == creator["id"]


def test_creator_only_sees_open_opportunities_but_agent_sees_own_drafts(tmp_path):
    accounts, store = _store(tmp_path)
    first_agent = _user(accounts, "agent1@example.com")
    second_agent = _user(accounts, "agent2@example.com")
    creator = _user(accounts, "creator@example.com")

    store.create_shop(first_agent["id"], OpportunityCreate(title="First draft", status="draft"))
    first_open = store.create_shop(first_agent["id"], OpportunityCreate(title="First open", status="open"))
    second_draft = store.create_shop(second_agent["id"], OpportunityCreate(title="Second draft", status="draft"))

    creator_rows = store.list_opportunities("shop", member_user_id=creator["id"], management=False)
    assert [row["id"] for row in creator_rows] == [first_open["id"]]

    first_agent_rows = store.list_opportunities("shop", member_user_id=first_agent["id"], management=True)
    assert {row["title"] for row in first_agent_rows} == {"First draft", "First open"}
    assert second_draft["id"] not in {row["id"] for row in first_agent_rows}

    owner_rows = store.list_opportunities("shop", member_user_id=first_agent["id"], management=True, owner=True)
    assert {row["title"] for row in owner_rows} == {"First draft", "First open", "Second draft"}


def test_agent_cannot_attach_or_update_another_agents_brand_lead(tmp_path):
    accounts, store = _store(tmp_path)
    first_agent = _user(accounts, "agent1@example.com")
    second_agent = _user(accounts, "agent2@example.com")

    lead = store.create_brand_lead(
        first_agent["id"],
        BrandLeadCreate(brand_name="Example Brand", source_url="https://example.com", stage="qualified"),
    )

    with pytest.raises(PermissionError, match="another ESP agent"):
        store.create_brand_opportunity(
            second_agent["id"],
            OpportunityCreate(title="Campaign", status="draft"),
            lead["id"],
        )

    with pytest.raises(PermissionError, match="another ESP agent"):
        store.update_brand_lead(
            lead["id"],
            second_agent["id"],
            BrandLeadUpdate(stage="follow_up"),
            owner=False,
        )

    owner_created = store.create_brand_opportunity(
        second_agent["id"],
        OpportunityCreate(title="Owner-reviewed campaign", status="draft"),
        lead["id"],
        owner=True,
    )
    assert owner_created["brand_lead_id"] == lead["id"]


def test_application_updates_are_owned_by_opportunity_agent_or_owner(tmp_path):
    accounts, store = _store(tmp_path)
    first_agent = _user(accounts, "agent1@example.com")
    second_agent = _user(accounts, "agent2@example.com")
    creator = _user(accounts, "creator@example.com")
    store.save_profile(creator["id"], CommercialProfileUpdate(brand_opt_in=True, disclosure_acknowledged=True))
    opportunity = store.create_brand_opportunity(first_agent["id"], OpportunityCreate(title="Music partnership", status="open"))
    application = store.apply("brand", opportunity["id"], creator["id"], "Available")

    update = ApplicationUpdate(status="accepted", deliverable_status="planning", tracking_note="Brief sent")
    with pytest.raises(PermissionError, match="opportunity owner"):
        store.update_application("brand", application["id"], second_agent["id"], update, owner=False)

    accepted = store.update_application("brand", application["id"], first_agent["id"], update, owner=False)
    assert accepted["status"] == "accepted"
    assert accepted["deliverable_status"] == "planning"


def test_commercial_routes_are_private_command_center_routes():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/commerce" in paths
    assert "/command-center/brands" in paths
    assert "/command-center/api/commercial/profile" in paths
    assert "/command-center/api/commerce/opportunities" in paths
    assert "/command-center/api/brands/leads" in paths
    assert "/commerce" not in paths
    assert "/brands" not in paths


def test_safe_url_blocks_non_https_workflow_links():
    assert _safe_url("") == ""
    assert _safe_url("https://www.tiktok.com/business/en") == "https://www.tiktok.com/business/en"
    with pytest.raises(ValueError):
        _safe_url("javascript:alert(1)")
    with pytest.raises(ValueError):
        _safe_url("http://example.com")
