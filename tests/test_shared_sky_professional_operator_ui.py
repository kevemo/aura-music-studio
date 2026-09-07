from __future__ import annotations

from fastapi import FastAPI

from aura_music_studio import shared_sky_professional_canvas as canvas
from aura_music_studio.shared_sky_professional_operator_ui import (
    OPERATOR_HTML,
    OPERATOR_JS,
    enhanced_operator_html,
    install_professional_operator_ui,
)


def _base(_project_id: str) -> str:
    return (
        "<html><head><style>.base{}</style></head><body>"
        "<div class='row'><button id='addBanner'>Banner</button></div>"
        "<aside class='right panel'><section id='transportConsole'></section><h2>Inspector</h2></aside>"
        "<script>const projectId='project-1';function keySafe(){return true};"
        "function render(){};function graphicMedia(){return ''};function rgba(){return ''};"
        "const state={session:{},project:{scenes:[]},selected:new Set(),transport:{}};"
        "const $=()=>null,$$=()=>[];const api=async()=>({});function handle(){};"
        "function assign(){};async function loadHistory(){};async function refreshProject(){};"
        "</script></body></html>"
    )


def test_operator_ui_injection_is_idempotent_and_follows_transport_console():
    once = enhanced_operator_html("project-1", _base)
    twice = enhanced_operator_html("project-1", lambda _project_id: once)
    assert once == twice
    assert once.count("id='operatorConsole'") == 1
    assert once.index("id='transportConsole'") < once.index("id='operatorConsole'")
    assert once.index("id='operatorConsole'") < once.index("<h2>Inspector</h2>")


def test_operator_ui_loads_and_activates_only_server_profiles():
    assert "/operator-profiles`" in OPERATOR_JS
    assert "/activate`" in OPERATOR_JS
    assert "operatorUI.profiles=d.profiles||[]" in OPERATOR_JS
    assert "operatorUI.profiles.find(p=>p.is_active)" in OPERATOR_JS
    assert "localStorage" not in OPERATOR_JS


def test_custom_hotkeys_override_fixed_listener_without_double_fire():
    assert "{capture:true}" in OPERATOR_JS
    assert "stopImmediatePropagation" in OPERATOR_JS
    assert "if(!keySafe(e)" in OPERATOR_JS
    assert "eventShortcut(e)" in OPERATOR_JS


def test_programme_commands_require_confirmation_from_custom_hotkey_and_macro():
    assert "command==='cut'||command==='transition'" in OPERATOR_JS
    assert "custom operator hotkey" in OPERATOR_JS
    assert "macro.commands.some(c=>c==='cut'||c==='transition')" in OPERATOR_JS
    assert "It will change Programme" in OPERATOR_JS


def test_macro_execution_is_explicit_sequential_and_aborts_on_failure():
    assert "data-operator-macro" in OPERATOR_JS
    assert "for(const command of macro.commands)" in OPERATOR_JS
    assert "await executeOperatorCommand(command)" in OPERATOR_JS
    assert "catch{return;}" in OPERATOR_JS
    for forbidden in ("transport_start", "recording_start", "participant_remove", "destination_retry"):
        assert forbidden not in OPERATOR_JS


def test_operator_commands_use_existing_versioned_studio_and_marker_routes():
    for route in ("/cut", "/transition", "/transition/complete", "/preview", "/markers"):
        assert route in OPERATOR_JS
    assert "command==='undo'||command==='redo'" in OPERATOR_JS
    assert "/sessions/${state.session.id}/${command}" in OPERATOR_JS
    assert "expected_version:state.session.version" in OPERATOR_JS
    assert "expected_studio_version" not in OPERATOR_JS


def test_operator_profile_editor_updates_with_server_version_and_allowlisted_commands():
    assert "id='addOperatorHotkey'" in OPERATOR_HTML
    assert "id='addOperatorMacro'" in OPERATOR_HTML
    assert "id='deleteOperatorProfile'" in OPERATOR_HTML
    assert "const OPERATOR_COMMANDS=['cut','transition','undo','redo','scene_next','scene_previous','marker_highlight']" in OPERATOR_JS
    assert "expected_version:profile.version" in OPERATOR_JS
    assert "OPERATOR_COMMANDS.includes(command)" in OPERATOR_JS
    assert "commands.some(c=>!OPERATOR_COMMANDS.includes(c))" in OPERATOR_JS
    assert "confirm_programme:programme" in OPERATOR_JS


def test_profile_selection_is_distinct_from_active_profile_for_safe_delete():
    assert "function selectedOperatorProfile()" in OPERATOR_JS
    assert "const profile=selectedOperatorProfile();if(!profile)return;if(profile.is_active)" in OPERATOR_JS
    assert "Activate another profile before deleting this active profile." in OPERATOR_JS
    assert "Delete inactive operator profile" in OPERATOR_JS
    assert "del.disabled=!selected||Boolean(selected.is_active)" in OPERATOR_JS
    assert "activate.disabled=!selected||Boolean(selected.is_active)" in OPERATOR_JS
    assert "$('#operatorProfile').onchange=updateOperatorButtons" in OPERATOR_JS


def test_hotkeys_and_macros_edit_active_profile_not_merely_selected_profile():
    assert "async function saveActiveOperatorProfile(fields){const profile=activeOperatorProfile()" in OPERATOR_JS
    assert "async function addOperatorHotkeyUI(){const profile=activeOperatorProfile()" in OPERATOR_JS
    assert "async function addOperatorMacroUI(){const profile=activeOperatorProfile()" in OPERATOR_JS
    assert "Hotkeys and macros edit the active profile" in OPERATOR_HTML


def test_composed_installer_wraps_professional_renderer_once(monkeypatch):
    monkeypatch.setattr(canvas, "professional_html", _base)
    app = FastAPI()
    install_professional_operator_ui(app)
    first = canvas.professional_html
    install_professional_operator_ui(app)
    second = canvas.professional_html
    assert first is second
    assert getattr(second, "_shared_sky_operator_ui", False) is True
    assert getattr(second, "_shared_sky_motion_graphics_ui", False) is True
    assert getattr(second, "_shared_sky_ingest_ui", False) is True
    page = second("project-1")
    assert page.count("id='operatorConsole'") == 1
    assert page.count("id='addTicker'") == 1


def test_operator_ui_explains_macro_authority_boundary():
    assert "Transport, recording, participant and destination mutations are not valid macro commands" in OPERATOR_HTML
    assert "Programme-changing macros confirm every run" in OPERATOR_HTML
