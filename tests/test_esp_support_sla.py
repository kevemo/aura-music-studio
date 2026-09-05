from __future__ import annotations

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_support_center import SupportCaseStore
from aura_music_studio.esp_support_sla import (
    EspSupportSlaStore,
    ServiceCaseCreate,
    ServiceMetaUpdate,
    SupportTouchCreate,
)


def _stores(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    support = SupportCaseStore(esp)
    return accounts, EspSupportSlaStore(esp, support)


def _user(accounts: AccountStore, email: str) -> dict:
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    return accounts.decide_membership(signup.approval_token, "approve", "Owner")


def test_service_case_uses_canonical_p0_p3_taxonomy_and_base_ticket(tmp_path):
    accounts, store = _stores(tmp_path)
    creator = _user(accounts, "creator@example.com")
    row = store.create(
        creator["id"],
        ServiceCaseCreate(
            category="live_technical",
            subcategory="obs_audio",
            priority="P1",
            region="UK+",
            subject="OBS audio is not reaching LIVE",
            description="Local audio works but the LIVE programme mix is silent.",
        ),
    )
    assert row["case"]["category"] == "technical"
    assert row["case"]["severity"] == "high"
    assert row["service"]["canonical_category"] == "live_technical"
    assert row["service"]["priority"] == "P1"
    assert row["service"]["policy"]["first_human_response_target_hours"] == 4


def test_automated_receipt_does_not_count_as_substantive_response(tmp_path):
    accounts, store = _stores(tmp_path)
    creator = _user(accounts, "creator@example.com")
    row = store.create(
        creator["id"],
        ServiceCaseCreate(category="general_programme", priority="P3", subject="Question", description="Programme question"),
    )
    case_id = row["case"]["id"]
    after_receipt = store.add_touch(
        case_id,
        SupportTouchCreate(kind="acknowledgement", note="Case received", substantive_human_response=False),
        actor="system",
    )
    assert after_receipt["service"]["acknowledged_at"]
    assert after_receipt["service"]["first_substantive_response_at"] is None

    after_human = store.add_touch(
        case_id,
        SupportTouchCreate(note="A human reviewed the issue and requested one diagnostic."),
        actor="support-owner",
    )
    assert after_human["service"]["first_substantive_response_at"]


def test_waiting_on_platform_is_explicit_and_does_not_change_esp_policy_truth(tmp_path):
    accounts, store = _stores(tmp_path)
    creator = _user(accounts, "creator@example.com")
    row = store.create(
        creator["id"],
        ServiceCaseCreate(category="compliance_violation", priority="P1", subject="Appeal support", description="Need help organising appeal evidence"),
    )
    updated = store.update_meta(
        row["case"]["id"],
        ServiceMetaUpdate(waiting_on="platform_partner", external_reference="ticket-123"),
    )
    assert updated["service"]["waiting_on"] == "platform_partner"
    assert "does not claim external platform response times" in updated["service"]["sla_interpretation"]


def test_creator_view_hides_internal_only_touch(tmp_path):
    accounts, store = _stores(tmp_path)
    creator = _user(accounts, "creator@example.com")
    row = store.create(
        creator["id"],
        ServiceCaseCreate(category="creator_care", priority="P2", subject="Support check-in", description="Requesting a private check-in"),
    )
    case_id = row["case"]["id"]
    store.add_touch(
        case_id,
        SupportTouchCreate(note="Internal routing note", substantive_human_response=False, creator_visible=False),
        actor="owner",
    )
    owner = store.get(case_id, owner=True)
    creator_view = store.get(case_id, user_id=creator["id"])
    assert len(owner["touches"]) == 1
    assert creator_view["touches"] == []


def test_metrics_separate_attached_cases_and_first_response_records(tmp_path):
    accounts, store = _stores(tmp_path)
    creator = _user(accounts, "creator@example.com")
    first = store.create(
        creator["id"],
        ServiceCaseCreate(category="traffic_distribution", priority="P2", subject="Reach drop", description="Distribution changed suddenly"),
    )
    store.add_touch(first["case"]["id"], SupportTouchCreate(note="Human review started"), actor="owner")
    second = store.create(
        creator["id"],
        ServiceCaseCreate(category="account_access", priority="P0", subject="Account locked", description="Cannot access account"),
    )
    metrics = store.metrics()
    assert metrics["sla_attached"] == 2
    assert metrics["by_priority"]["P0"] == 1
    assert metrics["by_priority"]["P2"] == 1
    assert metrics["first_substantive_response_recorded"] == 1
