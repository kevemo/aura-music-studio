from aura_music_studio.universal_creative_library import BUILTIN_ITEMS, catalogue, catalogue_summary


def test_builtin_catalogue_has_unique_namespaced_ids_and_parameter_contracts():
    ids = [item.id for item in BUILTIN_ITEMS]
    assert len(ids) == len(set(ids))
    assert len(BUILTIN_ITEMS) >= 30
    assert all(item.id.count(".") >= 2 for item in BUILTIN_ITEMS)
    for item in BUILTIN_ITEMS:
        assert item.input_types
        assert item.output_types
        for spec in item.parameters.values():
            assert "type" in spec
            assert "default" in spec


def test_catalogue_spans_video_image_live_game_automation_and_voice():
    summary = catalogue_summary()
    required = {"video", "image", "live", "game", "automation", "voice", "music"}
    assert required.issubset(summary["domains"])


def test_existing_audio_catalogues_are_adapted_not_duplicated_as_new_implementations():
    rows = catalogue(domain="music")
    assert any(row.get("source_catalogue") == "fx_presets" for row in rows)
    assert any(row.get("source_catalogue") == "instrument_catalog" for row in rows)
    assert any(row.get("source_catalogue") == "mastering" for row in rows)
    assert all(row["implementation_status"] == "existing" for row in rows if row.get("source_catalogue"))


def test_provider_required_items_declare_provider_task_and_provenance():
    rows = catalogue(status="external_provider_required")
    assert rows
    assert all(row["provider_task"] for row in rows)
    assert all(row["provenance_required"] is True for row in rows)


def test_catalogue_filters_by_domain_and_category():
    transitions = catalogue(domain="video", category="transition", include_existing_audio=False)
    assert len(transitions) >= 5
    assert all(row["domain"] == "video" and row["category"] == "transition" for row in transitions)
