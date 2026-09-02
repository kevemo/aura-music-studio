from __future__ import annotations

import warnings
from collections.abc import Iterable
from hashlib import sha256
from typing import Any


_SCHEMA_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def _http_signature(route: Any) -> tuple[str, tuple[str, ...]] | None:
    """Return an exact HTTP route signature or None for mounts/websockets/helpers."""
    path = getattr(route, "path", None)
    methods = getattr(route, "methods", None)
    if not isinstance(path, str) or not methods:
        return None
    normalized = tuple(sorted(str(method).upper() for method in methods))
    return path, normalized


def duplicate_http_signatures(routes: Iterable[Any]) -> dict[tuple[str, tuple[str, ...]], list[Any]]:
    grouped: dict[tuple[str, tuple[str, ...]], list[Any]] = {}
    for route in routes:
        signature = _http_signature(route)
        if signature is None:
            continue
        grouped.setdefault(signature, []).append(route)
    return {signature: items for signature, items in grouped.items() if len(items) > 1}


def _schema_operation_id(route: Any) -> str | None:
    if getattr(route, "include_in_schema", True) is False:
        return None
    value = getattr(route, "operation_id", None) or getattr(route, "unique_id", None)
    if value is None:
        return None
    clean = str(value).strip()
    return clean or None


def _operation_suffix(route: Any, *, ordinal: int) -> str:
    signature = _http_signature(route)
    if signature is None:
        source = f"route|{getattr(route, 'path', '')}|{ordinal}"
    else:
        source = f"{signature[0]}|{','.join(signature[1])}|{ordinal}"
    return sha256(source.encode("utf-8")).hexdigest()[:10]


def ensure_unique_operation_ids(routes: Iterable[Any]) -> list[dict[str, Any]]:
    """Repair route-level OpenAPI operation-ID collisions without changing dispatch.

    Preserve the first public identifier exactly. Only later route-level collisions receive a
    deterministic suffix. A separate schema pass handles the one case routes alone cannot model:
    a single APIRoute exposing multiple HTTP methods under one FastAPI ``unique_id``.
    """

    seen: set[str] = set()
    repaired: list[dict[str, Any]] = []

    for ordinal, route in enumerate(routes):
        operation_id = _schema_operation_id(route)
        if operation_id is None:
            continue
        if operation_id not in seen:
            seen.add(operation_id)
            continue

        candidate = f"{operation_id}_{_operation_suffix(route, ordinal=ordinal)}"
        bump = 1
        while candidate in seen:
            candidate = f"{operation_id}_{_operation_suffix(route, ordinal=ordinal)}_{bump}"
            bump += 1

        route.operation_id = candidate
        route.unique_id = candidate

        endpoint = getattr(route, "endpoint", None)
        signature = _http_signature(route)
        repaired.append(
            {
                "old_operation_id": operation_id,
                "new_operation_id": candidate,
                "path": signature[0] if signature else getattr(route, "path", None),
                "methods": list(signature[1]) if signature else [],
                "name": getattr(route, "name", None),
                "endpoint": getattr(endpoint, "__qualname__", None),
                "module": getattr(endpoint, "__module__", None),
            }
        )
        seen.add(candidate)

    return repaired


def _normalize_schema_operation_ids(schema: dict[str, Any]) -> list[dict[str, str]]:
    """Guarantee per-operation uniqueness in the emitted OpenAPI document.

    FastAPI uses one route-level ID for every method on an APIRoute. When a route intentionally
    accepts more than one method, OpenAPI therefore receives duplicate IDs even though there is no
    duplicate runtime route object. Keep the first emitted ID stable and suffix only later schema
    operations using their exact path and method.
    """

    seen: set[str] = set()
    repaired: list[dict[str, str]] = []
    paths = schema.get("paths")
    if not isinstance(paths, dict):
        return repaired

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in _SCHEMA_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            current = str(operation.get("operationId") or "").strip()
            if not current:
                digest = sha256(f"{path}|{method}".encode("utf-8")).hexdigest()[:12]
                current = f"operation_{digest}"
                operation["operationId"] = current
            if current not in seen:
                seen.add(current)
                continue

            digest = sha256(f"{path}|{method}".encode("utf-8")).hexdigest()[:12]
            candidate = f"{current}_{digest}"
            bump = 1
            while candidate in seen:
                candidate = f"{current}_{digest}_{bump}"
                bump += 1
            operation["operationId"] = candidate
            repaired.append(
                {
                    "old_operation_id": current,
                    "new_operation_id": candidate,
                    "path": path,
                    "method": method.upper(),
                }
            )
            seen.add(candidate)
    return repaired


