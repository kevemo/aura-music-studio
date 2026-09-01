from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fastapi.routing import APIRoute

from aura_music_studio import aura_sec_portal


ROOT = Path(__file__).resolve().parents[1]


def _fresh_production_routes() -> list[dict]:
    """Inspect the release entrypoint in a clean interpreter, like Uvicorn does.

    The full suite imports the shared FastAPI singleton from many test modules during
    collection, so its in-process route table is not a reliable clean-boot assertion.
    """
    code = r'''
import json
import app as production_entrypoint
from fastapi.routing import APIRoute
routes = []
for route in production_entrypoint.app.routes:
    if isinstance(route, APIRoute):
        routes.append({
            "path": route.path,
            "methods": sorted(route.methods or []),
            "module": route.endpoint.__module__,
            "schema": bool(route.include_in_schema),
        })
print("AURA_SEC_ROUTE_SNAPSHOT=" + json.dumps(routes, separators=(",", ":")))
'''
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=45,
    )
    assert proc.returncode == 0, proc.stderr
    marker = "AURA_SEC_ROUTE_SNAPSHOT="
    line = next((item for item in reversed(proc.stdout.splitlines()) if item.startswith(marker)), None)
    assert line is not None, proc.stdout
    return json.loads(line[len(marker):])


def _fresh_route(path: str) -> dict:
    matches = [route for route in _fresh_production_routes() if route["path"] == path]
    assert len(matches) == 1, f"expected exactly one clean-production route for {path!r}"
    return matches[0]


def test_production_app_mounts_member_safe_aura_sec_security_center():
    html_route = _fresh_route("/aura-sec")
    overview_route = _fresh_route("/aura-sec/overview")

    assert html_route["methods"] == ["GET"]
    assert overview_route["methods"] == ["GET"]
    assert overview_route["schema"] is True
    assert overview_route["module"] == "aura_music_studio.aura_sec_portal"


def test_aura_sec_browser_surface_does_not_mount_native_authority_routes():
    routes = _fresh_production_routes()
    paths = {route["path"] for route in routes}
    forbidden = {
        "/aura-sec/native/poll",
        "/aura-sec/native/heartbeat",
        "/aura-sec/native/receipt",
        "/aura-sec/native/execute",
        "/aura-sec/sign",
        "/aura-sec/approve",
        "/aura-sec/actions/execute",
    }
    assert forbidden.isdisjoint(paths)

    aura_routes = [route for route in routes if route["path"].startswith("/aura-sec")]
    assert {route["path"] for route in aura_routes} == {"/aura-sec", "/aura-sec/overview"}
    for route in aura_routes:
        assert set(route["methods"]) <= {"GET", "HEAD"}


def test_aura_sec_router_contract_is_member_safe_before_production_mount():
    routes = [route for route in aura_sec_portal.router.routes if isinstance(route, APIRoute)]
    assert {route.path for route in routes} == {"/aura-sec", "/aura-sec/overview"}
    assert all((route.methods or set()) <= {"GET", "HEAD"} for route in routes)


def test_security_center_truth_boundary_is_explicit_in_source_contract():
    source = aura_sec_portal._safe_control_plane_snapshot
    assert callable(source)
    # The browser contract deliberately has no function accepting signatures, command
    # envelopes, native receipts, approval proof or execution payloads.
    assert source.__code__.co_argcount == 1
    assert source.__code__.co_varnames[0] == "user_id"
