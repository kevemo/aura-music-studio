from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio.creative_library import creative_effects_library_page, router


def _request(plan_id: str = "free"):
    return SimpleNamespace(
        state=SimpleNamespace(
            member=SimpleNamespace(
                plan=SimpleNamespace(id=plan_id),
                user_id="member-effects-browser",
            )
        )
    )


def _html(plan_id: str = "free") -> str:
    response = creative_effects_library_page(_request(plan_id))
    return response.body.decode("utf-8")


def test_effects_library_route_is_mounted_on_creative_router():
    paths = {getattr(route, "path", "") for route in router.routes}
    assert "/creative/effects-library" in paths


def test_effects_library_requires_member_context():
    with pytest.raises(HTTPException) as exc:
        creative_effects_library_page(SimpleNamespace(state=SimpleNamespace()))
    assert exc.value.status_code == 401


def test_effects_library_uses_authoritative_runtime_catalogue_and_purchase_routes():
    html = _html()
    assert "/command-center/api/universal-library" in html
    assert "/runtime-effects" in html
    assert "/purchase" in html
    assert "/preview-plan" in html
    assert "JSON.stringify({idempotency_key:key})" in html
    assert "ccc_price" in html
    assert "entitlement" in html


def test_effects_library_exposes_required_discovery_and_coin_bands():
    html = _html("pro")
    for label in (
        "Search effects, systems, categories or tags",
        "All studios",
        "Core / Free",
        "Silver · 200 CCC",
        "Gold · 500 CCC",
        "Owned / included",
        "Purchases are permanent account unlocks",
    ):
        assert label in html
    assert "Pro plan" in html


def test_effects_library_does_not_fake_generic_apply_or_preview_authority():
    html = _html()
    assert ">Apply<" not in html
    assert "if(row.preview_compile_available)" in html
    assert "No project media was changed." in html
    assert "Owned — runtime implementation is still pending." in html


def test_effects_library_renders_catalogue_text_with_dom_textcontent():
    html = _html()
    assert "node.textContent" in html
    assert "grid.replaceChildren()" in html
    assert "insertAdjacentHTML" not in html
    assert "innerHTML" not in html


def test_effects_library_is_private_and_no_store():
    response = creative_effects_library_page(_request())
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert response.headers["referrer-policy"] == "same-origin"
