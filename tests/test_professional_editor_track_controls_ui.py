from aura_music_studio.professional_editor_lifecycle_inspector import PRO_EDITOR_LIFECYCLE_JS


BLEND_MODES = (
    "normal",
    "multiply",
    "screen",
    "overlay",
    "soft_light",
    "hard_light",
    "darken",
    "lighten",
    "difference",
)


def test_track_composite_controls_are_present_and_pro_gated():
    script = PRO_EDITOR_LIFECYCLE_JS
    assert "Track composite" in script
    assert 'id="proTrackOpacity"' in script
    assert 'id="proTrackBlend"' in script
    assert 'id="proApplyTrackComposite"' in script
    assert "Professional track compositing requires Pro." in script
    assert "trackOpacity.disabled=!isPro" in script
    assert "trackBlend.disabled=!isPro" in script
    assert "trackApply.disabled=!isPro" in script


def test_track_blend_contract_matches_professional_renderer_modes():
    script = PRO_EDITOR_LIFECYCLE_JS
    expected = "const blendModes=['" + "','".join(BLEND_MODES) + "'];"
    assert expected in script
    assert "${blendOptions}" in script


def test_track_controls_resolve_the_selected_items_containing_track():
    script = PRO_EDITOR_LIFECYCLE_JS
    assert "function currentTrack(item=current())" in script
    assert "(branch().tracks||[]).find(track=>(track.item_ids||[]).includes(item.id))" in script
    assert "The selected item is not attached to a track." in script


def test_track_opacity_is_clamped_before_persistence():
    script = PRO_EDITOR_LIFECYCLE_JS
    assert "const clamp01=value=>Math.max(0,Math.min(1" in script
    assert "const opacity=clamp01(byId('proTrackOpacity').value);" in script
    assert "byId('proTrackOpacity').value=String(opacity);" in script


def test_track_composite_patches_the_reversible_track_graph():
    script = PRO_EDITOR_LIFECYCLE_JS
    assert "endpoint(`/tracks/${encodeURIComponent(track.id)}`)" in script
    assert "method:'PATCH'" in script
    assert "changes:{opacity,blend_mode}" in script
    assert "Track composite saved" in script
    assert "Undo can restore it." in script


def test_existing_item_blend_workflow_is_preserved():
    script = PRO_EDITOR_LIFECYCLE_JS
    assert 'id="proBlendMode"' in script
    assert 'id="proApplyBlend"' in script
    assert "endpoint(`/items/${encodeURIComponent(item.id)}`)" in script
    assert "Blend mode saved" in script
