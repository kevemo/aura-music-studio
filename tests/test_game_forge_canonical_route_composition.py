from __future__ import annotations

from fastapi.routing import APIRoute

from app import app


def _matching(path: str, method: str) -> list[APIRoute]:
    normalized = method.upper()
    return [
        route
        for route in app.router.routes
        if isinstance(route, APIRoute)
        and route.path == path
        and normalized in (route.methods or set())
    ]


def test_game_forge_project_routes_have_singular_canonical_dispatch_authority():
    expected = [
        ("GET", "/game-creation/visual-logic/{game_id}/{entity_id}"),
        ("GET", "/api/game-forge/projects/{project_name}/games"),
        ("GET", "/api/game-forge/games/{game_id}/visual-logic"),
        ("GET", "/api/game-forge/games/{game_id}/visual-logic/{entity_id}"),
        ("PUT", "/api/game-forge/games/{game_id}/visual-logic/{entity_id}"),
        ("DELETE", "/api/game-forge/games/{game_id}/visual-logic/{entity_id}"),
    ]
    for method, path in expected:
        matches = _matching(path, method)
        assert len(matches) == 1, f"expected one canonical {method} {path}, found {len(matches)}"


def test_game_forge_live_mutations_dispatch_through_transport_guard_once():
    expected = [
        ("PATCH", "/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/presentation", "guarded_transition_game_live_source"),
        ("POST", "/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/promote-version", "guarded_promote_game_live_version"),
        ("POST", "/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/emergency-hide", "guarded_emergency_hide_game_live_source"),
        ("DELETE", "/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}", "guarded_detach_game_live_source"),
    ]
    for method, path, endpoint_name in expected:
        matches = _matching(path, method)
        assert len(matches) == 1, f"expected one guarded {method} {path}, found {len(matches)}"
        route = matches[0]
        assert route.endpoint.__module__.endswith("game_forge_live_transport_guard")
        assert route.endpoint.__name__ == endpoint_name


def test_game_forge_route_composition_hook_ran_before_integrity_enforcement():
    diagnostics = app.state.route_integrity
    assert "game_forge_project_routes" in diagnostics["composition_hooks_run"]
