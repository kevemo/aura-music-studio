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


def _route_snapshot(routes) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for route in routes:
        path = getattr(route, "path", None)
        if not isinstance(path, str):
            continue
        methods = sorted(str(method).upper() for method in (getattr(route, "methods", None) or set()))
        items.append({"path": path, "methods": methods})
    return items


def _fresh_production_snapshot() -> dict[str, object]:
    """Read routes from a clean canonical production-app boot.

    Run from the repository root so ``import app`` resolves the production entrypoint exactly as
    ``uvicorn app:app`` does. The diagnostic also records the source Battle router and the final
    route-integrity removals so a missing production route cannot be misclassified as test state.
    """
    script = r'''
import json
import app as production_app_module
from aura_music_studio.shared_sky_battle_api import router as battle_router

application = production_app_module.app

def snapshot(routes):
    items = []
    for route in routes:
        path = getattr(route, "path", None)
        if not isinstance(path, str):
            continue
        methods = sorted(str(method).upper() for method in (getattr(route, "methods", None) or set()))
        items.append({"path": path, "methods": methods})
    return items

all_app_routes = snapshot(application.router.routes)
battle_routes = snapshot(battle_router.routes)
selected = [item for item in all_app_routes if (
    item["path"].startswith("/shared-sky/api/")
    or item["path"].startswith("/owner/shared-sky/api/battle")
)]
related = [item for item in all_app_routes if "battle" in item["path"].lower() or "shared-sky" in item["path"].lower()]
integrity = getattr(application.state, "route_integrity", {})
removed = []
if isinstance(integrity, dict):
    removed = [item for item in integrity.get("removed", []) if (
        "battle" in str(item.get("path", "")).lower()
        or "shared-sky" in str(item.get("path", "")).lower()
    )]
print("CHAT6_ROUTE_SNAPSHOT=" + json.dumps({
    "module_file": getattr(production_app_module, "__file__", None),
    "app_route_count": len(all_app_routes),
    "battle_router_route_count": len(battle_routes),
    "battle_router_routes": battle_routes,
    "routes": selected,
    "related_app_routes": related,
    "integrity_removed": removed,
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


def _route_pair_counts(routes: list[dict[str, object]]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for route in routes:
        path = str(route.get("path") or "")
        for method in route.get("methods") or []:
            pair = (path, str(method).upper())
            counts[pair] = counts.get(pair, 0) + 1
    return counts


def test_chat6_battle_routes_are_mounted_on_fresh_canonical_production_app():
    snapshot = _fresh_production_snapshot()
    routes = list(snapshot.get("routes") or [])
    mounted = _route_pairs(routes)
    missing = sorted(_REQUIRED - mounted)
    assert not missing, (
        "Fresh canonical production app is missing Chat 6 routes: "
        f"{missing}; diagnostics={snapshot}"
    )


def test_chat6_required_routes_have_one_canonical_dispatch_authority():
    snapshot = _fresh_production_snapshot()
    counts = _route_pair_counts(list(snapshot.get("routes") or []))
    duplicates = {pair: counts.get(pair, 0) for pair in _REQUIRED if counts.get(pair, 0) != 1}
    assert not duplicates, (
        "Required Chat 6 routes must each have exactly one canonical dispatch authority: "
        f"{duplicates}; diagnostics={snapshot}"
    )


def test_chat6_does_not_publish_a_client_score_mutation_route():
    snapshot = _fresh_production_snapshot()
    routes = list(snapshot.get("routes") or [])
    mounted_paths = {str(route.get("path") or "") for route in routes}
    assert not _FORBIDDEN.intersection(mounted_paths)
