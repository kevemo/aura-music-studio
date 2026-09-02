from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256
from typing import Any


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
    """Make generated OpenAPI operation IDs unique without changing runtime dispatch.

    FastAPI normalises route paths when it generates ``unique_id`` values. Distinct, valid
    routes can therefore collide when aliases differ only by punctuation (for example a
    hyphenated path and an underscore compatibility path), even though their runtime path and
    method signatures are unambiguous. Generated clients require unique operation IDs.

    Preserve the first operation ID exactly. Only later collisions receive a deterministic
    suffix derived from their exact route signature and registration order. This changes schema
    metadata only; paths, methods, endpoints, dependencies and dispatch precedence are untouched.
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

        # FastAPI's OpenAPI generator prefers explicit operation_id over unique_id. Set both so
        # the final schema and any later introspection agree on the repaired identifier.
        try:
            route.operation_id = candidate
        except Exception:
            pass
        try:
            route.unique_id = candidate
        except Exception:
            pass

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


def deduplicate_http_routes(app: Any) -> list[dict[str, Any]]:
    """Remove unreachable exact duplicate HTTP routes and harden schema identity.

    Starlette/FastAPI dispatches routes in registration order, so when the same exact path and
    HTTP-method set is registered twice, every later copy is unreachable. Preserve the first
    authoritative route exactly as runtime dispatch already does and remove only later exact
    copies. Mounts, websocket routes, and different method sets are untouched.

    After runtime duplicates are removed, repair only OpenAPI operation-ID collisions among the
    remaining distinct routes. This is necessary because FastAPI's normalised ID generator can
    collapse punctuation-distinct compatibility paths to the same identifier.
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
    }
    return removed


__all__ = [
    "deduplicate_http_routes",
    "duplicate_http_signatures",
    "ensure_unique_operation_ids",
]
