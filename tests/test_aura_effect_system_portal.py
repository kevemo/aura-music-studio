from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio.aura_effect_system_api import effect_system_route_registrations
from aura_music_studio.aura_effect_system_portal import effect_system_creator_page


def _request(plan: str = "pro"):
    return SimpleNamespace(state=SimpleNamespace(member=SimpleNamespace(plan=SimpleNamespace(id=plan), user_id="member-a")))


def test_effect_system_creator_page_requires_membership():
    with pytest.raises(HTTPException) as exc:
        effect_system_creator_page(SimpleNamespace(state=SimpleNamespace(member=None)))
    assert exc.value.status_code == 401


def test_effect_system_creator_page_is_private_no_store_and_uses_exact_api_contract():
    response = effect_system_creator_page(_request())
    html = response.body.decode("utf-8")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "same-origin"
    assert "noindex,nofollow" in html
    assert "Rhiannon Effect/System Creator" in html
    assert "__PLAN__" not in html
    assert "pro plan" in html
    assert "'/command-center/api/universal-library/effect-systems'" in html
    assert "'/compose'" in html
    assert "/tracks/${encodeURIComponent(track)}/preview" in html
    assert "/tracks/${encodeURIComponent(track)}/apply" in html
    assert "/restore/${encodeURIComponent(lastRevision)}" in html


def test_browser_editor_exposes_real_edit_preview_save_apply_undo_controls():
    html = effect_system_creator_page(_request()).body.decode("utf-8")
    for element_id in (
        'id="prompt"',
        'id="nodes"',
        'id="save"',
        'id="preview"',
        'id="apply"',
        'id="undo"',
        'id="saved"',
        'id="catalogueSearch"',
        'id="catalogueSelection"',
        'id="searchCatalogue"',
        'id="addNode"',
    ):
        assert element_id in html
    assert "Apply previewed graph" in html
    assert "Save version" in html
    assert "Search catalogue" in html
    assert "Add selected effect" in html
    assert "Catalogue discovery is read-only" in html
    assert "previewToken=''" in html
    assert "q('apply').disabled=true" in html
    assert "expected_fingerprint:previewToken" in html
    assert "invalidated();renderNodes()" in html


def test_browser_editor_never_claims_preview_or_save_grants_entitlement():
    html = effect_system_creator_page(_request()).body.decode("utf-8")
    assert "Saving never grants effect ownership" in html
    assert "server re-checks entitlements immediately before mutation" in html
    assert "Unsupported prompts fail closed" in html
    assert "no shell, process or device commands" in html
    assert "eval(" not in html
    assert ".innerHTML" not in html


def test_portal_route_is_in_same_deterministic_registration_contract_as_effect_system_api():
    registrations = effect_system_route_registrations("/command-center/api/universal-library")
    routes = {(path, method): endpoint for path, endpoint, method in registrations}
    assert ("/creative/effect-system-creator", "GET") in routes
    assert routes[("/creative/effect-system-creator", "GET")] is effect_system_creator_page
    assert ("/command-center/api/universal-library/effect-systems/compose", "POST") in routes
    assert ("/command-center/api/universal-library/effect-systems/projects/{project_name}/tracks/{track_id}/preview", "POST") in routes
    assert ("/command-center/api/universal-library/effect-systems/projects/{project_name}/tracks/{track_id}/apply", "POST") in routes


def test_portal_route_is_reachable_on_canonical_production_app():
    # Importing the installer must bind both JSON and HTML routes to the canonical FastAPI app.
    import aura_music_studio.universal_creative_catalogue_api  # noqa: F401
    from aura_music_studio.api import app as canonical_app
    from app import app as production_app

    canonical_paths = {getattr(route, "path", None) for route in canonical_app.routes}
    production_paths = {getattr(route, "path", None) for route in production_app.routes}
    assert "/creative/effect-system-creator" in canonical_paths
    assert "/creative/effect-system-creator" in production_paths
    assert "/command-center/api/universal-library/effect-systems/compose" in production_paths
