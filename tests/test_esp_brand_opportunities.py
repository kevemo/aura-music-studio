from __future__ import annotations

import sqlite3

import pytest

from aura_music_studio.esp_brand_opportunities import (
    ApplicationCreate,
    CampaignCreate,
    CommercialOpportunityStore,
    DeliverableUpdate,
    PaymentUpdate,
)


def _db(tmp_path):
    path = tmp_path / "esp-commercial.sqlite3"
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            CREATE TABLE esp_memberships (
                user_id TEXT PRIMARY KEY, tiktok_handle TEXT, region TEXT,
                status TEXT NOT NULL, roles TEXT NOT NULL
            );
            CREATE TABLE esp_niche_profiles (
                user_id TEXT PRIMARY KEY, niche TEXT NOT NULL, sub_niche TEXT
            );
            CREATE TABLE esp_agent_creator_assignments (
                id TEXT PRIMARY KEY, agent_user_id TEXT NOT NULL, creator_user_id TEXT NOT NULL,
                status TEXT NOT NULL
            );
            """
        )
        members = [
            ("creator1", "creator_one", "UK", "active", "creator", "gaming", "variety"),
            ("creator2", "creator_two", "UK", "active", "creator", "gaming", "simulation"),
            ("creator3", "creator_three", "UK", "active", "creator", "beauty", "makeup"),
            ("agent1", "agent_one", "UK", "active", "agent", "business", "creator management"),
            ("both1", "both_one", "UK", "active", "both", "gaming", "variety"),
        ]
        for user_id, handle, region, status, role, niche, sub_niche in members:
            con.execute("INSERT INTO esp_memberships VALUES (?,?,?,?,?)", (user_id, handle, region, status, role))
            con.execute("INSERT INTO esp_niche_profiles VALUES (?,?,?)", (user_id, niche, sub_niche))
        con.execute("INSERT INTO esp_agent_creator_assignments VALUES ('a1','agent1','creator2','active')")
        con.execute("INSERT INTO esp_agent_creator_assignments VALUES ('a2','both1','creator1','active')")
    return path


def _campaign(store, title="Gaming Launch"):
    return store.create_campaign(
        "owner",
        CampaignCreate(
            brand_name="Example Brand",
            title=title,
            brief="Create a safe branded gaming activation with clear disclosure.",
            niches=["gaming"],
            regions=["UK"],
            platforms=["TikTok"],
            deliverables=["One short video", "One LIVE activation"],
            usage_terms="Organic brand channels for 30 days.",
            exclusivity_terms="No category exclusivity unless separately agreed.",
            disclosure_requirements="Use the applicable paid partnership/branded-content disclosure.",
            status="applications_open",
        ),
    )


def test_campaign_eligibility_is_niche_region_and_open_state_bound(tmp_path):
    store = CommercialOpportunityStore(str(_db(tmp_path)))
    campaign = _campaign(store)
    assert store.eligibility(campaign, "creator1")["eligible"] is True
    result = store.eligibility(campaign, "creator3")
    assert result["eligible"] is False
    assert any("niche" in reason.lower() for reason in result["reasons"])
    store.set_campaign_status(campaign["id"], "applications_closed", actor="owner")
    closed = store.eligibility(store.campaign(campaign["id"]), "creator1")
    assert closed["eligible"] is False
    assert any("not currently open" in reason.lower() for reason in closed["reasons"])


def test_creator_can_apply_for_self_but_not_another_creator(tmp_path):
    store = CommercialOpportunityStore(str(_db(tmp_path)))
    campaign = _campaign(store)
    app = store.apply(
        campaign["id"], actor_user_id="creator1", actor_role="creator",
        body=ApplicationCreate(concept="A challenge-led gaming activation."),
    )
    assert app["creator_user_id"] == "creator1"
    assert app["creator_opt_in_confirmed"] == 1
    with pytest.raises(PermissionError, match="only their own"):
        store.apply(
            campaign["id"], actor_user_id="creator1", actor_role="creator",
            body=ApplicationCreate(creator_user_id="creator2", concept="Not allowed"),
        )


def test_agent_requires_assignment_and_explicit_creator_opt_in(tmp_path):
    store = CommercialOpportunityStore(str(_db(tmp_path)))
    campaign = _campaign(store)
    with pytest.raises(PermissionError, match="opt-in"):
        store.apply(
            campaign["id"], actor_user_id="agent1", actor_role="agent",
            body=ApplicationCreate(creator_user_id="creator2", concept="Creator approved concept"),
        )
    with pytest.raises(PermissionError, match="not actively assigned"):
        store.apply(
            campaign["id"], actor_user_id="agent1", actor_role="agent",
            body=ApplicationCreate(creator_user_id="creator1", concept="Unassigned", creator_opt_in_confirmed=True),
        )
    app = store.apply(
        campaign["id"], actor_user_id="agent1", actor_role="agent",
        body=ApplicationCreate(creator_user_id="creator2", concept="Assigned and consented", creator_opt_in_confirmed=True),
    )
    assert app["creator_user_id"] == "creator2"
    assert app["agent_user_id"] == "agent1"


def test_both_role_can_act_in_agent_view_for_assigned_creator(tmp_path):
    store = CommercialOpportunityStore(str(_db(tmp_path)))
    campaign = _campaign(store)
    app = store.apply(
        campaign["id"], actor_user_id="both1", actor_role="both",
        body=ApplicationCreate(creator_user_id="creator1", concept="Agent-view submission", creator_opt_in_confirmed=True),
    )
    assert app["creator_user_id"] == "creator1"
    assert app["agent_user_id"] == "both1"
    visible = store.list_applications(requester_user_id="both1", requester_role="both")
    assert any(item["id"] == app["id"] for item in visible)


def test_owner_assistance_still_requires_creator_consent(tmp_path):
    store = CommercialOpportunityStore(str(_db(tmp_path)))
    campaign = _campaign(store)
    with pytest.raises(PermissionError, match="creator opt-in"):
        store.apply(
            campaign["id"], actor_user_id="owner", actor_role="owner",
            body=ApplicationCreate(creator_user_id="creator1", concept="Owner assisted"),
        )
    app = store.apply(
        campaign["id"], actor_user_id="owner", actor_role="owner",
        body=ApplicationCreate(creator_user_id="creator1", concept="Owner assisted", creator_opt_in_confirmed=True),
    )
    assert app["creator_opt_in_confirmed"] == 1


def test_agent_shortlists_owner_approves_and_payment_is_tracking_only(tmp_path):
    store = CommercialOpportunityStore(str(_db(tmp_path)))
    campaign = _campaign(store)
    app = store.apply(
        campaign["id"], actor_user_id="agent1", actor_role="agent",
        body=ApplicationCreate(creator_user_id="creator2", concept="Launch stream", creator_opt_in_confirmed=True),
    )
    shortlisted = store.set_application_status(
        app["id"], requester_user_id="agent1", requester_role="agent", status="shortlisted", note="Strong niche fit"
    )
    assert shortlisted["status"] == "shortlisted"
    with pytest.raises(PermissionError, match="final approve"):
        store.set_application_status(app["id"], requester_user_id="agent1", requester_role="agent", status="approved")
    approved = store.set_application_status(app["id"], requester_user_id="owner", requester_role="owner", status="approved")
    assert approved["status"] == "approved"

    deliverable = store.add_deliverable(app["id"], actor="owner", label="TikTok branded video", due_at="2026-09-15")
    submitted = store.update_deliverable(
        deliverable["id"], requester_user_id="creator2", requester_role="creator",
        body=DeliverableUpdate(status="submitted", submission_ref="artifact://creative-project/video-01"),
    )
    assert submitted["submission_ref"].startswith("artifact://")
    revision = store.update_deliverable(
        deliverable["id"], requester_user_id="agent1", requester_role="agent",
        body=DeliverableUpdate(status="revision_requested", review_note="Add disclosure in the opening frame."),
    )
    assert revision["status"] == "revision_requested"

    payment = store.set_payment(
        app["id"], actor="owner",
        body=PaymentUpdate(status="due", amount_minor=25000, currency="GBP", invoice_ref="INV-001"),
    )
    assert payment["status"] == "due"
    assert payment["amount_minor"] == 25000
    final = store.application(app["id"], requester_user_id="owner", requester_role="owner")
    actions = [event["action"] for event in final["activity"]]
    assert "application_submitted" in actions
    assert "deliverable_updated" in actions
    assert "payment_state_changed" in actions


def test_duplicate_creator_application_is_rejected(tmp_path):
    store = CommercialOpportunityStore(str(_db(tmp_path)))
    campaign = _campaign(store)
    body = ApplicationCreate(concept="One application only")
    store.apply(campaign["id"], actor_user_id="creator1", actor_role="creator", body=body)
    with pytest.raises(ValueError, match="already has an application"):
        store.apply(campaign["id"], actor_user_id="creator1", actor_role="creator", body=body)
