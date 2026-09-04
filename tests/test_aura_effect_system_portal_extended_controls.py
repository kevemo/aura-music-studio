from __future__ import annotations

from types import SimpleNamespace

from aura_music_studio.aura_effect_system_portal import effect_system_creator_page


def _request(plan: str = "pro"):
    return SimpleNamespace(
        state=SimpleNamespace(
            member=SimpleNamespace(plan=SimpleNamespace(id=plan), user_id="member-a")
        )
    )


def _html() -> str:
    return effect_system_creator_page(_request()).body.decode("utf-8")


def test_browser_editor_exposes_mix_automation_controls_and_bounded_contract():
    html = _html()
    for element_id in (
        'id="automationNode"',
        'id="interpolation"',
        'id="loadAutomation"',
        'id="addPoint"',
        'id="saveAutomation"',
        'id="clearAutomation"',
        'id="restoreAutomation"',
        'id="automationPoints"',
    ):
        assert element_id in html
    assert "Automation is limited to 2,000 keyframes." in html
    assert '<option value="linear">Linear</option>' in html
    assert '<option value="smooth">Smooth</option>' in html
    assert '<option value="hold">Hold</option>' in html
    assert "/mix-automation`" in html
    assert "/automation/restore/${encodeURIComponent(automationRevision)}" in html


def test_browser_editor_exposes_private_reusable_library_without_marketplace_widening():
    html = _html()
    for element_id in (
        'id="publishPrivate"',
        'id="loadLibrary"',
        'id="library"',
    ):
        assert element_id in html
    assert "/publish-private`" in html
    assert "await api('/library')" in html
    assert "/library/${encodeURIComponent(item.item_id)}/import" in html
    assert "Private reuse only." in html
    assert "do not publish to the public marketplace" in html
    assert "enable sales" in html
    assert "payment authority" in html


def test_extended_browser_controls_keep_same_origin_and_no_eval_dom_boundary():
    html = _html()
    assert "credentials:'same-origin'" in html
    assert "textContent" in html
    assert "eval(" not in html
    assert ".innerHTML" not in html
    assert "shell, process or device commands" in html


def test_automation_edits_remain_revision_backed_and_separate_from_apply_preview_token():
    html = _html()
    assert "automationRevision=data.revision_id||''" in html
    assert "Mix automation saved with a revision checkpoint." in html
    assert "Mix automation cleared with undo available." in html
    assert "previewToken=''" in html
    assert "expected_fingerprint:previewToken" in html
