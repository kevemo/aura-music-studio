from aura_music_studio.universal_capabilities import CAPABILITIES, capability_index, capability_summary


def test_universal_capability_registry_is_large_namespaced_and_unique():
    ids = [row.id for row in CAPABILITIES]
    assert len(CAPABILITIES) >= 100
    assert len(ids) == len(set(ids))
    assert all("." in row.id for row in CAPABILITIES)


def test_registry_spans_every_command_center_creation_domain():
    summary = capability_summary()
    required = {
        "music",
        "voice",
        "video",
        "image",
        "game",
        "live",
        "aura",
        "automation",
        "social",
        "assets",
        "security",
    }
    assert required.issubset(summary["domains"])


def test_registry_enforces_original_implementation_policy():
    policy = capability_summary()["policy"]
    assert policy["original_implementation_only"] is True
    assert policy["copy_proprietary_source"] is False
    assert policy["copy_protected_assets"] is False
    assert policy["copy_closed_model_weights"] is False


def test_domain_and_status_filters_are_stable():
    music = capability_index(domain="music")
    assert music
    assert all(row["domain"] == "music" for row in music)

    existing = capability_index(status="existing_foundation")
    assert existing
    assert all(row["status"] == "existing_foundation" for row in existing)


def test_every_capability_has_a_concrete_implementation_contract():
    assert all(row.implementation.strip() for row in CAPABILITIES)
    assert all(row.label.strip() for row in CAPABILITIES)
    assert all(row.category.strip() for row in CAPABILITIES)
