from aura_music_studio import aura_avatar_bootstrap
from aura_music_studio import aura_avatar_theme_tools  # noqa: F401 - installs runtime extension


def test_niche_theme_runtime_is_installed_once():
    runtime = aura_avatar_bootstrap.PATCHED_RUNTIME_JS
    assert runtime.count("/* AURA_NICHE_THEME_RUNTIME */") == 1
    assert "AuraEmbodiedRuntime.prototype.setTheme" in runtime
    assert "AuraEmbodiedRuntime.prototype.restoreTheme" in runtime
    assert "AuraEmbodiedRuntime.prototype.detectPageEnergyTheme" in runtime
    assert "AuraEmbodiedRuntime.prototype.setEnergyMode" in runtime
    assert "addEventListener('aura:theme'" in runtime


def test_canonical_identity_is_not_replaced_by_theme():
    runtime = aura_avatar_bootstrap.PATCHED_RUNTIME_JS
    # Theme/communication code only changes Aura's existing material energy. It never loads
    # another body, face, model URL or generic avatar.
    marker = runtime.split("/* AURA_NICHE_THEME_RUNTIME */", 1)[1]
    assert "loadModel(" not in marker
    assert "model.glb" not in marker
    assert "heartMaterials" in marker
    assert "circuitMaterials" in marker
    assert "eyeMaterials" in marker


def test_expected_creator_niche_palettes_exist():
    runtime = aura_avatar_bootstrap.PATCHED_RUNTIME_JS
    for niche in (
        "music", "gaming", "beauty", "food", "fitness", "art", "business",
        "spiritual", "education", "technology", "lifestyle", "asmr", "dance",
    ):
        assert f"{niche}:{{" in runtime
    assert "eyes:" in runtime


def test_communication_energy_modes_drive_glow_without_changing_theme_identity():
    runtime = aura_avatar_bootstrap.PATCHED_RUNTIME_JS
    for mode in ("idle", "listening", "speaking", "thinking", "creating", "translating", "guiding", "celebrate"):
        assert f"{mode}:{{" in runtime
    assert "this.speechEnergy" in runtime
    assert "emissiveIntensity" in runtime
    assert "this.energyColors.heart.lerp" in runtime
    assert "this.energyColors.circuit.lerp" in runtime
    assert "this.energyColors.eyes.lerp" in runtime
    assert "prefers-reduced-motion" in runtime


def test_active_page_context_can_retheme_aura_without_reload():
    runtime = aura_avatar_bootstrap.PATCHED_RUNTIME_JS
    assert "AURA_PAGE_THEME_HINTS" in runtime
    assert "data-niche" in runtime
    assert "data-workspace" in runtime
    assert "data-section" in runtime
    assert "addEventListener('aura:page-context'" in runtime
    assert "addEventListener('popstate'" in runtime
    assert "pushState" in runtime
    assert "replaceState" in runtime
    assert "MutationObserver" in runtime


def test_creative_and_translation_events_have_distinct_energy_signatures():
    runtime = aura_avatar_bootstrap.PATCHED_RUNTIME_JS
    assert "addEventListener('aura:creating'" in runtime
    assert "addEventListener('aura:translating'" in runtime
    assert "addEventListener('aura:researching'" in runtime
    assert "addEventListener('aura:guide'" in runtime
    assert "addEventListener('aura:celebrate'" in runtime