def _install_openapi_integrity(app: Any) -> None:
    """Enforce collision-safe schema identity on every uncached canonical schema build."""
    if getattr(app.state, "route_integrity_openapi_installed", False):
        return

    original_openapi = app.openapi

    def integrity_openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema

        # Re-run route-level repair at schema time in case a compatibility installer added a
        # legitimate route after the initial composition pass.
        late_route_repairs = ensure_unique_operation_ids(app.router.routes)
        app.openapi_schema = None
        with warnings.catch_warnings():
            # FastAPI emits this warning before callers can inspect/repair its completed schema.
            # Suppress only this exact generator warning inside the canonical boundary; every
            # collision is then repaired and audited immediately below. Other warnings remain.
            warnings.filterwarnings(
                "ignore",
                message=r"^Duplicate Operation ID .*",
                category=UserWarning,
                module=r"fastapi\.openapi\.utils",
            )
            schema = original_openapi()

        schema_repairs = _normalize_schema_operation_ids(schema)
        app.openapi_schema = schema
        diagnostics = app.state.route_integrity
        if late_route_repairs:
            existing = diagnostics.setdefault("operation_id_repairs", [])
            existing.extend(late_route_repairs)
            diagnostics["operation_id_collisions_repaired"] = len(existing)
        diagnostics["schema_operation_ids_repaired"] = len(schema_repairs)
        diagnostics["schema_operation_id_repairs"] = schema_repairs
        return schema

    app.openapi = integrity_openapi
    app.state.route_integrity_openapi_installed = True


def deduplicate_http_routes(app: Any) -> list[dict[str, Any]]:
    """Remove unreachable exact duplicate HTTP routes and harden schema identity.

    Starlette/FastAPI dispatches routes in registration order, so when the same exact path and
    HTTP-method set is registered twice, every later copy is unreachable. Preserve the first
    authoritative route exactly as runtime dispatch already does and remove only later exact
    copies. Mounts, websocket routes, and different method sets are untouched.

    After runtime duplicates are removed, repair route-level schema collisions and install a
    canonical OpenAPI wrapper that also handles per-method collisions. Runtime paths, methods,
    endpoints, dependencies and dispatch precedence remain unchanged.
    """

    seen: set[tuple[str, tuple[str, ...]]] = set()
    kept: list[Any] = []
    removed: list[dict[str, Any]] = []

    for route in app.router.routes:
        signature = _http_signature(route)
        if signature is None or signature not in seen:
            kept.append(route)
            if signature is not None:
                seen.add(signature)
            continue

        endpoint = getattr(route, "endpoint", None)
        removed.append(
            {
                "path": signature[0],
                "methods": list(signature[1]),
                "name": getattr(route, "name", None),
                "endpoint": getattr(endpoint, "__qualname__", None),
                "module": getattr(endpoint, "__module__", None),
            }
        )

    if removed:
        app.router.routes[:] = kept

    operation_ids_repaired = ensure_unique_operation_ids(app.router.routes)
    app.openapi_schema = None
    app.state.route_integrity = {
        "duplicates_removed": len(removed),
        "removed": removed,
        "operation_id_collisions_repaired": len(operation_ids_repaired),
        "operation_id_repairs": operation_ids_repaired,
        "schema_operation_ids_repaired": 0,
        "schema_operation_id_repairs": [],
    }
    _install_openapi_integrity(app)
    return removed


__all__ = [
    "deduplicate_http_routes",
    "duplicate_http_signatures",
    "ensure_unique_operation_ids",
]
