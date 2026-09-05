from __future__ import annotations

import json
import subprocess
import sys


_REQUIRED = {
    ("/shared-sky/api/broadcasts/{live_session_id}/participants/host", "POST"),
    ("/shared-sky/api/broadcasts/{live_session_id}/battles", "POST"),
    ("/shared-sky/api/battle-plans", "POST"),
    ("/shared-sky/api/battle-challenges", "POST"),
    ("/owner/shared-sky/api/battle-rulesets", "POST"),
}

_FORBIDDEN = {
    "/shared-sky/api/battles/{battle_id}/score",
    "/shared-sky/api/battles/{battle_id}/increment-score",
    "/shared-sky/api/battles/{battle_id}/gift-score",
}


def _fresh_production_routes() -> list[dict[str, object]]:
    """Read routes from a clean canonical production-app boot.

    The repository's test suite imports and exercises the shared FastAPI singleton across
    thousands of tests. A subprocess mirrors a fresh Uvicorn worker and prevents unrelated
    test-process mutations from changing this production-reachability regression.
    """
    script = r'''
import json
from app import app

routes = []
for route in app.router.routes:
    path = getattr(route, "path", None)
    methods = sorted(str(method).upper() for method in (getattr(route, "methods", None) or set()))
    if isinstance(path, str) and (
        path.startswith("/shared-sky/api/")
        or path.startswith("/owner/shared-sky/api/battle")
    ):
        routes.append({"path": path, "methods": methods})
print("CHAT6_ROUTE_SNAPSHOT=" + json.dumps(routes, sort_keys=True))
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    prefix = "CHAT6_ROUTE_SNAPSHOT="
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(prefix):
            return json.loads(line[len(prefix) :])
    raise AssertionError(
        "Fresh production-app probe did not emit a Chat 6 route snapshot. "
        f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
    )


def _route_pairs(routes: list[dict[str, object]]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for route in routes:
        path = str(route.get("path") or "")
        methods = route.get("methods") or []
        for method in methods:
            pairs.add((path, str(method).upper()))
    return pairs


def test_chat6_battle_routes_are_mounted_on_fresh_canonical_production_app():
    routes = _fresh_production_routes()
    mounted = _route_pairs(routes)
    missing = sorted(_REQUIRED - mounted)
    assert not missing, f"Fresh canonical production app is missing Chat 6 routes: {missing}; routes={routes}"


def test_chat6_does_not_publish_a_client_score_mutation_route():
    routes = _fresh_production_routes()
    mounted_paths = {str(route.get("path") or "") for route in routes}
    assert not _FORBIDDEN.intersection(mounted_paths)
