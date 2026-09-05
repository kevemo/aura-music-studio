from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


_REPO_ROOT = Path(__file__).resolve().parents[1]

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


def _fresh_production_snapshot() -> dict[str, object]:
    """Read routes from a clean canonical production-app boot.

    The repository's test suite imports and exercises shared application objects across thousands
    of tests and some tests legitimately change process working directories. Run the probe from
    the repository root so ``import app`` resolves the production ``app.py`` exactly as Uvicorn's
    documented ``uvicorn app:app`` entrypoint does, independent of surrounding test state.
    """
    script = r'''
import json
import app as production_app_module

application = production_app_module.app
routes = []
for route in application.router.routes:
    path = getattr(route, "path", None)
    methods = sorted(str(method).upper() for method in (getattr(route, "methods", None) or set()))
    if isinstance(path, str) and (
        path.startswith("/shared-sky/api/")
        or path.startswith("/owner/shared-sky/api/battle")
    ):
        routes.append({"path": path, "methods": methods})
print("CHAT6_ROUTE_SNAPSHOT=" + json.dumps({
    "module_file": getattr(production_app_module, "__file__", None),
    "routes": routes,
}, sort_keys=True))
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=_REPO_ROOT,
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
    snapshot = _fresh_production_snapshot()
    routes = list(snapshot.get("routes") or [])
    mounted = _route_pairs(routes)
    missing = sorted(_REQUIRED - mounted)
    assert not missing, (
        "Fresh canonical production app is missing Chat 6 routes: "
        f"{missing}; module_file={snapshot.get('module_file')!r}; routes={routes}"
    )


def test_chat6_does_not_publish_a_client_score_mutation_route():
    snapshot = _fresh_production_snapshot()
    routes = list(snapshot.get("routes") or [])
    mounted_paths = {str(route.get("path") or "") for route in routes}
    assert not _FORBIDDEN.intersection(mounted_paths)
