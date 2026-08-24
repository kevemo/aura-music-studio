from __future__ import annotations

import pytest

from aura_music_studio.workspace_theme import DEFAULT_THEME, WorkspaceThemeStore, theme_css, validate_theme_patch


def test_theme_preview_does_not_change_saved_theme(tmp_path):
    store = WorkspaceThemeStore(tmp_path / "themes.sqlite3")
    preview = store.create_preview(
        "member:user-a",
        {"accent": "#ff8800", "background_style": "gradient", "radius_px": 26},
        "Make my workspace warmer",
    )

    current = store.current("member:user-a")
    assert current["version"] == 0
    assert current["theme"] == DEFAULT_THEME
    assert preview["theme"]["accent"] == "#ff8800"
    assert preview["requires_confirmation"] is True
    assert "--workspace-accent:#ff8800" in preview["css"]


def test_confirm_applies_only_the_requested_safe_tokens(tmp_path):
    store = WorkspaceThemeStore(tmp_path / "themes.sqlite3")
    preview = store.create_preview(
        "member:user-a",
        {"accent": "#ff8800", "font_scale": 1.1, "density": "spacious"},
        "Personal workspace",
    )
    confirmed = store.confirm("member:user-a", preview["preview_id"], "Aura for user-a")

    assert confirmed["status"] == "confirmed"
    assert confirmed["version"] == 1
    assert confirmed["theme"]["accent"] == "#ff8800"
    assert confirmed["theme"]["font_scale"] == 1.1
    assert confirmed["theme"]["density"] == "spacious"
    assert confirmed["theme"]["surface"] == DEFAULT_THEME["surface"]


def test_discard_keeps_previous_theme(tmp_path):
    store = WorkspaceThemeStore(tmp_path / "themes.sqlite3")
    first = store.create_preview("member:user-a", {"accent": "#ff8800"}, "first")
    store.confirm("member:user-a", first["preview_id"], "user-a")

    second = store.create_preview("member:user-a", {"accent": "#00ccff"}, "try blue")
    discarded = store.discard("member:user-a", second["preview_id"])

    assert discarded["status"] == "reverted"
    assert store.current("member:user-a")["theme"]["accent"] == "#ff8800"


def test_revert_restores_last_saved_theme(tmp_path):
    store = WorkspaceThemeStore(tmp_path / "themes.sqlite3")
    preview = store.create_preview(
        "member:user-a",
        {"accent": "#ff8800", "background_style": "minimal"},
        "new style",
    )
    store.confirm("member:user-a", preview["preview_id"], "user-a")

    reverted = store.revert_last("member:user-a", "user-a")
    assert reverted["status"] == "reverted"
    assert reverted["version"] == 2
    assert reverted["theme"] == DEFAULT_THEME


def test_owner_profiles_are_separate_even_with_shared_admin_data(tmp_path):
    store = WorkspaceThemeStore(tmp_path / "themes.sqlite3")
    kev = store.create_preview("owner:kev", {"accent": "#ff8800"}, "Kev theme")
    mary = store.create_preview("owner:mary", {"accent": "#ff55cc"}, "Mary theme")
    store.confirm("owner:kev", kev["preview_id"], "Kev — ESP Co-Owner")
    store.confirm("owner:mary", mary["preview_id"], "Mary — ESP Co-Owner")

    assert store.current("owner:kev")["theme"]["accent"] == "#ff8800"
    assert store.current("owner:mary")["theme"]["accent"] == "#ff55cc"
    assert store.current("owner:kev")["theme"] != store.current("owner:mary")["theme"]


def test_theme_rejects_arbitrary_css_and_invalid_colours():
    with pytest.raises(ValueError, match="Unsupported theme fields"):
        validate_theme_patch({"custom_css": "body{display:none}"})
    with pytest.raises(ValueError, match="six-digit hex"):
        validate_theme_patch({"accent": "javascript:alert(1)"})
    with pytest.raises(ValueError, match="six-digit hex"):
        validate_theme_patch({"accent": "#fff"})


def test_generated_css_uses_validated_tokens_only():
    css = theme_css({"accent": "#123456", "font_style": "mono", "motion": "reduced"})
    assert "--workspace-accent:#123456" in css
    assert "ui-monospace" in css
    assert "--workspace-motion:0.01ms" in css
