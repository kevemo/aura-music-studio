from __future__ import annotations

from aura_music_studio.aura_effect_system_portal import _EFFECT_SYSTEM_CREATOR_HTML


def test_creator_exposes_bounded_canonical_catalogue_search() -> None:
    html = _EFFECT_SYSTEM_CREATOR_HTML

    assert 'id="catalogueSearch"' in html
    assert 'maxlength="160"' in html
    assert 'id="catalogueStudio"' in html
    assert '<option value="music">Music effects</option>' in html
    assert "new URLSearchParams({studio,limit:'40'})" in html
    assert "api(`/catalogue?${params.toString()}`)" in html
    assert "credentials:'same-origin'" in html


def test_catalogue_selection_is_metadata_only_until_explicit_add() -> None:
    html = _EFFECT_SYSTEM_CREATOR_HTML

    assert 'Selecting an item does not grant ownership, preview, apply or execute it.' in html
    assert "selectedCatalogueId=id;q('catalogueSelection').value=id" in html
    assert "select.onclick=()=>" in html
    assert "q('addNode').onclick=()=>" in html
    assert "const effect=q('catalogueSelection').value.trim()" in html
    assert "Preview is still required." in html
    assert "window.prompt" not in html


def test_catalogue_results_render_untrusted_metadata_without_html_injection() -> None:
    html = _EFFECT_SYSTEM_CREATOR_HTML

    assert "item.effect_id||item.catalogue_item_id||item.id" in html
    assert "title.textContent=String(item.label||id)" in html
    assert "summary.textContent=String(item.description||'Canonical catalogue effect.')" in html
    assert "idText.textContent=id" in html
    assert "item.entitlement" in html
    assert "item.ccc_price" in html
    assert "const runtimeName=String(item.runtime_name||item.runtime||'').trim()" in html
    assert "runtime.textContent=runtimeName" in html
    assert "status.textContent=String(item.status)" in html
    assert ".innerHTML" not in html


def test_selector_does_not_create_an_alternate_apply_path() -> None:
    html = _EFFECT_SYSTEM_CREATOR_HTML

    assert html.count("q('apply').onclick") == 1
    assert html.count("/apply`") == 1
    assert "expected_fingerprint:previewToken" in html
    assert "q('apply').disabled=!previewToken||!canApply" in html
