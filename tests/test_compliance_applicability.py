from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.compliance_applicability import (
    PolicyRegistryStore,
    PolicyReviewInput,
    evaluate_applicability,
    owner_router,
    router,
)


def test_eu_ai_transparency_evidence_is_effective_from_august_2026(tmp_path):
    store = PolicyRegistryStore(tmp_path / "registry.sqlite3")
    before = evaluate_applicability(
        country="ES",
        feature="ai_generated_content",
        as_of=date(2026, 8, 1),
        registry_store=store,
    )
    assert any(row["policy_id"] == "eu-ai-act-article-50" for row in before["upcoming_policies"])

    effective = evaluate_applicability(
        country="ES",
        feature="ai_generated_content",
        as_of=date(2026, 8, 28),
        registry_store=store,
    )
    assert any(row["policy_id"] == "eu-ai-act-article-50" for row in effective["applicable_policies"])
    assert effective["decision"] == "policy_evidence_available"
    assert effective["coverage_complete"] is False
    assert effective["legal_certification"] is False


def test_unknown_local_coverage_fails_closed_to_qualified_review(tmp_path):
    store = PolicyRegistryStore(tmp_path / "registry.sqlite3")
    result = evaluate_applicability(
        country="NZ",
        feature="live",
        user_role="creator",
        age=25,
        as_of=date(2026, 8, 28),
        registry_store=store,
    )
    assert result["decision"] == "requires_qualified_legal_review"
    assert "no_jurisdiction_specific_evidence_for_feature" in result["reasons"]
    assert result["grants_esp_role_or_permission"] is False
    assert result["alters_billing_or_membership"] is False


def test_age_findings_do_not_claim_tiktok_eligibility(tmp_path):
    store = PolicyRegistryStore(tmp_path / "registry.sqlite3")
    result = evaluate_applicability(
        country="GB",
        feature="live",
        user_role="creator",
        age=17,
        as_of=date(2026, 8, 28),
        registry_store=store,
    )
    assert {row["finding"] for row in result["age_findings"]} == {"age_below_policy_minimum"}
    assert result["decision"] == "requires_qualified_legal_review"
    assert result["legal_advice"] is False
    assert result["legal_certification"] is False


def test_california_region_isolated_from_other_us_queries(tmp_path):
    store = PolicyRegistryStore(tmp_path / "registry.sqlite3")
    ca = evaluate_applicability(
        country="US", region="CA", feature="privacy", as_of=date(2026, 8, 28), registry_store=store
    )
    tx = evaluate_applicability(
        country="US", region="TX", feature="privacy", as_of=date(2026, 8, 28), registry_store=store
    )
    assert any(row["policy_id"] == "california-ccpa" for row in ca["applicable_policies"])
    assert all(row["policy_id"] != "california-ccpa" for row in tx["applicable_policies"])
    assert tx["decision"] == "requires_qualified_legal_review"


def test_append_only_owner_review_can_mark_policy_evidence_stale(tmp_path):
    store = PolicyRegistryStore(tmp_path / "registry.sqlite3")
    body = PolicyReviewInput(
        policy_id="uk-data-protection-baseline",
        jurisdiction="GB",
        title="UK data protection reviewed baseline",
        source_url="https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/",
        effective_from=date(2021, 1, 1),
        reviewed_at=date(2026, 6, 1),
        next_review_at=date(2026, 7, 1),
        status="active",
        features=["privacy"],
        roles=["*"],
        confidence="high",
        source_class="official_regulator",
        evidence_reference="ico-review:2026-06",
    )
    first = store.append_review(body)
    second = store.append_review(body.model_copy(update={"evidence_reference": "ico-review:2026-06-repeat"}))
    assert first["id"] != second["id"]
    assert len(store.latest_reviews()) == 1

    result = evaluate_applicability(
        country="GB", feature="privacy", as_of=date(2026, 8, 28), registry_store=store
    )
    assert result["decision"] == "requires_qualified_legal_review"
    assert result["stale_policy_ids"] == ["uk-data-protection-baseline"]


def test_owner_policy_review_route_hidden_and_requires_owner():
    app = FastAPI()
    app.include_router(router)
    app.include_router(owner_router)
    assert "/compliance/applicability" in app.openapi()["paths"]
    assert "/owner/compliance/policy-registry/reviews" not in app.openapi()["paths"]

    client = TestClient(app)
    response = client.post(
        "/owner/compliance/policy-registry/reviews",
        json={
            "policy_id": "example-policy",
            "jurisdiction": "GB",
            "title": "Example reviewed policy",
            "source_url": "https://example.gov/policy",
            "effective_from": "2026-01-01",
            "reviewed_at": "2026-08-28",
            "next_review_at": "2026-09-28",
            "status": "active",
            "features": ["privacy"],
            "roles": ["member"],
            "confidence": "high",
            "source_class": "official_government",
            "evidence_reference": "review:example-1"
        },
    )
    assert response.status_code == 403


def test_integration_overlay_dispatches_applicability_routes():
    from aura_music_studio.creative_version_autopromotion import router as integration_router

    app = FastAPI()
    app.include_router(integration_router)
    client = TestClient(app)

    member_response = client.get("/compliance/applicability?country=GB&feature=privacy")
    assert member_response.status_code != 404

    owner_response = client.post(
        "/owner/compliance/policy-registry/reviews",
        json={
            "policy_id": "integration-probe",
            "jurisdiction": "GB",
            "title": "Integration probe",
            "source_url": "https://example.gov/policy",
            "effective_from": "2026-01-01",
            "reviewed_at": "2026-08-28",
            "next_review_at": "2026-09-28",
            "status": "active",
            "features": ["privacy"],
            "roles": ["member"],
            "confidence": "high",
            "source_class": "official_government",
            "evidence_reference": "review:integration-probe"
        },
    )
    assert owner_response.status_code == 403
