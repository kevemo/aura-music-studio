from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_creator_discovery import (
    CreateLeadRequest,
    CreatorDiscoveryStore,
    EventRequest,
    ValidateLeadRequest,
    normalize_handle,
    router,
)


def _user(accounts: AccountStore, email: str) -> dict:
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    return accounts.decide_membership(signup.approval_token, "approve", "Owner")


def _lead(handle: str, source: str = "https://www.tiktok.com/@example") -> CreateLeadRequest:
    return CreateLeadRequest(
        tiktok_handle=handle,
        display_name="Example Creator",
        region="UK+",
        niche="music",
        source_kind="public_profile",
        source_ref=source,
        public_contact="TikTok public profile",
    )


def _eligible() -> ValidateLeadRequest:
    return ValidateLeadRequest(
        affiliation_status="none",
        age_status="adult_confirmed",
        eligibility_note="Public profile reviewed; no other Creator Network affiliation visible and adult status confirmed.",
    )


def test_handle_normalization_is_global_and_case_insensitive(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    agent_a = _user(accounts, "agent-a@example.com")
    agent_b = _user(accounts, "agent-b@example.com")
    store = CreatorDiscoveryStore(accounts.db_path)

    first = store.create(agent_a["id"], _lead("@Creator.Name"))
    assert first["tiktok_handle"] == "creator.name"
    assert normalize_handle("  @CREATOR.NAME ") == "creator.name"

    with pytest.raises(FileExistsError, match="already exists"):
        store.create(agent_b["id"], _lead("creator.name"))


def test_unvalidated_prospect_cannot_receive_apply_message(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    agent = _user(accounts, "agent@example.com")
    store = CreatorDiscoveryStore(accounts.db_path)
    lead = store.create(agent["id"], _lead("unvalidated.creator"))

    with pytest.raises(PermissionError, match="not eligible"):
        store.add_event(
            lead["id"],
            agent["id"],
            EventRequest(event_type="apply_message_sent", channel="TikTok DM"),
        )


def test_other_network_and_underage_prospects_fail_closed(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    agent = _user(accounts, "agent2@example.com")
    store = CreatorDiscoveryStore(accounts.db_path)

    network_lead = store.create(agent["id"], _lead("network.creator"))
    network_lead = store.validate(
        network_lead["id"],
        agent["id"],
        ValidateLeadRequest(affiliation_status="other_network", age_status="adult_confirmed"),
    )
    assert network_lead["validation_status"] == "blocked"
    assert network_lead["pipeline_status"] == "closed"
    assert network_lead["contact_allowed"] is False
    assert network_lead["do_not_contact"] is True

    child_lead = store.create(agent["id"], _lead("young.creator"))
    child_lead = store.validate(
        child_lead["id"],
        agent["id"],
        ValidateLeadRequest(affiliation_status="none", age_status="underage"),
    )
    assert child_lead["validation_status"] == "ineligible"
    assert child_lead["contact_allowed"] is False
    assert child_lead["do_not_contact"] is True


def test_eligible_outreach_followup_application_and_join_are_tracked(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    agent = _user(accounts, "agent3@example.com")
    store = CreatorDiscoveryStore(accounts.db_path)
    lead = store.create(agent["id"], _lead("eligible.creator"))
    lead = store.validate(lead["id"], agent["id"], _eligible())
    assert lead["validation_status"] == "eligible"
    assert lead["pipeline_status"] == "ready"
    assert lead["contact_allowed"] is True

    lead = store.add_event(
        lead["id"],
        agent["id"],
        EventRequest(
            event_type="apply_message_sent",
            channel="TikTok DM",
            note="Official ESP information offered individually.",
            next_follow_up_at="2026-08-30T10:00:00+00:00",
        ),
    )
    assert lead["pipeline_status"] == "follow_up_due"
    assert lead["last_contact_at"]

    lead = store.add_event(lead["id"], agent["id"], EventRequest(event_type="reply_received"))
    assert lead["pipeline_status"] == "replied"
    lead = store.add_event(lead["id"], agent["id"], EventRequest(event_type="application_started"))
    assert lead["pipeline_status"] == "applied"
    lead = store.add_event(lead["id"], agent["id"], EventRequest(event_type="joined"))
    assert lead["pipeline_status"] == "joined"
    assert lead["contact_allowed"] is False

    stats = store.stats(agent["id"])
    assert stats["joined"] == 1
    assert stats["contacted_or_beyond"] == 1
    assert stats["join_conversion_percent"] == 100.0
    assert [event["event_type"] for event in lead["events"]][:4] == [
        "joined",
        "application_started",
        "reply_received",
        "apply_message_sent",
    ]


def test_opt_out_prevents_future_recruitment_contact(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    agent = _user(accounts, "agent4@example.com")
    store = CreatorDiscoveryStore(accounts.db_path)
    lead = store.create(agent["id"], _lead("optout.creator"))
    lead = store.validate(lead["id"], agent["id"], _eligible())
    lead = store.add_event(lead["id"], agent["id"], EventRequest(event_type="opt_out", note="Creator asked for no further contact."))
    assert lead["do_not_contact"] is True
    assert lead["contact_allowed"] is False

    with pytest.raises(PermissionError, match="not eligible"):
        store.add_event(
            lead["id"],
            agent["id"],
            EventRequest(event_type="follow_up_sent", channel="TikTok DM"),
        )


def test_agents_only_see_their_own_discovery_rows(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    agent_a = _user(accounts, "agent5@example.com")
    agent_b = _user(accounts, "agent6@example.com")
    store = CreatorDiscoveryStore(accounts.db_path)
    lead_a = store.create(agent_a["id"], _lead("agenta.creator"))
    lead_b = store.create(agent_b["id"], _lead("agentb.creator"))

    assert [row["id"] for row in store.list(agent_a["id"])] == [lead_a["id"]]
    assert [row["id"] for row in store.list(agent_b["id"])] == [lead_b["id"]]
    assert {row["id"] for row in store.list(agent_a["id"], owner=True)} == {lead_a["id"], lead_b["id"]}


def test_discovery_router_is_private_and_does_not_offer_auto_dm():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/agent/discovery" in paths
    assert "/command-center/api/agent/discovery" in paths
    assert "/command-center/api/agent/discovery/{lead_id}/validate" in paths
    assert "/command-center/api/agent/discovery/{lead_id}/events" in paths
    assert "/command-center/api/agent/discovery/templates" in paths
    assert "/agent/discovery" not in paths
