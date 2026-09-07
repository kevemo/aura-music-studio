from __future__ import annotations

import pytest

from aura_music_studio.studio_catalogue_menus import (
    EFFECT_BANDS,
    STUDIO_MENUS,
    _contains_blocked_brand,
    public_studio_catalogue,
    validate_studio_menus,
)


def test_taxonomy_has_all_required_studios_and_real_depth():
    assert set(STUDIO_MENUS) == {"video", "image", "music", "game", "voice", "live", "social"}
    assert all(len(menus) >= 14 for menus in STUDIO_MENUS.values())
    validate_studio_menus()


def test_effect_bands_are_separate_and_canonical():
    assert [(band.id, band.coin_price) for band in EFFECT_BANDS] == [
        ("core", 0),
        ("silver", 200),
        ("gold", 500),
    ]


def test_public_catalogue_can_filter_one_studio():
    payload = public_studio_catalogue(domain="music")
    assert payload["domains"] == ["music"]
    assert payload["menus"]
    assert all(row["domain"] == "music" for row in payload["menus"])
    assert payload["effect_band_is_separate_from_subscription_plan"] is True


def test_unknown_domain_fails_closed():
    with pytest.raises(ValueError, match="Unknown studio catalogue domain"):
        public_studio_catalogue(domain="unknown")


def test_brand_guard_matches_explicit_brands_not_innocent_substrings():
    assert _contains_blocked_brand("Built with CapCut presets") is True
    assert _contains_blocked_brand("Adobe Premiere workflow") is True
    assert _contains_blocked_brand("Crop and expand canvases intelligently") is False
    assert _contains_blocked_brand("Observation tools and descriptive controls") is False


def test_image_expand_crop_regression_is_valid():
    menu = next(menu for menu in STUDIO_MENUS["image"] if menu.id == "studio.image.expand_crop")
    assert "content-aware crop" in menu.feature_families
    assert _contains_blocked_brand(" ".join((menu.id, menu.label, menu.description, *menu.feature_families, *menu.search_terms))) is False


def test_public_taxonomy_contains_no_explicit_blocked_brand():
    payload = public_studio_catalogue()
    assert payload["third_party_branding_in_public_taxonomy"] is False
    for menu in payload["menus"]:
        text = " ".join(
            (
                menu["id"],
                menu["label"],
                menu["description"],
                *menu["feature_families"],
                *menu["search_terms"],
            )
        )
        assert _contains_blocked_brand(text) is False
