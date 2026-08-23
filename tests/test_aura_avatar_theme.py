from aura_music_studio import aura_avatar_bootstrap
from aura_music_studio import aura_avatar_theme_tools  # noqa: F401 - installs runtime extension


def test_niche_theme_runtime_is_installed_once():
    runtime = aura_avatar_bootstrap.PATCHED_RUNTIME_JS
    assert runtime.count("/* AURA_NICHE_THEME_RUNTIME */") == 1
    assert "AuraEmbodiedRuntime.prototype.setTheme" in runtime
    assert "AuraEmbodiedRuntime.prototype.restoreTheme" in runtime
    assert "addEventListener('aura:theme'" in runtime


def test_canonical_identity_is_not_replaced_by_theme():
    runtime = aura_avatar_bootstrap.PATCHED_RUNTIME_JS
    # Theme code changes material emission only; it never loads a different avatar/model URL.
    marker = runtime.split("/* AURA_NICHE_THEME_RUNTIME */", 1)[1]
    assert "loadModel(" not in marker
    assert "model.glb" not in marker
    assert "heartMaterials" in marker
    assert "circuitMaterials" in marker


def test_expected_creator_niche_palettes_exist():
    runtime = aura_avatar_bootstrap.PATCHED_RUNTIME_JS
    for niche in (
        "music", "gaming", "beauty", "food", "fitness", "art", "business",
        "spiritual", "education", "technology", "lifestyle", "asmr", "dance",
    ):
        assert f"{niche}:{{" in runtime
