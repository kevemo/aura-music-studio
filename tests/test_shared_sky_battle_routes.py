from app import app


def _mounted(path: str, method: str) -> bool:
    return any(
        getattr(route, "path", None) == path
        and method in (getattr(route, "methods", None) or set())
        for route in app.router.routes
    )


def test_chat6_battle_routes_are_mounted_on_canonical_production_app():
    assert _mounted("/shared-sky/api/broadcasts/{live_session_id}/participants/host", "POST")
    assert _mounted("/shared-sky/api/broadcasts/{live_session_id}/battles", "POST")
    assert _mounted("/shared-sky/api/battle-plans", "POST")
    assert _mounted("/shared-sky/api/battle-challenges", "POST")
    assert _mounted("/owner/shared-sky/api/battle-rulesets", "POST")


def test_chat6_does_not_publish_a_client_score_mutation_route():
    forbidden = {
        "/shared-sky/api/battles/{battle_id}/score",
        "/shared-sky/api/battles/{battle_id}/increment-score",
        "/shared-sky/api/battles/{battle_id}/gift-score",
    }
    mounted = {getattr(route, "path", "") for route in app.router.routes}
    assert not forbidden.intersection(mounted)
