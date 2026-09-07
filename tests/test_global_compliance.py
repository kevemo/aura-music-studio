from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.global_compliance import (
    TikTokLivePreflight,
    canonical_locale,
    evaluate_tiktok_live_preflight,
    manifest,
    notices,
    router,
)


def test_locale_resolution_is_bounded_and_has_rtl_metadata():
    assert canonical_locale("es-MX") == "es"
    assert canonical_locale("pt_BR") == "pt-BR"
    assert canonical_locale("not-a-real-locale") == "en-GB"
    assert notices("ar")["dir"] == "rtl"
    assert notices("fr")["title"] != notices("en-GB")["title"]


def test_manifest_never_claims_legal_certification_or_grants_roles():
    data = manifest("de")
    assert data["legal_certification"] is False
    assert data["continuous_compliance"] is True
    assert data["translation_legal_review_required"] is True
    assert "never grant" in data["role_boundary"].lower()
    assert len(data["sources"]) >= 6


def test_under_18_creator_is_blocked_from_tiktok_live_preflight():
    result = evaluate_tiktok_live_preflight(TikTokLivePreflight(creator_age=17))
    assert result["status"] == "blocked"
    assert any(item["code"] == "tiktok_live_age" for item in result["findings"])
    assert result["grants_esp_role_or_permission"] is False


def test_hate_harassment_weapons_gambling_and_gift_pressure_are_blockers():
    result = evaluate_tiktok_live_preflight(
        TikTokLivePreflight(
            creator_age=18,
            hate_or_protected_class_attack=True,
            bullying_or_harassment=True,
            firearms_or_explosive_weapons=True,
            gambling_or_gambling_like=True,
            gift_or_engagement_pressure=True,
        )
    )
    codes = {item["code"] for item in result["findings"] if item["severity"] == "block"}
    assert {"hate", "harassment", "live_weapons", "live_gambling", "gift_pressure"} <= codes
    assert result["status"] == "blocked"


def test_commercial_live_requires_disclosure_and_third_party_tools_require_moderation():
    result = evaluate_tiktok_live_preflight(
        TikTokLivePreflight(
            creator_age=30,
            commercial_content=True,
            commercial_disclosure_enabled=False,
            third_party_tools_enabled=True,
            third_party_tools_moderated=False,
        )
    )
    codes = {item["code"] for item in result["findings"]}
    assert "commercial_disclosure" in codes
    assert "third_party_tools" in codes
    assert result["status"] == "blocked"


def test_ai_and_professional_topics_require_review_not_fake_professional_authority():
    result = evaluate_tiktok_live_preflight(
        TikTokLivePreflight(
            creator_age=25,
            ai_generated_or_materially_edited=True,
            ai_disclosure_planned=False,
            professional_topic="medical",
            professional_disclaimer_planned=False,
        )
    )
    assert result["status"] == "review"
    assert result["human_review_required"] is True
    assert {item["code"] for item in result["findings"]} == {"ai_transparency", "professional_boundary"}
    assert result["legal_certification"] is False


def test_clean_live_preflight_passes_without_granting_any_esp_permission():
    result = evaluate_tiktok_live_preflight(
        TikTokLivePreflight(
            creator_age=25,
            commercial_content=True,
            commercial_disclosure_enabled=True,
            third_party_tools_enabled=True,
            third_party_tools_moderated=True,
            ai_generated_or_materially_edited=True,
            ai_disclosure_planned=True,
            professional_topic="financial",
            professional_disclaimer_planned=True,
        )
    )
    assert result["status"] == "pass"
    assert result["findings"] == []
    assert result["grants_esp_role_or_permission"] is False


def test_compliance_routes_are_real_and_locale_aware():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    page = client.get("/compliance?lang=es")
    assert page.status_code == 200
    assert "Centro Global" in page.text

    data = client.get("/compliance/manifest.json", headers={"accept-language": "ar, en;q=0.8"})
    assert data.status_code == 200
    assert data.json()["notices"]["dir"] == "rtl"

    preflight = client.post("/compliance/tiktok-live/preflight", json={"creator_age": 16})
    assert preflight.status_code == 200
    assert preflight.json()["status"] == "blocked"
    assert preflight.headers["cache-control"] == "private, no-store"
