from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from aura_music_studio.ip_rights import (
    CounterNoticeInput,
    IPRightsStore,
    OwnerCounterReviewInput,
    OwnerNoticeReviewInput,
    RightsNoticeInput,
    member_router,
    owner_router,
)


def notice_payload(**overrides):
    value = dict(
        rights_type="copyright",
        jurisdiction_profile="us_dmca",
        claimant_basis="rights_holder",
        protected_work_reference="work:original-song-1",
        target_reference="content:render-22",
        contact_evidence_reference="vault:contact-1",
        signature_evidence_reference="vault:signature-1",
        detail="I own the referenced work and request qualified review of the referenced content.",
        good_faith_belief=True,
        accuracy_and_authority_attested=True,
    )
    value.update(overrides)
    return RightsNoticeInput(**value)


def counter_payload(**overrides):
    value = dict(
        reason="I believe the material was identified in error and request qualified review.",
        contact_evidence_reference="vault:respondent-contact-1",
        signature_evidence_reference="vault:respondent-signature-1",
        good_faith_mistake_or_misidentification=True,
        us_jurisdiction_and_service_attested=True,
    )
    value.update(overrides)
    return CounterNoticeInput(**value)


def test_notice_is_evidence_only_and_member_scoped(tmp_path):
    store = IPRightsStore(tmp_path / "ip.sqlite3")
    created = store.submit_notice(claimant_user_id="member-a", payload=notice_payload())
    assert created["notice"]["status"] == "submitted"
    assert created["automatic_content_action_taken"] is False
    assert created["legal_sufficiency_certified"] is False
    assert len(store.list_for_member("member-a")) == 1
    assert store.list_for_member("member-b") == []
    with pytest.raises(KeyError):
        store.get_for_member(created["notice"]["id"], "member-b")


def test_notice_rejects_url_path_refs_and_missing_attestations(tmp_path):
    store = IPRightsStore(tmp_path / "ip.sqlite3")
    with pytest.raises(ValueError):
        store.submit_notice(claimant_user_id="member-a", payload=notice_payload(target_reference="https://example.com/file"))
    with pytest.raises(ValueError):
        store.submit_notice(claimant_user_id="member-a", payload=notice_payload(contact_evidence_reference="/tmp/contact.txt"))
    with pytest.raises(ValueError):
        store.submit_notice(claimant_user_id="member-a", payload=notice_payload(good_faith_belief=False))


def test_counter_notice_requires_owner_bound_verified_respondent(tmp_path):
    store = IPRightsStore(tmp_path / "ip.sqlite3")
    notice = store.submit_notice(claimant_user_id="claimant", payload=notice_payload())["notice"]
    store.owner_review_notice(
        notice_id=notice["id"],
        payload=OwnerNoticeReviewInput(
            status="action_recommended",
            reason="Qualified reviewer found sufficient evidence to proceed to an external decision step.",
            counter_notice_eligible_user_id="affected-member",
        ),
    )
    with pytest.raises(PermissionError):
        store.submit_counter_notice(notice_id=notice["id"], submitter_user_id="random-member", payload=counter_payload())
    counter = store.submit_counter_notice(
        notice_id=notice["id"], submitter_user_id="affected-member", payload=counter_payload()
    )
    assert counter["status"] == "submitted"


def test_us_dmca_counter_profile_requires_jurisdiction_service_attestation(tmp_path):
    store = IPRightsStore(tmp_path / "ip.sqlite3")
    notice = store.submit_notice(claimant_user_id="claimant", payload=notice_payload())["notice"]
    store.owner_review_notice(
        notice_id=notice["id"],
        payload=OwnerNoticeReviewInput(
            status="accepted", reason="Reviewed", decision_evidence_reference="legal:decision-1",
            counter_notice_eligible_user_id="affected-member",
        ),
    )
    with pytest.raises(ValueError):
        store.submit_counter_notice(
            notice_id=notice["id"], submitter_user_id="affected-member",
            payload=counter_payload(us_jurisdiction_and_service_attested=False),
        )


def test_terminal_owner_decisions_require_evidence_and_do_not_execute_content_action(tmp_path):
    store = IPRightsStore(tmp_path / "ip.sqlite3")
    notice = store.submit_notice(claimant_user_id="claimant", payload=notice_payload(jurisdiction_profile="uk"))["notice"]
    with pytest.raises(ValueError):
        store.owner_review_notice(
            notice_id=notice["id"], payload=OwnerNoticeReviewInput(status="accepted", reason="Reviewed")
        )
    result = store.owner_review_notice(
        notice_id=notice["id"],
        payload=OwnerNoticeReviewInput(
            status="accepted", reason="Reviewed by qualified process",
            decision_evidence_reference="legal:decision-uk-1",
            external_content_action_reference="moderation:action-record-1",
            counter_notice_eligible_user_id="affected-member",
        ),
    )
    assert result["notice"]["status"] == "accepted"
    assert result["automatic_content_action_taken"] is False
    assert result["automatic_content_restoration"] is False
    assert result["grants_esp_role_or_permission"] is False
    assert result["changes_billing_or_membership"] is False


