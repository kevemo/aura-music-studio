from __future__ import annotations

import sqlite3

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_niche import EspNicheStore, NICHE_CATALOG


def _active_free_user(store: AccountStore, email: str):
    signup = store.signup(email, "ESP Niche Test", "very-secure-password", "free")
    user = store.decide_membership(signup.approval_token, "approve", "ESP Test Owner")
    assert user["status"] == "active"
    return user


def _esp_user(store: AccountStore, esp: EspStore, email: str, role: str = "creator"):
    user = _active_free_user(store, email)
    _item, token = esp.request_access(user["id"], role, "niche.test", "UK+", "niche test")
    approved = esp.decide(token, "approve", role, "ESP Test Owner")
    return approved


def test_ordinary_customer_has_no_esp_niche_access(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    EspStore(store)
    niches = EspNicheStore(store)
    user = _active_free_user(store, "ordinary@example.com")

    assert niches.dashboard_context(user["id"]) is None
    assert niches.dashboard_fragment(user["id"]) == ""
    with pytest.raises(PermissionError):
        niches.set_preference(user["id"], "music")


def test_active_creator_agent_both_and_owner_can_have_niche_context(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(store)
    niches = EspNicheStore(store)

    for role in ("creator", "agent", "both"):
        user = _esp_user(store, esp, f"{role}@example.com", role)
        context = niches.dashboard_context(user["id"])
        assert context is not None
        assert context["onboarding_required"] is True
        assert context["membership"]["roles"] == role

    owner = _active_free_user(store, "owner@example.com")
    with sqlite3.connect(store.db_path) as con:
        con.execute(
            "INSERT INTO esp_memberships(user_id,status,roles,region,updated_at) VALUES (?,'owner','owner','Global',datetime('now'))",
            (owner["id"],),
        )
    assert niches.dashboard_context(owner["id"])["membership"]["roles"] == "owner"


def test_niche_catalog_contains_broad_creator_coverage():
    required = {
        "music", "gaming", "just-chatting", "comedy", "beauty", "fashion", "fitness", "food",
        "education", "business", "technology", "art", "asmr", "spiritual", "travel", "dance",
        "automotive", "pets", "family", "books", "history", "science", "languages", "reaction",
        "reviews", "podcasts", "news-commentary", "multi-niche", "other",
    }
    assert required.issubset(NICHE_CATALOG)
    assert len(NICHE_CATALOG) >= 40


def test_preference_persists_and_builds_personalized_workspace(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(store)
    niches = EspNicheStore(store)
    user = _esp_user(store, esp, "music@example.com", "creator")

    saved = niches.set_preference(user["id"], "music")
    assert saved["primary_niche"] == "music"
    context = niches.dashboard_context(user["id"])
    assert context["onboarding_required"] is False
    assert context["primary"]["title"] == "Music / Singing"
    assert context["theme"] == "music"
    assert "Music LIVE structure" in context["modules"]
    assert any(level.startswith("Beginner · Music / Singing") for level in context["academy"])

    fragment = niches.dashboard_fragment(user["id"])
    assert "ESP Creator Workspace" in fragment
    assert "Music / Singing" in fragment
    assert "aura:theme" in fragment
    assert "/esp/niche/preference" in fragment


def test_first_login_fragment_is_visual_and_forces_niche_choice(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(store)
    niches = EspNicheStore(store)
    user = _esp_user(store, esp, "firstlogin@example.com", "creator")

    fragment = niches.dashboard_fragment(user["id"])
    assert "What type of creator are you?" in fragment
    assert "esp-niche-grid" in fragment
    assert "data-open-on-load='true'" in fragment
    assert "Build my ESP workspace" in fragment


def test_multi_niche_requires_two_to_five_standard_niches(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(store)
    niches = EspNicheStore(store)
    user = _esp_user(store, esp, "multi@example.com", "creator")

    with pytest.raises(ValueError, match="between 2 and 5"):
        niches.set_preference(user["id"], "multi-niche", ["music"])

    saved = niches.set_preference(user["id"], "multi-niche", ["music", "gaming", "music"])
    assert saved["secondary_niches"] == ["music", "gaming"]
    context = niches.dashboard_context(user["id"])
    assert "Music LIVE structure" in context["modules"]
    assert "Gaming LIVE structure" in context["modules"]

    with pytest.raises(ValueError, match="no more than 5"):
        niches.set_preference(user["id"], "multi-niche", ["music", "gaming", "beauty", "fashion", "food", "fitness"])


def test_other_niche_requires_creator_description(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(store)
    niches = EspNicheStore(store)
    user = _esp_user(store, esp, "other@example.com", "creator")

    with pytest.raises(ValueError, match="Describe the niche"):
        niches.set_preference(user["id"], "other", custom_niche="")

    saved = niches.set_preference(user["id"], "other", custom_niche="Vintage restoration and historical craftsmanship")
    assert saved["custom_niche"] == "Vintage restoration and historical craftsmanship"
    assert niches.dashboard_context(user["id"])["academy"][0].endswith("Vintage restoration and historical craftsmanship")


def test_invalid_and_special_secondary_niches_are_rejected(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(store)
    niches = EspNicheStore(store)
    user = _esp_user(store, esp, "invalid@example.com", "creator")

    with pytest.raises(ValueError, match="valid creator niche"):
        niches.set_preference(user["id"], "definitely-not-a-niche")
    with pytest.raises(ValueError, match="standard creator niches"):
        niches.set_preference(user["id"], "multi-niche", ["music", "other"])


def test_niche_preferences_are_user_isolated(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(store)
    niches = EspNicheStore(store)
    music_user = _esp_user(store, esp, "one@example.com", "creator")
    gaming_user = _esp_user(store, esp, "two@example.com", "creator")

    niches.set_preference(music_user["id"], "music")
    niches.set_preference(gaming_user["id"], "gaming")

    assert niches.get_preference(music_user["id"])["primary_niche"] == "music"
    assert niches.get_preference(gaming_user["id"])["primary_niche"] == "gaming"
    assert niches.dashboard_context(music_user["id"])["theme"] == "music"
    assert niches.dashboard_context(gaming_user["id"])["theme"] == "gaming"


def test_creator_niche_context_contains_no_private_agent_or_admin_modules(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(store)
    niches = EspNicheStore(store)
    user = _esp_user(store, esp, "boundary@example.com", "creator")
    niches.set_preference(user["id"], "business")

    serialized = str(niches.dashboard_context(user["id"])).lower()
    assert "agent academy" not in serialized
    assert "admin" not in serialized
    assert "commission" not in serialized
    assert "lead management" not in serialized
