from __future__ import annotations

from fastapi import FastAPI

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_niche import EspNicheStore, NICHE_CATALOG, social_access_reason
from aura_music_studio.social_management_api import router as social_api_router
from aura_music_studio.social_management_portal import router as social_portal_router


def _active_esp_creator(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    signup = accounts.signup(
        "niche.creator@example.com",
        "ESP Niche Creator",
        "very-secure-test-password",
        "free",
    )
    user = accounts.decide_membership(signup.approval_token, "approve", "ESP Test Owner")
    _request, token = esp.request_access(
        user["id"],
        "creator",
        "niche.creator",
        "UK+",
        "test niche access",
    )
    approved = esp.decide(token, "approve", "creator", "ESP Test Owner")
    return accounts, esp, approved


def _mounted_paths(router) -> list[str]:
    """Inspect effective public paths instead of FastAPI's private router internals."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router)
    return [route.path for route in app.routes if hasattr(route, "path")]


def test_niche_profile_persists_theme_training_and_creator_goals(tmp_path):
    _accounts, esp, user = _active_esp_creator(tmp_path)
    niches = EspNicheStore(esp)
    profile = niches.set(
        user["id"],
        niche="music",
        sub_niche="singer-songwriter",
        audience="People who enjoy original acoustic music",
        goals=["Improve LIVE retention", "Build repeat viewers"],
        network_status="esp_only",
    )

    assert profile["niche"] == "music"
    assert profile["sub_niche"] == "singer-songwriter"
    assert profile["goals"] == ["Improve LIVE retention", "Build repeat viewers"]
    assert profile["catalog"]["title"] == "Music & Performing Arts"
    assert profile["catalog"]["theme"]["accent"]
    assert len(profile["catalog"]["training"]) >= 5

    reloaded = EspNicheStore(esp).get(user["id"])
    assert reloaded["network_status"] == "esp_only"
    assert reloaded["catalog"]["icon"] == "🎵"


def test_other_creator_network_blocks_esp_social_management(tmp_path):
    _accounts, esp, user = _active_esp_creator(tmp_path)
    profile = EspNicheStore(esp).set(
        user["id"],
        niche="gaming",
        network_status="other_network",
    )
    membership = esp.membership(user["id"])

    allowed, reason = social_access_reason(membership, profile)
    assert allowed is False
    assert "another Creator Network" in reason
    assert "poach" in reason.lower()


def test_esp_only_profile_allows_social_management(tmp_path):
    _accounts, esp, user = _active_esp_creator(tmp_path)
    profile = EspNicheStore(esp).set(
        user["id"],
        niche="beauty",
        network_status="esp_only",
    )
    membership = esp.membership(user["id"])

    allowed, reason = social_access_reason(membership, profile)
    assert allowed is True
    assert "confirmed" in reason.lower()


def test_missing_or_unsure_affiliation_keeps_social_tools_locked(tmp_path):
    _accounts, esp, user = _active_esp_creator(tmp_path)
    membership = esp.membership(user["id"])

    allowed, reason = social_access_reason(membership, None)
    assert allowed is False
    assert "niche" in reason.lower()

    profile = EspNicheStore(esp).set(
        user["id"],
        niche="education",
        network_status="unsure",
    )
    allowed, reason = social_access_reason(membership, profile)
    assert allowed is False
    assert "affiliation" in reason.lower()


def test_revoked_or_non_esp_membership_cannot_gain_social_access(tmp_path):
    _accounts, esp, user = _active_esp_creator(tmp_path)
    profile = EspNicheStore(esp).set(
        user["id"],
        niche="lifestyle",
        network_status="esp_only",
    )
    esp.revoke(user["id"], "ESP Test Owner")
    membership = esp.membership(user["id"])

    allowed, reason = social_access_reason(membership, profile)
    assert allowed is False
    assert "ESP approval" in reason


def test_all_niches_provide_distinct_training_context():
    assert len(NICHE_CATALOG) >= 20
    for key, definition in NICHE_CATALOG.items():
        assert definition["title"]
        assert definition["icon"]
        assert definition["theme"]["accent"].startswith("#")
        assert definition["theme"]["secondary"].startswith("#")
        assert len(definition["training"]) >= 5, key


def test_social_routes_exist_only_under_private_esp_command_center():
    api_paths = _mounted_paths(social_api_router)
    portal_paths = _mounted_paths(social_portal_router)

    assert api_paths
    assert all(path.startswith("/command-center/api/social") for path in api_paths)
    assert portal_paths == ["/command-center/social"]
    assert "/social-house" not in portal_paths