def test_counter_review_requires_evidence_and_never_restores_automatically(tmp_path):
    store = IPRightsStore(tmp_path / "ip.sqlite3")
    notice = store.submit_notice(claimant_user_id="claimant", payload=notice_payload())["notice"]
    store.owner_review_notice(
        notice_id=notice["id"],
        payload=OwnerNoticeReviewInput(
            status="accepted", reason="Reviewed", decision_evidence_reference="legal:decision-2",
            counter_notice_eligible_user_id="affected-member",
        ),
    )
    counter = store.submit_counter_notice(
        notice_id=notice["id"], submitter_user_id="affected-member", payload=counter_payload()
    )
    with pytest.raises(ValueError):
        store.owner_review_counter(
            notice_id=notice["id"], counter_id=counter["id"],
            payload=OwnerCounterReviewInput(status="accepted", reason="Reviewed"),
        )
    result = store.owner_review_counter(
        notice_id=notice["id"], counter_id=counter["id"],
        payload=OwnerCounterReviewInput(
            status="accepted", reason="Qualified counter-notice review complete",
            decision_evidence_reference="legal:counter-decision-1",
        ),
    )
    assert result["automatic_content_restoration"] is False
    assert result["legal_sufficiency_certified"] is False


def test_owner_ip_review_events_record_reviewer_actor_without_touching_member_events(monkeypatch, tmp_path):
    import aura_music_studio.ip_rights as ip_module

    monkeypatch.setattr(ip_module, "owner_actor", lambda: "Mary · ESP Owner")
    store = IPRightsStore(tmp_path / "ip.sqlite3")
    notice = store.submit_notice(claimant_user_id="claimant", payload=notice_payload())["notice"]
    store.owner_review_notice(
        notice_id=notice["id"],
        payload=OwnerNoticeReviewInput(
            status="action_recommended",
            reason="Owner review completed for counter-notice eligibility.",
            counter_notice_eligible_user_id="affected-member",
        ),
    )
    counter = store.submit_counter_notice(
        notice_id=notice["id"], submitter_user_id="affected-member", payload=counter_payload()
    )
    result = store.owner_review_counter(
        notice_id=notice["id"], counter_id=counter["id"],
        payload=OwnerCounterReviewInput(status="under_review", reason="Counter-notice review started."),
    )
    submitted = [item for item in result["events"] if item["action"] == "rights_notice_submitted"][-1]
    notice_review = [item for item in result["events"] if item["action"] == "notice_review"][-1]
    counter_review = [item for item in result["events"] if item["action"] == "counter_notice_review"][-1]
    assert "reviewer_actor" not in submitted["data"]
    assert notice_review["actor_type"] == "owner"
    assert notice_review["data"]["reviewer_actor"] == "Mary · ESP Owner"
    assert counter_review["actor_type"] == "owner"
    assert counter_review["data"]["reviewer_actor"] == "Mary · ESP Owner"
    assert result["audit_chain_valid"] is True


def test_ip_event_chain_detects_persisted_tampering(tmp_path):
    db = tmp_path / "ip.sqlite3"
    store = IPRightsStore(db)
    notice = store.submit_notice(claimant_user_id="claimant", payload=notice_payload())["notice"]
    store.owner_review_notice(
        notice_id=notice["id"],
        payload=OwnerNoticeReviewInput(status="under_review", reason="Initial legal review"),
    )
    assert store.owner_get(notice["id"])["audit_chain_valid"] is True
    with sqlite3.connect(db) as con:
        con.execute(
            "UPDATE ip_rights_events SET data_json=? WHERE notice_id=? AND action='rights_notice_submitted'",
            ('{"tampered":true}', notice["id"]),
        )
    assert store.owner_get(notice["id"])["audit_chain_valid"] is False


def test_member_routes_bind_to_authenticated_identity_and_owner_routes_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "routes.sqlite3"))
    app = FastAPI()

    @app.middleware("http")
    async def bind_member(request: Request, call_next):
        request.state.member = SimpleNamespace(user_id="authenticated-member")
        return await call_next(request)

    app.include_router(member_router)
    app.include_router(owner_router)
    client = TestClient(app)
    body = notice_payload().model_dump()
    body["claimant_user_id"] = "spoofed-member"
    response = client.post("/member/ip-rights/notices", json=body)
    assert response.status_code == 200
    assert len(IPRightsStore(tmp_path / "routes.sqlite3").list_for_member("authenticated-member")) == 1
    assert IPRightsStore(tmp_path / "routes.sqlite3").list_for_member("spoofed-member") == []
    assert client.get("/owner/ip-rights/notices").status_code == 403
    assert "/owner/ip-rights/notices" not in app.openapi()["paths"]


def test_integration_overlay_mounts_ip_routes(monkeypatch, tmp_path):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "integration.sqlite3"))
    from aura_music_studio.creative_version_autopromotion import router as integration_router

    app = FastAPI()
    app.include_router(integration_router)
    paths = app.openapi()["paths"]
    assert "/member/ip-rights/notices" in paths
    assert "/member/ip-rights/notices/{notice_id}/counter-notices" in paths
    # Hidden owner route must still be mounted; unauthenticated dispatch reaches owner auth and returns 403.
    client = TestClient(app)
    assert client.get("/owner/ip-rights/notices").status_code == 403
    assert "/owner/ip-rights/notices" not in paths