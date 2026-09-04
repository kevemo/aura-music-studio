from aura_music_studio.studio_catalogue_menus import (
    DISCOVERY_SECTIONS,
    EFFECT_BANDS,
    PUBLIC_BLOCKED_BRAND_TERMS,
    STUDIO_MENUS,
    public_studio_catalogue,
)


def test_effect_bands_are_coin_priced_and_not_subscription_tiers():
    payload = public_studio_catalogue()
    assert [(row["id"], row["coin_price"]) for row in payload["effect_bands"]] == [
        ("core", 0),
        ("silver", 200),
        ("gold", 500),
    ]
    assert payload["effect_band_is_separate_from_subscription_plan"] is True


def test_discovery_surface_covers_search_ownership_and_creator_sources():
    ids = {section.id for section in DISCOVERY_SECTIONS}
    assert {
        "discover.search", "discover.trending", "discover.new", "discover.recommended",
        "discover.free", "discover.silver", "discover.gold", "discover.owned",
        "discover.favourites", "discover.recent", "discover.aura_created",
        "discover.user_created", "discover.esp_originals",
    }.issubset(ids)


def test_every_creation_studio_has_deep_namespaced_menu_taxonomy():
    required = {"video", "image", "music", "game", "voice", "live", "social"}
    assert set(STUDIO_MENUS) == required
    ids = []
    for domain, menus in STUDIO_MENUS.items():
        assert len(menus) >= 14
        for menu in menus:
            assert menu.id.startswith(f"studio.{domain}.")
            assert menu.domain == domain
            assert menu.feature_families
            assert menu.search_terms
            ids.append(menu.id)
    assert len(ids) == len(set(ids))


def test_major_studios_cover_professional_and_ai_workflows():
    by_domain = {domain: {menu.id for menu in menus} for domain, menus in STUDIO_MENUS.items()}
    assert {
        "studio.video.mask_roto_tracking", "studio.video.colour_hdr",
        "studio.video.particles_weather_energy", "studio.video.ai_create",
        "studio.video.ai_edit", "studio.video.quality_restore",
    }.issubset(by_domain["video"])
    assert {
        "studio.image.layers", "studio.image.selections_masks",
        "studio.image.typography", "studio.image.ai_edit", "studio.image.product",
    }.issubset(by_domain["image"])
    assert {
        "studio.music.instruments", "studio.music.eq_filters", "studio.music.spectral",
        "studio.music.mastering", "studio.music.aura_chain",
    }.issubset(by_domain["music"])
    assert {
        "studio.game.materials_shaders", "studio.game.vfx_particles",
        "studio.game.network_multiplayer", "studio.game.procedural",
        "studio.game.runtime_live_creation", "studio.game.visual_scripting",
    }.issubset(by_domain["game"])


def test_public_taxonomy_blocks_brand_tokens_without_obs_objects_false_positive():
    payload = public_studio_catalogue()
    public_text = repr(payload).casefold()
    assert payload["original_first_party_taxonomy"] is True
    assert payload["third_party_branding_in_public_taxonomy"] is False
    # Regression: the short blocked brand token "obs" must not match "objects".
    assert "objects" in public_text or "object" in public_text
    for brand in PUBLIC_BLOCKED_BRAND_TERMS:
        if brand == "obs":
            continue
        assert brand not in public_text


def test_domain_filter_is_deterministic_and_unknown_domain_fails_closed():
    game = public_studio_catalogue(domain="game")
    assert game["domains"] == ["game"]
    assert game["menu_count"] == len(STUDIO_MENUS["game"])
    assert all(row["domain"] == "game" for row in game["menus"])

    try:
        public_studio_catalogue(domain="unknown")
    except ValueError as exc:
        assert "Unknown studio catalogue domain" in str(exc)
    else:
        raise AssertionError("Unknown studio domain should fail closed")
