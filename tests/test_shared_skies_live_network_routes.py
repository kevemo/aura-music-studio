from fastapi import FastAPI

from aura_music_studio.shared_skies_live_network import (
    install_shared_skies_live_network,
    router,
)


def _route_signatures(app: FastAPI) -> list[tuple[str, frozenset[str]]]:
    return [
        (
            getattr(route, "path", ""),
            frozenset(getattr(route, "methods", set()) or set()),
        )
        for route in app.router.routes
    ]


def test_shared_skies_live_routes_mount_deterministically_and_idempotently() -> None:
    app = FastAPI()
    install_shared_skies_live_network(app)

    mounted = _route_signatures(app)
    expected = [
        (
            getattr(route, "path", ""),
            frozenset(getattr(route, "methods", set()) or set()),
        )
        for route in router.routes
    ]

    for signature in expected:
        assert signature in mounted
        assert mounted.count(signature) == 1

    before = list(mounted)
    install_shared_skies_live_network(app)
    assert _route_signatures(app) == before


def test_shared_skies_live_mount_preserves_private_host_mutation_methods() -> None:
    app = FastAPI()
    install_shared_skies_live_network(app)
    mounted = set(_route_signatures(app))

    assert ("/api/live/start", frozenset({"POST"})) in mounted
    assert ("/api/live/{session_id}/stop", frozenset({"POST"})) in mounted
    assert ("/api/live/{session_id}/gifts", frozenset({"POST"})) in mounted
    assert ("/api/live/{session_id}/chat-links", frozenset({"POST"})) in mounted
    assert ("/api/live-now", frozenset({"GET"})) in mounted
    assert ("/live-now", frozenset({"GET"})) in mounted
