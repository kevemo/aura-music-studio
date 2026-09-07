from __future__ import annotations

import ast
from pathlib import Path

from fastapi.routing import APIRoute

from aura_music_studio import aura_sec_portal


ROOT = Path(__file__).resolve().parents[1]


def _production_tree() -> ast.Module:
    return ast.parse((ROOT / "app.py").read_text(encoding="utf-8"), filename="app.py")


def test_production_entrypoint_imports_and_mounts_aura_sec_exactly_once():
    tree = _production_tree()

    imports = [
        alias
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "aura_music_studio.aura_sec_portal"
        for alias in node.names
        if alias.name == "router" and alias.asname == "aura_sec_portal_router"
    ]
    assert len(imports) == 1

    mounts = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "include_router"
            and isinstance(func.value, ast.Name)
            and func.value.id == "app"
        ):
            continue
        first = node.args[0]
        if isinstance(first, ast.Name) and first.id == "aura_sec_portal_router":
            mounts.append(node)
    assert len(mounts) == 1


def test_aura_sec_router_exposes_only_member_safe_read_routes():
    routes = [route for route in aura_sec_portal.router.routes if isinstance(route, APIRoute)]
    by_path = {route.path: route for route in routes}

    assert set(by_path) == {"/aura-sec", "/aura-sec/overview"}
    assert by_path["/aura-sec"].methods == {"GET"}
    assert by_path["/aura-sec/overview"].methods == {"GET"}
    assert by_path["/aura-sec"].include_in_schema is False
    assert by_path["/aura-sec/overview"].include_in_schema is True


def test_aura_sec_browser_contract_contains_no_native_authority_route():
    paths = {
        route.path
        for route in aura_sec_portal.router.routes
        if isinstance(route, APIRoute)
    }
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


def test_security_center_truth_boundary_accepts_member_identity_only():
    source = aura_sec_portal._safe_control_plane_snapshot
    assert callable(source)
    assert source.__code__.co_argcount == 1
    assert source.__code__.co_varnames[0] == "user_id"
